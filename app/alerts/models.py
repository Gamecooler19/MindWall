"""SQLAlchemy ORM models for the alerts domain.

Alerts are created by the analysis pipeline when a message receives a
high-risk verdict.  Each alert links to a quarantine item and provides
an actionable triage workflow for admins.

State machine:
  OPEN → ACKNOWLEDGED → RESOLVED
  OPEN → RESOLVED (direct close without acknowledgement is allowed)

All enums use native_enum=False for SQLite test compatibility.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertSeverity(enum.StrEnum):
    """How urgently the alert needs attention."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(enum.StrEnum):
    """Lifecycle state of an alert."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Alert(Base):
    """A security alert raised when a high-risk message is detected.

    Each alert corresponds to a single quarantine event and provides the
    triage workflow for admin review.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Display fields
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    severity: Mapped[AlertSeverity] = mapped_column(
        SAEnum(AlertSeverity, native_enum=False),
        nullable=False,
        index=True,
    )
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus, native_enum=False),
        nullable=False,
        default=AlertStatus.OPEN,
        index=True,
    )

    # What triggered this alert (e.g. "quarantine.created", "verdict.escalate")
    trigger_action: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Optional links to other domain objects — nullable because alerts may
    # be raised for system events (not only per-message events).
    quarantine_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("quarantine_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Timestamps
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    acknowledged_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Who acted on it
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Admin notes
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
