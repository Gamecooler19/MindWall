"""ORM models for outbound SMTP submissions.

One row per message submitted through the Mindwall SMTP proxy.  The model
captures enough metadata to:
  - identify which mailbox profile and user submitted the message,
  - record all RFC 5321 envelope information (MAIL FROM / RCPT TO),
  - store a reference to the raw .eml bytes on disk,
  - track delivery status and mode,
  - surface relay errors for admin visibility.

Design choices:
  - Raw bytes are stored on the filesystem (same two-level SHA-256 layout as
    the inbound RawMessageStore) so the DB row stays small.
  - recipients is stored as JSON text (comma-separated would be ambiguous for
    addresses that contain commas, though rare).
  - delivery_mode is a VARCHAR matching SmtpDeliveryMode enum values.
  - Enum columns use native_enum=False for SQLite test compatibility.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SmtpDeliveryMode(enum.StrEnum):
    """How the submitted message was (or will be) delivered."""

    CAPTURE = "capture"   # Stored locally; not relayed upstream.
    RELAY = "relay"       # Forwarded to the upstream SMTP server.


class SmtpDeliveryStatus(enum.StrEnum):
    """Outcome of the delivery attempt."""

    PENDING = "pending"       # Accepted but not yet delivered (relay queuing).
    CAPTURED = "captured"     # Stored successfully in capture mode.
    RELAYED = "relayed"       # Successfully relayed upstream.
    FAILED = "failed"         # Relay attempt failed; relay_error is set.


class OutboundMessage(Base):
    """One outbound message submitted via the Mindwall SMTP proxy.

    Created when DATA is accepted from an authenticated SMTP client.
    """

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Which registered mailbox profile this submission came from.
    mailbox_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("mailbox_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Proxy username used during AUTH (informational / audit trail).
    proxy_username: Mapped[str] = mapped_column(String(100), nullable=False)

    # RFC 5321 envelope sender (MAIL FROM address).
    envelope_from: Mapped[str] = mapped_column(String(998), nullable=False)

    # RFC 5321 envelope recipients (RCPT TO addresses), stored as JSON array.
    # e.g. '["alice@example.com", "bob@example.com"]'
    envelope_to_json: Mapped[str] = mapped_column(Text, nullable=False)

    # RFC 5322 Subject header extracted from the message (may be None).
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)

    # Size in bytes of the raw .eml DATA.
    raw_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # SHA-256 hex digest of the raw .eml DATA.
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Relative path within the outbound store root, e.g. "ab/abcdef….eml".
    # NULL if capture/write failed.
    raw_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Delivery mode and status.
    delivery_mode: Mapped[SmtpDeliveryMode] = mapped_column(
        SAEnum(SmtpDeliveryMode, name="smtp_delivery_mode", native_enum=False, length=10),
        nullable=False,
        default=SmtpDeliveryMode.CAPTURE,
    )
    delivery_status: Mapped[SmtpDeliveryStatus] = mapped_column(
        SAEnum(SmtpDeliveryStatus, name="smtp_delivery_status", native_enum=False, length=10),
        nullable=False,
        default=SmtpDeliveryStatus.PENDING,
    )

    # Safe summary of a relay error (never contains credentials).
    relay_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Wall-clock time at which DATA was accepted.
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<OutboundMessage id={self.id} mailbox={self.mailbox_profile_id} "
            f"from={self.envelope_from!r} mode={self.delivery_mode} "
            f"status={self.delivery_status}>"
        )
