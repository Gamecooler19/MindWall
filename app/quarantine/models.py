"""SQLAlchemy ORM models for the quarantine domain.

Two tables:
  quarantine_items — one per quarantined message (idempotent per message)
  audit_events     — append-only record of every state change and action

Design choices:
  - One QuarantineItem per message. Re-analysis updates the existing item rather
    than creating a duplicate, preserving the review timeline.
  - AuditEvent rows are never updated or deleted — they are an append-only log.
  - All enums use native_enum=False for SQLite test compatibility.
  - reviewed_at / reviewed_by_user_id are nullable until a review action is taken.
  - notes is a freeform analyst annotation that accumulates over time via audit events.
"""

import enum

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class QuarantineStatus(enum.StrEnum):
    """Lifecycle state of a quarantine item."""

    PENDING_REVIEW = "pending_review"
    IN_REVIEW = "in_review"
    RELEASED = "released"
    FALSE_POSITIVE = "false_positive"
    CONFIRMED_MALICIOUS = "confirmed_malicious"
    DELETED = "deleted"


class QuarantineAction(enum.StrEnum):
    """Actions that can be applied to a quarantine item via the review UI.

    Each action maps to a valid state transition defined in the service layer.
    """

    MARK_IN_REVIEW = "mark_in_review"
    RELEASE = "release"
    MARK_FALSE_POSITIVE = "mark_false_positive"
    CONFIRM_MALICIOUS = "confirm_malicious"
    DELETE = "delete"


class QuarantineItem(Base):
    """A single quarantined message pending or under analyst review.

    One item is created per message when it receives a quarantine-worthy verdict.
    The item is updated (not replaced) when the message is re-analysed.
    """

    __tablename__ = "quarantine_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Link to the source message record — unique so each message has at most one item.
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Link to the analysis run that triggered quarantine (nullable — set on creation,
    # updated when re-analysis changes the situation).
    analysis_run_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("analysis_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Current lifecycle state.
    status: Mapped[str] = mapped_column(
        SAEnum(QuarantineStatus, name="quarantine_status", native_enum=False, length=30),
        nullable=False,
        default=QuarantineStatus.PENDING_REVIEW,
        index=True,
    )

    # The verdict that triggered quarantine ("quarantine", "soft_hold", "escalate_to_admin").
    trigger_verdict: Mapped[str] = mapped_column(String(30), nullable=False)

    # Risk score snapshot at the time of quarantine (for inbox sorting/display).
    risk_score_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Analyst notes — updated on each review action.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Review provenance — set when any review action is taken.
    reviewed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ORM relationships
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent",
        primaryjoin=(
            "and_(AuditEvent.target_type=='quarantine_item',"
            " foreign(AuditEvent.target_id)==QuarantineItem.id)"
        ),
        lazy="selectin",
        order_by="AuditEvent.created_at",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return (
            f"<QuarantineItem id={self.id} message_id={self.message_id}"
            f" status={self.status}>"
        )


class AuditEvent(Base):
    """Immutable audit record for any security-relevant action.

    Rows are never updated or deleted. The updated_at column inherited from Base
    will equal created_at for all audit rows.

    Reusable across all Mindwall domains — keyed by (target_type, target_id).
    Common target_types: 'quarantine_item', 'message', 'mailbox_profile', 'user'.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Actor — nullable for system-generated events (e.g. auto-quarantine).
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # What happened
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    # What it happened to
    target_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # State transition tracking
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Human-readable note from the actor
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional structured metadata (JSON)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id} action={self.action!r}"
            f" target={self.target_type}/{self.target_id}>"
        )
