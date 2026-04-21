"""ORM models for IMAP sync state and upstream message mapping.

Two tables:

  mailbox_sync_states — per-folder checkpoint: last synced UID, UIDVALIDITY,
    sync status, and any error summary.

  mailbox_items — one row per upstream UID in a folder.  Each row maps an
    upstream (folder, UID) pair to a local Message record and tracks the
    Mindwall visibility state that the future IMAP proxy will use.

Design choices:
  - UIDVALIDITY is stored alongside each UID so that a mailbox reset can be
    detected and handled without losing history.
  - mindwall_uid provides a stable virtual UID namespace for the future IMAP
    proxy — allocated once per item and never reused.
  - ingestion_error captures a safe summary when ingestion fails so the sync
    does not silently discard problem messages.
"""

import enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncStatus(enum.StrEnum):
    """Coarse sync lifecycle status stored in MailboxSyncState."""

    IDLE = "idle"               # No sync running; last sync succeeded or never ran.
    SYNCING = "syncing"         # Sync is actively in progress.
    ERROR = "error"             # Last sync ended with a fatal error.
    PARTIAL = "partial"         # Sync ran but some messages failed individually.


class ItemVisibility(enum.StrEnum):
    """Mindwall-controlled visibility of a message in the virtual inbox.

    The future IMAP proxy uses this to decide whether to expose a message
    to the mail client or hide it behind the virtual Quarantine folder.
    """

    VISIBLE = "visible"             # Allowed through; shown normally.
    QUARANTINED = "quarantined"     # Risky; hidden from inbox and in Quarantine.
    HIDDEN = "hidden"               # Explicitly held; not yet quarantine-confirmed.
    INGESTION_ERROR = "ingestion_error"  # Ingestion or analysis failed.
    PENDING = "pending"             # Fetched but not yet analyzed.


class MailboxSyncState(Base):
    """Per-folder sync checkpoint for a registered mailbox profile.

    One row exists per (mailbox_profile_id, folder_name) pair.
    It is upserted after each sync attempt.
    """

    __tablename__ = "mailbox_sync_states"

    __table_args__ = (
        UniqueConstraint(
            "mailbox_profile_id",
            "folder_name",
            name="uq_sync_state_mailbox_folder",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("mailbox_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Which folder this checkpoint covers.
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # IMAP UIDVALIDITY — if this changes the UID sequence has been reset
    # and we must re-sync from scratch.
    uid_validity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The highest upstream UID successfully mapped in this folder.
    # UIDs greater than this are fetched on the next sync run.
    last_seen_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Wall-clock time of the most recently completed sync attempt.
    last_sync_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Wall-clock time of the most recently *successful* sync.
    last_successful_sync_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sync_status: Mapped[SyncStatus] = mapped_column(
        SAEnum(SyncStatus, name="sync_status", native_enum=False, length=20),
        nullable=False,
        default=SyncStatus.IDLE,
    )

    # Safe summary of the last error; never contains credentials or full traces.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Number of messages synced in the last run (informational).
    last_sync_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MailboxSyncState mailbox={self.mailbox_profile_id} "
            f"folder={self.folder_name!r} uid={self.last_seen_uid} "
            f"status={self.sync_status}>"
        )


class MailboxItem(Base):
    """Maps one upstream IMAP UID to a local Mindwall message.

    One row per (mailbox_profile_id, folder_name, upstream_uid) triplet.
    The unique constraint prevents duplicate mapping on repeated syncs.
    """

    __tablename__ = "mailbox_items"

    __table_args__ = (
        UniqueConstraint(
            "mailbox_profile_id",
            "folder_name",
            "upstream_uid",
            name="uq_mailbox_item_uid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("mailbox_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Which folder this item came from.
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Upstream IMAP UID and the UIDVALIDITY value at the time of mapping.
    upstream_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    uid_validity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Local message record — nullable because ingestion may fail.
    message_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # RFC 5322 Message-ID header value (stored separately for look-up even if
    # the local message record failed to persist).
    rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)

    # ------------------------------------------------------------------
    # Mindwall-controlled visibility (used by virtualization + proxy layer)
    # ------------------------------------------------------------------
    visibility: Mapped[ItemVisibility] = mapped_column(
        SAEnum(ItemVisibility, name="item_visibility", native_enum=False, length=20),
        nullable=False,
        default=ItemVisibility.PENDING,
    )

    # Pre-allocated virtual UID for the future IMAP proxy's UID namespace.
    # Set once and never changed, so proxy clients can rely on it.
    mindwall_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Safe error summary if ingestion or analysis failed for this item.
    ingestion_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MailboxItem mailbox={self.mailbox_profile_id} "
            f"folder={self.folder_name!r} uid={self.upstream_uid} "
            f"msg={self.message_id} vis={self.visibility}>"
        )
