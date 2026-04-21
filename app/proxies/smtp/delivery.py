"""Outbound SMTP submission delivery service.

Handles the post-DATA path for the SMTP proxy:
  1. Write the raw message bytes to the outbound store.
  2. Persist an OutboundMessage record to the database.
  3. In relay mode: attempt upstream relay and update status accordingly.
  4. In capture mode: mark as CAPTURED immediately.

Design:
  - All heavy I/O is synchronous-compatible (no asyncio in the relay call)
    because smtplib is synchronous.  The async wrapper runs relay in a thread
    via asyncio.to_thread() to avoid blocking the event loop.
  - The function is transactional: the DB row is written first so that even
    if a relay attempt fails, there is an audit record of the submission.
  - relay_error is always a safe string; upstream credentials never appear.
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.proxies.smtp.models import (
    OutboundMessage,
    SmtpDeliveryMode,
    SmtpDeliveryStatus,
)
from app.proxies.smtp.relay import SmtpRelayError, relay_message
from app.proxies.smtp.session import SmtpProxySession

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Outbound raw message store
# ---------------------------------------------------------------------------


def _write_outbound_eml(store_root: Path, raw_bytes: bytes) -> tuple[str, str]:
    """Write raw .eml bytes to the outbound store.

    Returns:
        (sha256_hex, relative_path) — e.g. ("ab1234…", "ab/ab1234….eml")
    """
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    relative_path = f"{sha256[:2]}/{sha256}.eml"
    full_path = store_root / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if not full_path.exists():
        tmp = full_path.with_suffix(".tmp")
        tmp.write_bytes(raw_bytes)
        tmp.replace(full_path)
    return sha256, relative_path


def _extract_subject(raw_bytes: bytes) -> str | None:
    """Extract Subject header from raw .eml bytes; return None if absent."""
    try:
        msg = email.message_from_bytes(raw_bytes)
        subject = msg.get("Subject")
        if subject:
            return str(subject)[:998]
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Main delivery function
# ---------------------------------------------------------------------------


async def deliver_outbound(
    db: AsyncSession,
    session: SmtpProxySession,
    envelope_from: str,
    envelope_to: list[str],
    raw_message: bytes,
    store_root: Path,
    delivery_mode: str,
    *,
    # Optional relay dependencies — passed explicitly for testability.
    encryptor=None,         # CredentialEncryptor | None
    profile=None,           # MailboxProfile | None (pre-fetched for relay)
    relay_timeout: int = 30,
) -> OutboundMessage:
    """Accept a DATA payload and persist + deliver it.

    Args:
        db:              Active async DB session.
        session:         Authenticated proxy session context.
        envelope_from:   RFC 5321 MAIL FROM address.
        envelope_to:     List of RFC 5321 RCPT TO addresses.
        raw_message:     Raw DATA bytes (including all headers).
        store_root:      Root directory of the outbound message store.
        delivery_mode:   "capture" or "relay".
        encryptor:       Required if delivery_mode == "relay".
        profile:         Pre-fetched MailboxProfile; required if mode == "relay".
        relay_timeout:   Seconds to wait for upstream SMTP.

    Returns:
        The persisted OutboundMessage ORM record.
    """
    # 1. Write raw bytes to disk.
    sha256, rel_path = _write_outbound_eml(store_root, raw_message)

    # 2. Extract subject header.
    subject = _extract_subject(raw_message)

    # 3. Determine initial status.
    mode = SmtpDeliveryMode(delivery_mode)
    initial_status = SmtpDeliveryStatus.PENDING

    # 4. Persist the OutboundMessage record.
    outbound = OutboundMessage(
        mailbox_profile_id=session.mailbox_profile_id,
        proxy_username=session.proxy_username,
        envelope_from=envelope_from,
        envelope_to_json=json.dumps(envelope_to),
        subject=subject,
        raw_size_bytes=len(raw_message),
        raw_sha256=sha256,
        raw_storage_path=rel_path,
        delivery_mode=mode,
        delivery_status=initial_status,
        submitted_at=datetime.now(UTC),
    )
    db.add(outbound)
    await db.flush()  # Get the id without committing yet.

    # 5. Deliver.
    if mode == SmtpDeliveryMode.CAPTURE:
        outbound.delivery_status = SmtpDeliveryStatus.CAPTURED
        log.info(
            "smtp_delivery.captured",
            outbound_id=outbound.id,
            size_bytes=len(raw_message),
            recipients=len(envelope_to),
        )

    elif mode == SmtpDeliveryMode.RELAY:
        if profile is None or encryptor is None:
            log.error(
                "smtp_delivery.relay_misconfigured",
                outbound_id=outbound.id,
            )
            outbound.delivery_status = SmtpDeliveryStatus.FAILED
            outbound.relay_error = "Relay mode requested but profile/encryptor not provided."
        else:
            try:
                # Run synchronous smtplib relay in a thread to avoid blocking
                # the asyncio event loop.
                await asyncio.to_thread(
                    _relay_sync,
                    profile,
                    encryptor,
                    envelope_from,
                    envelope_to,
                    raw_message,
                    relay_timeout,
                )
                outbound.delivery_status = SmtpDeliveryStatus.RELAYED
                log.info(
                    "smtp_delivery.relayed",
                    outbound_id=outbound.id,
                    recipients=len(envelope_to),
                )
            except SmtpRelayError as exc:
                outbound.delivery_status = SmtpDeliveryStatus.FAILED
                outbound.relay_error = str(exc)[:500]
                log.warning(
                    "smtp_delivery.relay_failed",
                    outbound_id=outbound.id,
                    error=str(exc),
                )

    await db.commit()
    return outbound


def _relay_sync(profile, encryptor, envelope_from, envelope_to, raw_message, timeout):
    """Synchronous relay call — runs in a thread pool via asyncio.to_thread()."""
    import asyncio as _asyncio

    # relay_message is async but smtplib is sync — run the coroutine in a new event loop.
    loop = _asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            relay_message(
                profile=profile,
                encryptor=encryptor,
                envelope_from=envelope_from,
                envelope_to=envelope_to,
                raw_message=raw_message,
                timeout_seconds=timeout,
            )
        )
    finally:
        loop.close()
