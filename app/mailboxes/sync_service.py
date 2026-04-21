"""Mailbox sync service — orchestrates upstream IMAP sync into Mindwall.

Entry points:
  sync_mailbox_folder   — authenticate, fetch new UIDs, ingest, analyze,
                          quarantine, record mappings, update checkpoint.
  get_sync_state        — load or initialize the MailboxSyncState for a folder.
  get_sync_states_for_mailbox — load all sync states for a mailbox.

Sync is idempotent: if a (mailbox_profile_id, folder_name, upstream_uid) row
already exists in mailbox_items, the message is skipped.  The local Message
record is also deduplicated by raw SHA-256 inside ingest_raw_message.

Error handling:
  - Upstream authentication / connection failures abort the sync and record
    the error in MailboxSyncState with status=error.
  - Per-message failures (malformed .eml, analysis crash) are caught, logged,
    and recorded on the MailboxItem row.  The sync continues to the next UID.
  - Partial sync failures set status=partial.

This service has no protocol-server concerns.  It is called by:
  - Admin route handlers (on-demand sync from the UI)
  - Future background worker jobs
  - Future IMAP proxy session handlers (for pull-on-access patterns)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import service as analysis_service
from app.mailboxes.models import MailboxProfile
from app.mailboxes.sync_models import ItemVisibility, MailboxItem, MailboxSyncState, SyncStatus
from app.messages import service as msg_service
from app.messages.models import IngestionSource
from app.messages.storage import RawMessageStore
from app.policies.constants import Verdict
from app.proxies.imap.client import UpstreamImapClient, UpstreamImapError
from app.quarantine import service as quarantine_service
from app.security.crypto import CredentialEncryptor

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Summary of a completed (or aborted) sync run."""

    mailbox_profile_id: int
    folder_name: str
    new_messages: int = 0
    skipped: int = 0
    errors: int = 0
    quarantined: int = 0
    failed_auth: bool = False
    failed_connection: bool = False
    error_summary: str | None = None
    per_message_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sync state helpers
# ---------------------------------------------------------------------------


