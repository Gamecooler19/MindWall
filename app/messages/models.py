"""SQLAlchemy ORM models for the messages domain.

Three tables:
  messages            — one row per ingested RFC 5322 message
  message_urls        — extracted URLs (one-to-many with messages)
  message_attachments — attachment metadata (one-to-many with messages)

Design choices:
  - Raw message bytes are stored on the filesystem, not in the DB.
  - Address lists (to/cc/bcc) are stored as JSON text for simplicity.
  - Enum columns use native_enum=False for SQLite test compatibility.
  - Auth headers are captured as raw strings for later deterministic analysis.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class IngestionSource(enum.StrEnum):
    """How a message arrived in the Mindwall system."""

    MESSAGE_LAB = "message_lab"
    IMAP_PROXY = "imap_proxy"
    SMTP_GATEWAY = "smtp_gateway"


class UrlSource(enum.StrEnum):
    """Whether a URL was extracted from plain-text or HTML body."""

    TEXT = "text"
    HTML = "html"


class Message(Base):
    """Canonical record for a single ingested RFC 5322 email message."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Optional link to the mailbox profile that received this message.
    # NULL for messages ingested via the admin Message Lab.
    mailbox_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("mailbox_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------ #
    # RFC 5322 identifiers
    # ------------------------------------------------------------------ #
    message_id: Mapped[str | None] = mapped_column(String(998), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(998), nullable=True)
    references: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------ #
    # Envelope metadata
    # ------------------------------------------------------------------ #
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    from_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # JSON-encoded list of email address strings
    to_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    cc_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    bcc_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Date from message headers, normalised to UTC
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ------------------------------------------------------------------ #
    # Body content
    # ------------------------------------------------------------------ #
    has_text_plain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_text_html: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text_plain: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Safe text extracted from HTML — scripts/styles/remote content stripped
    text_html_safe: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------ #
    # Raw message storage
    # ------------------------------------------------------------------ #
    raw_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Path relative to raw_message_store_path; None if storage was skipped
    raw_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ------------------------------------------------------------------ #
    # Authentication headers (captured for later deterministic analysis)
    # ------------------------------------------------------------------ #
    header_authentication_results: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_received_spf: Mapped[str | None] = mapped_column(String(500), nullable=True)
    header_dkim_signature_present: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    header_x_mailer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ------------------------------------------------------------------ #
    # Structural counters (denormalised for quick list-view display)
    # ------------------------------------------------------------------ #
    num_attachments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    num_urls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ------------------------------------------------------------------ #
    # Ingestion metadata
    # ------------------------------------------------------------------ #
    ingestion_source: Mapped[IngestionSource] = mapped_column(
        SAEnum(IngestionSource, name="ingestion_source", native_enum=False, length=30),
        nullable=False,
    )

    # ------------------------------------------------------------------ #
    # Relationships
    # ------------------------------------------------------------------ #
    urls: Mapped[list["MessageUrl"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageUrl.position",
    )
    attachments: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageAttachment.position",
    )


class MessageUrl(Base):
    """A URL extracted from a message body."""

    __tablename__ = "message_urls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    raw_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    scheme: Mapped[str | None] = mapped_column(String(20), nullable=True)
    host: Mapped[str | None] = mapped_column(String(253), nullable=True, index=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[UrlSource] = mapped_column(
        SAEnum(UrlSource, name="url_source", native_enum=False, length=10),
        nullable=False,
    )
    # Anchor text from HTML links; None for plain-text URLs
    link_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Ordinal position within the message (for stable ordering)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    message: Mapped["Message"] = relationship(back_populates="urls")


class MessageAttachment(Base):
    """Metadata for a single MIME attachment within a message."""

    __tablename__ = "message_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    message: Mapped["Message"] = relationship(back_populates="attachments")
