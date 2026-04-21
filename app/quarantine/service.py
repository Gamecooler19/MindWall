"""Quarantine service for Mindwall.

Handles the full quarantine lifecycle:
  - Deciding whether a verdict warrants quarantine
  - Idempotent creation and update of QuarantineItem records
  - State transitions with validation
  - Audit trail recording for every action
  - Inbox and detail queries

State machine
-------------
Valid transitions per action:

  mark_in_review      : pending_review         → in_review
  release             : pending_review,         → released
                        in_review
  mark_false_positive : pending_review,         → false_positive
                        in_review
  confirm_malicious   : pending_review,         → confirmed_malicious
                        in_review
  delete              : any non-deleted state   → deleted
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit import service as audit_service
from app.policies.constants import Verdict
from app.quarantine.models import QuarantineAction, QuarantineItem, QuarantineStatus

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Valid state transitions
# ---------------------------------------------------------------------------

# Maps each action to (valid_from_states, target_state)
_VALID_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    QuarantineAction.MARK_IN_REVIEW: (
        frozenset({QuarantineStatus.PENDING_REVIEW}),
        QuarantineStatus.IN_REVIEW,
    ),
    QuarantineAction.RELEASE: (
        frozenset({QuarantineStatus.PENDING_REVIEW, QuarantineStatus.IN_REVIEW}),
        QuarantineStatus.RELEASED,
    ),
    QuarantineAction.MARK_FALSE_POSITIVE: (
        frozenset({QuarantineStatus.PENDING_REVIEW, QuarantineStatus.IN_REVIEW}),
        QuarantineStatus.FALSE_POSITIVE,
    ),
    QuarantineAction.CONFIRM_MALICIOUS: (
        frozenset({QuarantineStatus.PENDING_REVIEW, QuarantineStatus.IN_REVIEW}),
        QuarantineStatus.CONFIRMED_MALICIOUS,
    ),
    QuarantineAction.DELETE: (
        frozenset(
            {
                QuarantineStatus.PENDING_REVIEW,
                QuarantineStatus.IN_REVIEW,
                QuarantineStatus.RELEASED,
                QuarantineStatus.FALSE_POSITIVE,
                QuarantineStatus.CONFIRMED_MALICIOUS,
            }
        ),
        QuarantineStatus.DELETED,
    ),
}

# Verdicts that automatically trigger quarantine creation.
_QUARANTINE_VERDICTS: frozenset[str] = frozenset(
    {Verdict.QUARANTINE, Verdict.ESCALATE_TO_ADMIN, Verdict.REJECT}
)


class InvalidTransitionError(ValueError):
    """Raised when a requested state transition is not permitted."""


def should_quarantine(verdict: str, quarantine_soft_hold: bool = False) -> bool:
    """Return True if the given verdict warrants creating a quarantine item.

    Args:
        verdict:              The policy verdict string.
        quarantine_soft_hold: Whether SOFT_HOLD verdicts should also be quarantined.
                              Controlled by the ``quarantine_soft_hold`` setting.
    """
    if verdict in _QUARANTINE_VERDICTS:
        return True
    if quarantine_soft_hold and verdict == Verdict.SOFT_HOLD:
        return True
    return False


async def get_or_create_quarantine_item(
    db: AsyncSession,
    *,
    message_id: int,
    analysis_run_id: int,
    trigger_verdict: str,
    risk_score_snapshot: float | None,
    actor_user_id: int | None = None,
) -> tuple[QuarantineItem, bool]:
    """Create a new quarantine item or update the existing one for this message.

    This function is idempotent: if a QuarantineItem already exists for the
    message, it updates the analysis_run_id and risk_score_snapshot rather than
    creating a duplicate.

    Args:
        db:                  Active async session.
        message_id:          ID of the quarantined message.
        analysis_run_id:     ID of the AnalysisRun that triggered quarantine.
        trigger_verdict:     The verdict string (e.g. ``"quarantine"``).
        risk_score_snapshot: Overall risk score at time of quarantine.
        actor_user_id:       User ID (None for system-triggered events).

    Returns:
        (QuarantineItem, created) where ``created`` is True for new items.
    """
    # Check for existing item
    result = await db.execute(
        select(QuarantineItem)
        .where(QuarantineItem.message_id == message_id)
        .options(selectinload(QuarantineItem.audit_events))
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        # Update the analysis reference and risk snapshot on re-analysis.
        old_risk = existing.risk_score_snapshot
        existing.analysis_run_id = analysis_run_id
        existing.risk_score_snapshot = risk_score_snapshot
        existing.trigger_verdict = trigger_verdict
        existing.updated_at = datetime.now(UTC)  # type: ignore[assignment]
        await db.flush()

        await audit_service.record_event(
            db,
            action="quarantine.updated",
            target_type="quarantine_item",
            target_id=existing.id,
            actor_user_id=actor_user_id,
            from_status=existing.status,
            to_status=existing.status,
            note=(
                f"Re-analysis updated quarantine item. "
                f"Risk: {old_risk:.3f} -> {risk_score_snapshot:.3f}"
                if old_risk is not None and risk_score_snapshot is not None
                else "Re-analysis updated quarantine item."
            ),
        )
        log.info(
            "quarantine.item_updated",
            quarantine_id=existing.id,
            message_id=message_id,
            verdict=trigger_verdict,
        )
        return existing, False

    # Create new item
    item = QuarantineItem(
        message_id=message_id,
        analysis_run_id=analysis_run_id,
        status=QuarantineStatus.PENDING_REVIEW,
        trigger_verdict=trigger_verdict,
        risk_score_snapshot=risk_score_snapshot,
    )
    db.add(item)
    await db.flush()

    await audit_service.record_event(
        db,
        action="quarantine.created",
        target_type="quarantine_item",
        target_id=item.id,
        actor_user_id=actor_user_id,
        from_status=None,
        to_status=QuarantineStatus.PENDING_REVIEW,
        note=f"Message quarantined. Verdict: {trigger_verdict}.",
    )

    log.info(
        "quarantine.item_created",
        quarantine_id=item.id,
        message_id=message_id,
        verdict=trigger_verdict,
        risk_score=risk_score_snapshot,
    )
    return item, True


async def apply_action(
    db: AsyncSession,
    *,
    item: QuarantineItem,
    action: str,
    actor_user_id: int | None = None,
    note: str | None = None,
) -> QuarantineItem:
    """Apply a review action to a quarantine item and record the audit event.

    Validates the state transition before making any changes.
    Commits the transaction.

    Args:
        db:            Active async session.
        item:          The QuarantineItem to act on.
        action:        One of the QuarantineAction values.
        actor_user_id: ID of the user performing the action.
        note:          Optional analyst comment.

    Returns:
        The updated QuarantineItem.

    Raises:
        InvalidTransitionError: If the action is not valid from the current state.
        ValueError:             If the action string is unrecognised.
    """
    if action not in _VALID_TRANSITIONS:
        raise ValueError(f"Unknown quarantine action: {action!r}")

    valid_from, target_status = _VALID_TRANSITIONS[action]
    if item.status not in valid_from:
        raise InvalidTransitionError(
            f"Cannot apply action {action!r} to a quarantine item in status "
            f"{item.status!r}. Valid from: {sorted(valid_from)}"
        )

    from_status = item.status
    now = datetime.now(UTC)

    item.status = target_status  # type: ignore[assignment]
    item.updated_at = now  # type: ignore[assignment]

    # Record review provenance for analyst actions.
    if action != QuarantineAction.MARK_IN_REVIEW or item.reviewed_at is None:
        item.reviewed_at = now  # type: ignore[assignment]
        item.reviewed_by_user_id = actor_user_id

    if note:
        # Append to existing notes.
        item.notes = (
            f"{item.notes}\n\n{note}" if item.notes else note
        )

    await db.flush()

    await audit_service.record_event(
        db,
        action=f"quarantine.{action}",
        target_type="quarantine_item",
        target_id=item.id,
        actor_user_id=actor_user_id,
        from_status=from_status,
        to_status=target_status,
        note=note,
    )

    await db.commit()
    await db.refresh(item)

    log.info(
        "quarantine.action_applied",
        quarantine_id=item.id,
        message_id=item.message_id,
        action=action,
        from_status=from_status,
        to_status=target_status,
        actor_user_id=actor_user_id,
    )
    return item


async def list_quarantine_items(
    db: AsyncSession,
    status_filter: str | None = None,
) -> list[QuarantineItem]:
    """Return quarantine items for the inbox, newest first.

    Args:
        db:            Active async session.
        status_filter: Optionally restrict to a specific status value.
                       Defaults to all non-deleted items.
    """
    stmt = select(QuarantineItem)

    if status_filter:
        stmt = stmt.where(QuarantineItem.status == status_filter)
    else:
        # Default: exclude deleted items from the inbox.
        stmt = stmt.where(QuarantineItem.status != QuarantineStatus.DELETED)

    stmt = stmt.order_by(QuarantineItem.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_quarantine_item_by_id(
    db: AsyncSession,
    item_id: int,
) -> QuarantineItem | None:
    """Load a single quarantine item with its audit history."""
    result = await db.execute(
        select(QuarantineItem)
        .where(QuarantineItem.id == item_id)
        .options(selectinload(QuarantineItem.audit_events))
    )
    return result.scalar_one_or_none()


async def get_quarantine_item_for_message(
    db: AsyncSession,
    message_id: int,
) -> QuarantineItem | None:
    """Return the quarantine item for a message, if one exists."""
    result = await db.execute(
        select(QuarantineItem)
        .where(QuarantineItem.message_id == message_id)
        .options(selectinload(QuarantineItem.audit_events))
    )
    return result.scalar_one_or_none()


async def count_pending_review(db: AsyncSession) -> int:
    """Return the number of items currently awaiting review."""
    from sqlalchemy import func

    result = await db.execute(
        select(func.count()).select_from(QuarantineItem).where(
            QuarantineItem.status.in_(
                [QuarantineStatus.PENDING_REVIEW, QuarantineStatus.IN_REVIEW]
            )
        )
    )
    return result.scalar_one()
