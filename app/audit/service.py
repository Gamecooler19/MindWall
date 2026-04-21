"""Audit trail service for Mindwall.

Provides a single, reusable interface for recording append-only audit events.

Every security-relevant action (quarantine, release, false-positive, login,
credential change, policy edit) should produce an AuditEvent row via this
service.  Rows are never mutated after insert.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.quarantine.models import AuditEvent

log = structlog.get_logger(__name__)


async def record_event(
    db: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: int,
    actor_user_id: int | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    metadata_json: str | None = None,
) -> AuditEvent:
    """Insert an append-only audit event and flush it to the current transaction.

    Does NOT commit — callers own the transaction boundary so that the audit
    event and the triggering state change are committed atomically.

    Args:
        db:             Active async session.
        action:         Short action identifier, e.g. ``"quarantine.created"``.
        target_type:    Entity type, e.g. ``"quarantine_item"``, ``"message"``.
        target_id:      Primary key of the target entity.
        actor_user_id:  User who performed the action; None for system events.
        from_status:    Previous status (state transitions).
        to_status:      New status (state transitions).
        note:           Human-readable comment from the actor.
        metadata_json:  Optional structured JSON string for extra context.

    Returns:
        The flushed (but not yet committed) AuditEvent row.
    """
    event = AuditEvent(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        from_status=from_status,
        to_status=to_status,
        note=note,
        metadata_json=metadata_json,
    )
    db.add(event)
    await db.flush()

    log.debug(
        "audit.event_recorded",
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_user_id=actor_user_id,
        from_status=from_status,
        to_status=to_status,
    )
    return event


async def get_events_for_target(
    db: AsyncSession,
    target_type: str,
    target_id: int,
) -> list[AuditEvent]:
    """Return all audit events for a specific object, oldest first."""
    result = await db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.target_type == target_type,
            AuditEvent.target_id == target_id,
        )
        .order_by(AuditEvent.created_at)
    )
    return list(result.scalars().all())