async def get_sync_state(
    db: AsyncSession,
    mailbox_profile_id: int,
    folder_name: str,
) -> MailboxSyncState:
    """Return the MailboxSyncState for a folder, creating a new row if absent."""
    result = await db.execute(
        select(MailboxSyncState).where(
            MailboxSyncState.mailbox_profile_id == mailbox_profile_id,
            MailboxSyncState.folder_name == folder_name,
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = MailboxSyncState(
            mailbox_profile_id=mailbox_profile_id,
            folder_name=folder_name,
            sync_status=SyncStatus.IDLE,
        )
        db.add(state)
        await db.flush()
    return state


async def get_sync_states_for_mailbox(
    db: AsyncSession,
    mailbox_profile_id: int,
) -> list[MailboxSyncState]:
    """Return all MailboxSyncState rows for a given mailbox profile."""
    result = await db.execute(
        select(MailboxSyncState)
        .where(MailboxSyncState.mailbox_profile_id == mailbox_profile_id)
        .order_by(MailboxSyncState.folder_name)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# UID mapping helpers
# ---------------------------------------------------------------------------


async def _uid_already_mapped(
    db: AsyncSession,
    mailbox_profile_id: int,
    folder_name: str,
    upstream_uid: int,
) -> bool:
    """Return True if a MailboxItem already exists for this UID."""
    result = await db.execute(
        select(MailboxItem.id).where(
            MailboxItem.mailbox_profile_id == mailbox_profile_id,
            MailboxItem.folder_name == folder_name,
            MailboxItem.upstream_uid == upstream_uid,
        )
    )
    return result.scalar_one_or_none() is not None


async def _allocate_mindwall_uid(
    db: AsyncSession,
    mailbox_profile_id: int,
) -> int:
    """Allocate the next sequential Mindwall UID for a mailbox.

    The Mindwall UID is a monotonically increasing integer assigned once to
    each MailboxItem.  It forms the stable virtual UID namespace that the
    future IMAP proxy will present to mail clients.
    """
    from sqlalchemy import func

    result = await db.execute(
        select(func.max(MailboxItem.mindwall_uid)).where(
            MailboxItem.mailbox_profile_id == mailbox_profile_id,
        )
    )
    current_max = result.scalar_one_or_none() or 0
    return current_max + 1


# ---------------------------------------------------------------------------
# Main sync orchestrator
# ---------------------------------------------------------------------------


async def sync_mailbox_folder(
    db: AsyncSession,
    profile: MailboxProfile,
    folder_name: str,
    encryptor: CredentialEncryptor,
    store: RawMessageStore,
    *,
    batch_size: int = 50,
    imap_timeout: int = 30,
    llm_enabled: bool = True,
    quarantine_soft_hold: bool = False,
    actor_user_id: int | None = None,
) -> SyncResult:
    """Sync new messages from an upstream IMAP folder into Mindwall.

    Steps:
    1.  Load or initialize sync state for (profile, folder_name).
    2.  Decrypt IMAP credentials.
    3.  Connect and authenticate to the upstream server.
    4.  SELECT the folder; compare UIDVALIDITY.
    5.  Fetch UIDs > last_seen_uid (up to batch_size).
    6.  For each new UID:
        a.  Skip if already mapped (idempotent).
        b.  Fetch raw message bytes.
        c.  Ingest through the standard message pipeline.
        d.  Run analysis + auto-quarantine.
        e.  Record MailboxItem with visibility outcome.
        f.  Update last_seen_uid checkpoint.
    7.  Persist sync state with updated checkpoint and status.
    8.  Return SyncResult summary.

    Args:
        db:                 Active async session.
        profile:            Authenticated MailboxProfile to sync.
        folder_name:        IMAP folder name (e.g. ``"INBOX"``).
        encryptor:          Credential encryptor for decrypting IMAP password.
        store:              RawMessageStore for persisting raw .eml files.
        batch_size:         Maximum number of new UIDs to fetch per run.
        imap_timeout:       IMAP connection and operation timeout in seconds.
        llm_enabled:        Whether to enable LLM analysis during sync.
        quarantine_soft_hold: Quarantine SOFT_HOLD verdicts as well.
        actor_user_id:      User ID for audit events (None for automated sync).

    Returns:
        SyncResult with counts and error summary.
    """
    result = SyncResult(
        mailbox_profile_id=profile.id,
        folder_name=folder_name,
    )

    # ------------------------------------------------------------------
    # 1. Load sync state
    # ------------------------------------------------------------------
    state = await get_sync_state(db, profile.id, folder_name)
    state.sync_status = SyncStatus.SYNCING
    state.last_sync_at = datetime.now(UTC)  # type: ignore[assignment]
    await db.flush()

    # ------------------------------------------------------------------
    # 2. Decrypt credentials
    # ------------------------------------------------------------------
    try:
        imap_password = encryptor.decrypt(profile.imap_password_enc)
    except Exception as exc:
        _abort_sync(
            state,
            result,
            error="Failed to decrypt IMAP credentials.",
            is_auth_failure=True,
        )
        await db.flush()
        await db.commit()
        log.error("sync.decrypt_failed", mailbox_id=profile.id, exc_type=type(exc).__name__)
        return result

    # ------------------------------------------------------------------
    # 3. Connect and authenticate
    # ------------------------------------------------------------------
    try:
        client = UpstreamImapClient(
            host=profile.imap_host,
            port=profile.imap_port,
            security=profile.imap_security,
            username=profile.imap_username,
            password=imap_password,
            timeout=imap_timeout,
        )
        await client.connect()
    except UpstreamImapError as exc:
        is_auth = "authentication" in str(exc).lower()
        _abort_sync(state, result, error=str(exc), is_auth_failure=is_auth)
        await db.flush()
        await db.commit()
        log.warning(
            "sync.connect_failed",
            mailbox_id=profile.id,
            error=str(exc),
        )
        return result
    finally:
        imap_password = ""  # Zero out in-memory plaintext

    # ------------------------------------------------------------------
    # 4. Select folder and check UIDVALIDITY
    # ------------------------------------------------------------------
    try:
        uid_validity, all_uids = await client.select_folder(folder_name)
    except UpstreamImapError as exc:
        await client.logout()
        _abort_sync(state, result, error=str(exc))
        await db.flush()
        await db.commit()
        return result

    # Detect UIDVALIDITY reset — if it changed, our uid mapping is stale.
    # Simple recovery: reset last_seen_uid so we re-fetch from the beginning.
    if state.uid_validity is not None and state.uid_validity != uid_validity:
        log.warning(
            "sync.uid_validity_changed",
            mailbox_id=profile.id,
            old=state.uid_validity,
            new=uid_validity,
        )
        state.last_seen_uid = None  # Force full re-sync

    state.uid_validity = uid_validity

    # ------------------------------------------------------------------
    # 5. Determine new UIDs to fetch
    # ------------------------------------------------------------------
    last_seen = state.last_seen_uid or 0
    new_uids: list[int]
    if last_seen == 0:
        new_uids = all_uids[-batch_size:]
    else:
        try:
            new_uids = await client.fetch_uids_in_range(last_seen + 1)
        except UpstreamImapError as exc:
            await client.logout()
            _abort_sync(state, result, error=str(exc))
            await db.flush()
            await db.commit()
            return result
        new_uids = new_uids[:batch_size]

    log.info(
        "sync.start",
        mailbox_id=profile.id,
        folder=folder_name,
        new_uid_count=len(new_uids),
        last_seen_uid=last_seen,
    )

    # ------------------------------------------------------------------
    # 6. Process each new UID
    # ------------------------------------------------------------------
    highest_uid_synced = last_seen

    for uid in new_uids:
        try:
            already = await _uid_already_mapped(db, profile.id, folder_name, uid)
            if already:
                result.skipped += 1
                highest_uid_synced = max(highest_uid_synced, uid)
                continue

            # Fetch raw message
            try:
                raw_bytes = await client.fetch_raw_message(uid)
            except UpstreamImapError as exc:
                await _record_failed_item(
                    db,
                    mailbox_profile_id=profile.id,
                    folder_name=folder_name,
                    upstream_uid=uid,
                    uid_validity=uid_validity,
                    error=str(exc),
                )
                result.errors += 1
                result.per_message_errors.append(f"UID {uid}: {exc}")
                continue

            # Ingest + persist
            try:
                message = await msg_service.ingest_raw_message(
                    db=db,
                    raw_bytes=raw_bytes,
                    source=IngestionSource.IMAP_SYNC,
                    store=store,
                    mailbox_profile_id=profile.id,
                )
                await db.flush()
            except Exception as exc:
                await _record_failed_item(
                    db,
                    mailbox_profile_id=profile.id,
                    folder_name=folder_name,
                    upstream_uid=uid,
                    uid_validity=uid_validity,
                    error=f"Ingestion failed: {type(exc).__name__}",
                )
                result.errors += 1
                result.per_message_errors.append(f"UID {uid}: ingestion error")
                log.warning(
                    "sync.ingestion_failed",
                    mailbox_id=profile.id,
                    uid=uid,
                    exc_type=type(exc).__name__,
                )
                continue

            # Analysis + auto-quarantine
            verdict: str = Verdict.ALLOW
            try:
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=message,
                    ollama_client=None,  # will use get_settings() inside
                    llm_enabled=llm_enabled,
                    quarantine_soft_hold=quarantine_soft_hold,
                    actor_user_id=actor_user_id,
                )
                verdict = run.verdict
            except Exception as exc:
                log.warning(
                    "sync.analysis_failed",
                    mailbox_id=profile.id,
                    message_id=message.id,
                    uid=uid,
                    exc_type=type(exc).__name__,
                )
                verdict = Verdict.ALLOW

            # Determine visibility
            visibility = _verdict_to_visibility(verdict, quarantine_soft_hold)
            if visibility == ItemVisibility.QUARANTINED:
                result.quarantined += 1

            # Allocate Mindwall UID
            mindwall_uid = await _allocate_mindwall_uid(db, profile.id)

            # Record the mapping
            item = MailboxItem(
                mailbox_profile_id=profile.id,
                folder_name=folder_name,
                upstream_uid=uid,
                uid_validity=uid_validity,
                message_id=message.id,
                rfc_message_id=message.message_id,
                visibility=visibility,
                mindwall_uid=mindwall_uid,
            )
            db.add(item)
            await db.flush()

            result.new_messages += 1
            highest_uid_synced = max(highest_uid_synced, uid)

            log.debug(
                "sync.message_synced",
                mailbox_id=profile.id,
                uid=uid,
                message_id=message.id,
                verdict=verdict,
                visibility=visibility.value,
            )

        except Exception as exc:
            # Catch-all to ensure one bad message cannot abort the whole sync.
            result.errors += 1
            result.per_message_errors.append(f"UID {uid}: unexpected error")
            log.error(
                "sync.unexpected_error",
                mailbox_id=profile.id,
                uid=uid,
                exc_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------
    await client.logout()

    # ------------------------------------------------------------------
    # 7. Update sync state checkpoint
    # ------------------------------------------------------------------
    if highest_uid_synced > last_seen:
        state.last_seen_uid = highest_uid_synced
    state.last_sync_count = result.new_messages

    if result.errors > 0 and result.new_messages == 0:
        state.sync_status = SyncStatus.ERROR
        state.last_error = "; ".join(result.per_message_errors[:3])
    elif result.errors > 0:
        state.sync_status = SyncStatus.PARTIAL
        state.last_error = f"{result.errors} message(s) failed"
    else:
        state.sync_status = SyncStatus.IDLE
        state.last_error = None
        state.last_successful_sync_at = datetime.now(UTC)  # type: ignore[assignment]

    await db.flush()
    await db.commit()

    log.info(
        "sync.complete",
        mailbox_id=profile.id,
        folder=folder_name,
        new_messages=result.new_messages,
        skipped=result.skipped,
        errors=result.errors,
        quarantined=result.quarantined,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verdict_to_visibility(
    verdict: str,
    quarantine_soft_hold: bool,
) -> ItemVisibility:
    """Map a policy verdict to a Mindwall inbox visibility value."""
    if quarantine_service.should_quarantine(verdict, quarantine_soft_hold):
        return ItemVisibility.QUARANTINED
    if verdict == Verdict.SOFT_HOLD:
        return ItemVisibility.HIDDEN
    return ItemVisibility.VISIBLE


def _abort_sync(
    state: MailboxSyncState,
    result: SyncResult,
    error: str,
    is_auth_failure: bool = False,
) -> None:
    """Record a fatal sync failure on the state and result objects."""
    state.sync_status = SyncStatus.ERROR
    state.last_error = error[:500]
    result.error_summary = error
    if is_auth_failure:
        result.failed_auth = True
    else:
        result.failed_connection = True


async def _record_failed_item(
    db: AsyncSession,
    *,
    mailbox_profile_id: int,
    folder_name: str,
    upstream_uid: int,
    uid_validity: int | None,
    error: str,
) -> None:
    """Persist a MailboxItem row for a UID that failed ingestion."""
    mindwall_uid = await _allocate_mindwall_uid(db, mailbox_profile_id)
    item = MailboxItem(
        mailbox_profile_id=mailbox_profile_id,
        folder_name=folder_name,
        upstream_uid=upstream_uid,
        uid_validity=uid_validity,
        message_id=None,
        visibility=ItemVisibility.INGESTION_ERROR,
        mindwall_uid=mindwall_uid,
        ingestion_error=error[:1000],
    )
    db.add(item)
    await db.flush()
