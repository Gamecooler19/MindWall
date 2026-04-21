"""Mailbox virtualization service — computes the Mindwall-filtered mailbox view.

This is the business-logic layer the future IMAP proxy will query.  It does
not implement any protocol; it answers questions like:

  - "Which messages in INBOX should the mail client see?"
  - "Which messages are quarantined for this mailbox?"
  - "What is the MailboxItem + Message detail for item N?"

The visibility field on each MailboxItem is the authoritative gate:

  VISIBLE        — message passes through; shown in the virtual inbox.
  QUARANTINED    — message is risky; hidden from inbox, shown in Quarantine.
  HIDDEN         — soft-hold; hidden pending review.
  INGESTION_ERROR — failed to ingest; hidden.
  PENDING        — not yet analyzed; treated as hidden until verdict is in.

The virtualization layer does *not* re-derive visibility from the current
analysis state on every call — the visibility field is set at sync/analysis
time and only changes when an analyst takes an explicit quarantine action
(e.g., release) that the quarantine service propagates back.

Entry points:
  get_visible_inbox    — items visible in the Mindwall inbox for a mailbox.
  get_quarantine_inbox — quarantined items for a mailbox.
  get_pending_items    — items awaiting analysis.
  get_item_with_message — full detail for a single MailboxItem.
  update_item_visibility — update visibility after an external event (e.g., release).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analysis import service as analysis_service
from app.analysis.models import AnalysisRun
from app.mailboxes.sync_models import ItemVisibility, MailboxItem
from app.messages.models import Message
from app.quarantine.models import QuarantineItem

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class VirtualInboxItem:
    """A single item in the Mindwall-computed mailbox view.

    Bundles the MailboxItem, the local Message, and optionally the latest
    AnalysisRun and QuarantineItem so route handlers have everything they
    need without additional queries.
    """

    mailbox_item: MailboxItem
    message: Message | None
    analysis: AnalysisRun | None
    quarantine_item: QuarantineItem | None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def get_visible_inbox(
    db: AsyncSession,
    mailbox_profile_id: int,
    folder_name: str | None = None,
    limit: int = 100,
) -> list[VirtualInboxItem]:
    """Return the Mindwall-visible messages for a mailbox.

    Only VISIBLE items are returned; QUARANTINED / HIDDEN / ERROR items are
    excluded.  Results are ordered by upstream_uid descending (newest first).

    Args:
        db:                   Active async database session.
        mailbox_profile_id:   The mailbox to query.
        folder_name:          Filter to a specific IMAP folder, or None for all.
        limit:                Maximum number of items to return.
    """
    stmt = (
        select(MailboxItem)
        .where(
            MailboxItem.mailbox_profile_id == mailbox_profile_id,
            MailboxItem.visibility == ItemVisibility.VISIBLE,
        )
        .order_by(MailboxItem.upstream_uid.desc())
        .limit(limit)
    )
    if folder_name is not None:
        stmt = stmt.where(MailboxItem.folder_name == folder_name)

    result = await db.execute(stmt)
    items = list(result.scalars().all())
    return await _enrich(db, items)


async def get_quarantine_inbox(
    db: AsyncSession,
    mailbox_profile_id: int,
    folder_name: str | None = None,
    limit: int = 100,
) -> list[VirtualInboxItem]:
    """Return quarantined messages for a mailbox.

    Returns QUARANTINED and HIDDEN items, ordered by upstream_uid descending.
    """
    stmt = (
        select(MailboxItem)
        .where(
            MailboxItem.mailbox_profile_id == mailbox_profile_id,
            MailboxItem.visibility.in_(
                [ItemVisibility.QUARANTINED, ItemVisibility.HIDDEN]
            ),
        )
        .order_by(MailboxItem.upstream_uid.desc())
        .limit(limit)
    )
    if folder_name is not None:
        stmt = stmt.where(MailboxItem.folder_name == folder_name)

    result = await db.execute(stmt)
    items = list(result.scalars().all())
    return await _enrich(db, items)


async def get_pending_items(
    db: AsyncSession,
    mailbox_profile_id: int,
    limit: int = 50,
) -> list[VirtualInboxItem]:
    """Return items still awaiting analysis (visibility=PENDING)."""
    result = await db.execute(
        select(MailboxItem)
        .where(
            MailboxItem.mailbox_profile_id == mailbox_profile_id,
            MailboxItem.visibility == ItemVisibility.PENDING,
        )
        .order_by(MailboxItem.upstream_uid.asc())
        .limit(limit)
    )
    items = list(result.scalars().all())
    return await _enrich(db, items)


async def get_item_with_message(
    db: AsyncSession,
    item_id: int,
) -> VirtualInboxItem | None:
    """Load a single MailboxItem with its associated message and analysis.

    Returns None if the item does not exist.
    """
    result = await db.execute(
        select(MailboxItem).where(MailboxItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        return None
    enriched = await _enrich(db, [item])
    return enriched[0] if enriched else None


async def update_item_visibility(
    db: AsyncSession,
    item: MailboxItem,
    visibility: ItemVisibility,
) -> MailboxItem:
    """Update the visibility of a MailboxItem after an external event.

    This is called when a quarantine analyst releases or confirms a message,
    so the virtual inbox reflects the updated state.

    The caller must commit the transaction.
    """
    old = item.visibility
    item.visibility = visibility
    await db.flush()
    log.info(
        "mailbox_item.visibility_updated",
        item_id=item.id,
        old_visibility=old,
        new_visibility=visibility,
    )
    return item


async def get_mailbox_item_counts(
    db: AsyncSession,
    mailbox_profile_id: int,
) -> dict[str, int]:
    """Return per-visibility counts for a mailbox — useful for dashboard badges."""
    from sqlalchemy import func

    result = await db.execute(
        select(MailboxItem.visibility, func.count().label("cnt"))
        .where(MailboxItem.mailbox_profile_id == mailbox_profile_id)
        .group_by(MailboxItem.visibility)
    )
    rows = result.all()
    return {row.visibility: row.cnt for row in rows}


# ---------------------------------------------------------------------------
# Internal enrichment
# ---------------------------------------------------------------------------


async def _enrich(
    db: AsyncSession,
    items: list[MailboxItem],
) -> list[VirtualInboxItem]:
    """Load associated Message, AnalysisRun, and QuarantineItem for each item."""
    if not items:
        return []

    # Bulk-load messages
    message_ids = [i.message_id for i in items if i.message_id is not None]
    messages_by_id: dict[int, Message] = {}
    if message_ids:
        msg_result = await db.execute(
            select(Message)
            .where(Message.id.in_(message_ids))
            .options(selectinload(Message.urls), selectinload(Message.attachments))
        )
        for msg in msg_result.scalars():
            messages_by_id[msg.id] = msg

    # Bulk-load latest analysis for each message
    analysis_by_message: dict[int, AnalysisRun] = {}
    for mid in message_ids:
        run = await analysis_service.get_latest_analysis(db, mid)
        if run is not None:
            analysis_by_message[mid] = run

    # Bulk-load quarantine items
    quarantine_by_message: dict[int, QuarantineItem] = {}
    if message_ids:
        from app.quarantine.models import QuarantineItem as QuarantineItemAlias

        q_result = await db.execute(
            select(QuarantineItemAlias).where(QuarantineItemAlias.message_id.in_(message_ids))
        )
        for qi in q_result.scalars():
            quarantine_by_message[qi.message_id] = qi

    return [
        VirtualInboxItem(
            mailbox_item=item,
            message=messages_by_id.get(item.message_id) if item.message_id else None,
            analysis=analysis_by_message.get(item.message_id) if item.message_id else None,
            quarantine_item=(
                quarantine_by_message.get(item.message_id) if item.message_id else None
            ),
        )
        for item in items
    ]
