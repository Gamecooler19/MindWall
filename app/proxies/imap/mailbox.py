"""Mailbox data adapter for the IMAP proxy.

Provides a clean, protocol-agnostic interface for loading mailbox data that
the IMAP command handlers need.  This layer sits between the IMAP server
and the existing view_service / quarantine_service layers.

Design:
  - All functions are async and accept an AsyncSession.
  - No IMAP protocol concerns exist here — only data retrieval.
  - The two exposed virtual mailboxes are INBOX and Mindwall/Quarantine.
  - UIDs exposed to IMAP clients are the mindwall_uid from MailboxItem; a
    sequential integer is assigned as fallback if mindwall_uid is null.
  - Raw message bytes are loaded from the RawMessageStore when available.
  - Lightweight envelope headers are synthesised from the Message ORM record
    when the raw file is unavailable (graceful degradation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mailboxes.sync_models import ItemVisibility, MailboxItem
from app.messages.models import Message

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The two virtual IMAP folders exposed by the proxy.
INBOX_FOLDER = "INBOX"
QUARANTINE_FOLDER = "Mindwall/Quarantine"

_VISIBLE_FOLDER_LIST = [INBOX_FOLDER, QUARANTINE_FOLDER]

# Visibilities that belong to each virtual folder.
_INBOX_VISIBILITIES = frozenset({ItemVisibility.VISIBLE})
_QUARANTINE_VISIBILITIES = frozenset({ItemVisibility.QUARANTINED, ItemVisibility.HIDDEN})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ImapMessage:
    """A minimal message record for the IMAP proxy to serve to clients.

    The uid is the stable mindwall_uid (or fallback sequential id) exposed
    in IMAP UID commands.
    """

    uid: int          # IMAP UID — the mindwall_uid value
    seq: int          # 1-based sequence number in the selected folder
    size: int         # RFC 822 message size in bytes
    flags: list[str]  # IMAP flags — always ["\\Seen"] for read-only MVP
    raw_bytes: bytes  # Raw .eml bytes (may be synthesised if unavailable)
    subject: str | None
    from_address: str | None
    date_str: str | None    # RFC 2822 date string for headers


@dataclass
class ImapMailbox:
    """A selected virtual mailbox state.

    Created by select_mailbox() and held in the connection state.
    """

    name: str
    uid_validity: int       # IMAP UIDVALIDITY for this virtual folder
    messages: list[ImapMessage]

    @property
    def exists(self) -> int:
        """Number of messages (IMAP EXISTS)."""
        return len(self.messages)

    @property
    def recent(self) -> int:
        """Messages with \\Recent flag — always 0 in read-only MVP."""
        return 0

    @property
    def uid_next(self) -> int:
        """Next UID that will be assigned (one past the maximum existing UID)."""
        if not self.messages:
            return 1
        return max(m.uid for m in self.messages) + 1

    def by_uid(self, uid: int) -> ImapMessage | None:
        """Look up a message by its IMAP UID."""
        for m in self.messages:
            if m.uid == uid:
                return m
        return None

    def by_seq(self, seq: int) -> ImapMessage | None:
        """Look up a message by its 1-based sequence number."""
        if 1 <= seq <= len(self.messages):
            return self.messages[seq - 1]
        return None


# ---------------------------------------------------------------------------
# Folder listing
# ---------------------------------------------------------------------------


def list_folders() -> list[tuple[str, str, str]]:
    """Return the list of virtual IMAP folders exposed by the proxy.

    Each tuple is (flags, delimiter, name) matching IMAP LIST response format.
    """
    return [
        (r'(\HasNoChildren)', "/", INBOX_FOLDER),
        (r'(\HasNoChildren)', "/", QUARANTINE_FOLDER),
    ]


# ---------------------------------------------------------------------------
# Mailbox selection
# ---------------------------------------------------------------------------


async def select_mailbox(
    db: AsyncSession,
    mailbox_profile_id: int,
    folder_name: str,
    raw_store_root: Path | None = None,
) -> ImapMailbox | None:
    """Load the virtual mailbox state for a folder selection.

    Returns None if the folder name is not recognised.

    Args:
        db:                    Active async session.
        mailbox_profile_id:    The authenticated mailbox profile.
        folder_name:           The IMAP folder name (case-insensitive comparison
                               against virtual folder names).
        raw_store_root:        Optional path to the RawMessageStore root.
                               Used to load raw .eml bytes for FETCH operations.
    """
    normalized = _normalize_folder(folder_name)
    if normalized is None:
        return None

    if normalized == INBOX_FOLDER:
        visibilities = list(_INBOX_VISIBILITIES)
        uid_validity = _folder_uid_validity(mailbox_profile_id, INBOX_FOLDER)
    else:
        visibilities = list(_QUARANTINE_VISIBILITIES)
        uid_validity = _folder_uid_validity(mailbox_profile_id, QUARANTINE_FOLDER)

    items = await _load_items(db, mailbox_profile_id, visibilities)
    messages = await _build_imap_messages(db, items, raw_store_root)

    return ImapMailbox(
        name=normalized,
        uid_validity=uid_validity,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_folder(name: str) -> str | None:
    """Return the canonical folder name or None if unrecognised."""
    upper = name.upper()
    if upper == "INBOX":
        return INBOX_FOLDER
    for folder in (QUARANTINE_FOLDER,):
        if upper == folder.upper():
            return folder
    return None


def _folder_uid_validity(mailbox_profile_id: int, folder_name: str) -> int:
    """Derive a stable UIDVALIDITY for a virtual folder.

    We use a fixed offset so the value is reproducible across proxy restarts.
    The UIDVALIDITY only needs to change if we reset the UID namespace.
    """
    # Simple stable hash based on mailbox id and folder — stays constant.
    base = 0x4D574C30  # "MWL0" in hex — Mindwall prefix
    if folder_name == INBOX_FOLDER:
        return (base + mailbox_profile_id * 2) & 0x7FFFFFFF
    return (base + mailbox_profile_id * 2 + 1) & 0x7FFFFFFF


async def _load_items(
    db: AsyncSession,
    mailbox_profile_id: int,
    visibilities: list[ItemVisibility],
) -> list[MailboxItem]:
    """Load MailboxItems matching the given visibilities, ordered by uid."""
    result = await db.execute(
        select(MailboxItem)
        .where(
            MailboxItem.mailbox_profile_id == mailbox_profile_id,
            MailboxItem.visibility.in_(visibilities),
        )
        .order_by(MailboxItem.mindwall_uid.asc().nulls_last(), MailboxItem.id.asc())
    )
    return list(result.scalars().all())


async def _build_imap_messages(
    db: AsyncSession,
    items: list[MailboxItem],
    raw_store_root: Path | None,
) -> list[ImapMessage]:
    """Convert MailboxItems into ImapMessage records.

    Loads the corresponding Message row for envelope headers and optionally
    reads raw .eml bytes from the store.
    """
    if not items:
        return []

    # Bulk-load associated Message rows
    message_ids = [i.message_id for i in items if i.message_id is not None]
    messages_by_id: dict[int, Message] = {}
    if message_ids:
        msg_result = await db.execute(
            select(Message).where(Message.id.in_(message_ids))
        )
        for msg in msg_result.scalars():
            messages_by_id[msg.id] = msg

    imap_messages: list[ImapMessage] = []
    seq = 1

    for item in items:
        uid = item.mindwall_uid if item.mindwall_uid is not None else item.id
        message = messages_by_id.get(item.message_id) if item.message_id else None

        raw_bytes = _load_raw_bytes(message, raw_store_root)

        subject = message.subject if message else None
        from_address = message.from_address if message else None
        date_str = _format_date(message)

        imap_messages.append(
            ImapMessage(
                uid=uid,
                seq=seq,
                size=len(raw_bytes),
                flags=[r"\Seen"],
                raw_bytes=raw_bytes,
                subject=subject,
                from_address=from_address,
                date_str=date_str,
            )
        )
        seq += 1

    return imap_messages


def _load_raw_bytes(message: Message | None, raw_store_root: Path | None) -> bytes:
    """Load raw .eml bytes from the store, or synthesise a minimal message."""
    if message is not None and raw_store_root is not None and message.raw_storage_path:
        try:
            full_path = raw_store_root / message.raw_storage_path
            if full_path.exists():
                return full_path.read_bytes()
        except OSError:
            log.warning(
                "imap_proxy.raw_read_failed",
                message_id=message.id,
                path=message.raw_storage_path,
            )

    # Synthesise a minimal RFC 5322 message from stored envelope fields.
    return _synthesise_message(message)


def _synthesise_message(message: Message | None) -> bytes:
    """Build a minimal RFC 5322 message from envelope metadata."""
    lines: list[str] = []
    if message:
        if message.from_address:
            lines.append(f"From: {message.from_address}")
        if message.subject:
            lines.append(f"Subject: {message.subject}")
        if message.date:
            lines.append(f"Date: {message.date.strftime('%a, %d %b %Y %H:%M:%S +0000')}")
        if message.message_id:
            lines.append(f"Message-ID: {message.message_id}")
        lines.append("")  # blank line separating headers from body
        body = message.text_plain or message.text_html_safe or ""
        lines.append(body[:4096])  # truncate for safety
    else:
        lines = ["Subject: [Unavailable]", "", "(Message data not available)"]

    return "\r\n".join(lines).encode("utf-8", errors="replace")


def _format_date(message: Message | None) -> str | None:
    """Return an RFC 2822 date string or None."""
    if message is None or message.date is None:
        return None
    return message.date.strftime("%a, %d %b %Y %H:%M:%S +0000")
