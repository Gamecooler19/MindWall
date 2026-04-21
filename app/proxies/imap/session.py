"""IMAP proxy authentication and session management.

Authenticates incoming proxy connections using Mindwall-issued proxy credentials
(proxy_username + proxy_password).  On success, returns a ProxySession that
the protocol handler uses for all subsequent mailbox operations.

Security principles:
  - Proxy credentials are checked against bcrypt hashes (never plaintext).
  - A constant-time dummy verify runs when the username is not found to
    prevent username enumeration via timing.
  - Upstream credentials are never touched during proxy authentication.
  - The ProxySession holds only the profile ID and owner context; it does not
    cache decrypted credentials.
  - Failed auth attempts are logged at WARNING level without echoing input.
"""

from __future__ import annotations

from dataclasses import dataclass

import bcrypt
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mailboxes.models import MailboxProfile, MailboxStatus

log = structlog.get_logger(__name__)

# Constant-time dummy hash used when a proxy username is not found.
_DUMMY_HASH: bytes = bcrypt.hashpw(b"mindwall-proxy-dummy-verify-no-account", bcrypt.gensalt())


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class ProxySession:
    """Authenticated IMAP proxy session context.

    Created by ``authenticate_proxy_credentials`` on successful login.
    The protocol handler stores this for the lifetime of the connection.
    """

    mailbox_profile_id: int
    owner_user_id: int
    proxy_username: str
    email_address: str


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def authenticate_proxy_credentials(
    db: AsyncSession,
    proxy_username: str,
    proxy_password: str,
) -> ProxySession | None:
    """Verify Mindwall proxy credentials and return a ProxySession on success.

    Returns None if the username does not exist, the password is wrong,
    or the mailbox profile is inactive.

    Args:
        db:              Active async session (read-only in this call).
        proxy_username:  Username from the IMAP LOGIN command.
        proxy_password:  Plaintext password from the IMAP LOGIN command.

    Returns:
        ProxySession on success, None on any failure.
    """
    result = await db.execute(
        select(MailboxProfile).where(MailboxProfile.proxy_username == proxy_username)
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        # Constant-time dummy verify to prevent username enumeration.
        bcrypt.checkpw(b"mindwall-proxy-dummy-verify-no-account", _DUMMY_HASH)
        log.warning("imap_proxy.auth_failed", reason="username_not_found")
        return None

    if profile.proxy_password_hash is None:
        # Proxy credentials were never fully provisioned — treat as inactive.
        log.warning(
            "imap_proxy.auth_failed",
            reason="no_proxy_credentials",
            mailbox_id=profile.id,
        )
        return None

    # Verify the plaintext password against the stored bcrypt hash.
    try:
        password_ok = bcrypt.checkpw(
            proxy_password.encode(),
            profile.proxy_password_hash.encode(),
        )
    except Exception:
        log.warning(
            "imap_proxy.auth_failed",
            reason="bcrypt_error",
            mailbox_id=profile.id,
        )
        return None

    if not password_ok:
        log.warning(
            "imap_proxy.auth_failed",
            reason="bad_password",
            mailbox_id=profile.id,
        )
        return None

    if profile.status not in (MailboxStatus.ACTIVE, MailboxStatus.PENDING):
        log.warning(
            "imap_proxy.auth_failed",
            reason="mailbox_inactive",
            mailbox_id=profile.id,
            status=profile.status,
        )
        return None

    log.info(
        "imap_proxy.auth_success",
        mailbox_id=profile.id,
        owner_id=profile.owner_id,
    )

    return ProxySession(
        mailbox_profile_id=profile.id,
        owner_user_id=profile.owner_id,
        proxy_username=proxy_username,
        email_address=profile.email_address,
    )
