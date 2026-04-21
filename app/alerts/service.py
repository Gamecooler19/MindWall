"""Alert service for Mindwall.

Creates, lists, and manages lifecycle transitions for security alerts.

Alerts are created automatically when the quarantine service processes a
message with a high-risk verdict.  Admins acknowledge and resolve them via
the admin UI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.models import Alert, AlertSeverity, AlertStatus
from app.audit import service as audit_service
from app.policies.constants import Verdict

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Mapping verdict → severity
# ---------------------------------------------------------------------------

_VERDICT_SEVERITY: dict[str, AlertSeverity] = {
    Verdict.ESCALATE_TO_ADMIN: AlertSeverity.CRITICAL,
    Verdict.QUARANTINE: AlertSeverity.HIGH,
    Verdict.SOFT_HOLD: AlertSeverity.MEDIUM,
    Verdict.REJECT: AlertSeverity.HIGH,
}


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def create_alert(
    db: AsyncSession,
    *,
    title: str,
    body: str | None = None,
    severity: AlertSeverity,
    trigger_action: str | None = None,
    quarantine_item_id: int | None = None,
    message_id: int | None = None,
    actor_user_id: int | None = None,
) -> Alert:
    """Create a new alert and flush it to the current transaction.

    Does NOT commit — caller owns the transaction so the alert and the
    triggering event are committed atomically.
    """
    alert = Alert(
        title=title,
        body=body,
        severity=severity,
        status=AlertStatus.OPEN,
        trigger_action=trigger_action,
        quarantine_item_id=quarantine_item_id,
        message_id=message_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    db.add(alert)
    await db.flush()

    await audit_service.record_event(
        db,
        action="alert.created",
        target_type="alert",
        target_id=alert.id,
        actor_user_id=actor_user_id,
        to_status=AlertStatus.OPEN,
        note=f"Alert created: {title}",
    )

    log.info(
        "alert.created",
        alert_id=alert.id,
        severity=severity,
        trigger_action=trigger_action,
        quarantine_item_id=quarantine_item_id,
    )
    return alert


async def create_alert_for_verdict(
    db: AsyncSession,
    *,
    verdict: str,
    quarantine_item_id: int | None = None,
    message_id: int | None = None,
    sender: str | None = None,
    subject: str | None = None,
    risk_score: float | None = None,
) -> Alert | None:
    """Create an alert based on a message verdict.

    Returns None if the verdict doesn't warrant an alert.
    Only QUARANTINE, ESCALATE_TO_ADMIN, REJECT, and SOFT_HOLD verdicts
    create alerts.
    """
    severity = _VERDICT_SEVERITY.get(verdict)
    if severity is None:
        return None

    title_parts = []
    if verdict == Verdict.ESCALATE_TO_ADMIN:
        title_parts.append("[CRITICAL] Message escalated for admin review")
    elif verdict == Verdict.QUARANTINE:
        title_parts.append("[HIGH] Message quarantined")
    elif verdict == Verdict.REJECT:
        title_parts.append("[HIGH] Message rejected at gateway")
    elif verdict == Verdict.SOFT_HOLD:
        title_parts.append("[MEDIUM] Message soft-held for review")

    if sender:
        title_parts.append(f"from {sender}")
    title = " ".join(title_parts)

    body_parts = []
    if subject:
        body_parts.append(f"Subject: {subject}")
    if sender:
        body_parts.append(f"Sender: {sender}")
    if risk_score is not None:
        body_parts.append(f"Risk score: {risk_score:.2f}")
    body_parts.append(f"Verdict: {verdict}")
    body = "\n".join(body_parts) if body_parts else None

    return await create_alert(
        db,
        title=title,
        body=body,
        severity=severity,
        trigger_action=f"verdict.{verdict}",
        quarantine_item_id=quarantine_item_id,
        message_id=message_id,
    )


async def list_alerts(
    db: AsyncSession,
    *,
    status: AlertStatus | None = None,
    severity: AlertSeverity | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Alert]:
    """Return a list of alerts, newest first, with optional filters."""
    stmt = select(Alert)
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    stmt = stmt.order_by(Alert.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_open_alerts(db: AsyncSession) -> int:
    """Return the count of open (unacknowledged) alerts."""
    from sqlalchemy import func

    result = await db.execute(
        select(func.count()).where(Alert.status == AlertStatus.OPEN)  # type: ignore[arg-type]
    )
    return result.scalar_one()


async def get_alert(db: AsyncSession, alert_id: int) -> Alert | None:
    """Return a single alert by ID, or None if not found."""
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    return result.scalar_one_or_none()


async def acknowledge_alert(
    db: AsyncSession,
    alert_id: int,
    actor_user_id: int,
    note: str | None = None,
) -> Alert:
    """Transition alert to ACKNOWLEDGED state.

    Raises ValueError if the alert is not in OPEN state.
    """
    alert = await get_alert(db, alert_id)
    if alert is None:
        raise ValueError(f"Alert {alert_id} not found.")
    if alert.status != AlertStatus.OPEN:
        raise ValueError(
            f"Alert {alert_id} is {alert.status}; only OPEN alerts can be acknowledged."
        )

    old_status = alert.status
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by_user_id = actor_user_id
    alert.acknowledged_at = datetime.now(UTC).isoformat()
    if note:
        alert.resolution_note = note

    await db.flush()

    await audit_service.record_event(
        db,
        action="alert.acknowledged",
        target_type="alert",
        target_id=alert.id,
        actor_user_id=actor_user_id,
        from_status=old_status,
        to_status=AlertStatus.ACKNOWLEDGED,
        note=note,
    )

    await db.commit()
    log.info("alert.acknowledged", alert_id=alert_id, actor=actor_user_id)
    return alert


async def resolve_alert(
    db: AsyncSession,
    alert_id: int,
    actor_user_id: int,
    note: str | None = None,
) -> Alert:
    """Transition alert to RESOLVED state.

    Raises ValueError if the alert is already resolved.
    """
    alert = await get_alert(db, alert_id)
    if alert is None:
        raise ValueError(f"Alert {alert_id} not found.")
    if alert.status == AlertStatus.RESOLVED:
        raise ValueError(f"Alert {alert_id} is already resolved.")

    old_status = alert.status
    alert.status = AlertStatus.RESOLVED
    alert.resolved_by_user_id = actor_user_id
    alert.resolved_at = datetime.now(UTC).isoformat()
    if note:
        alert.resolution_note = note

    await db.flush()

    await audit_service.record_event(
        db,
        action="alert.resolved",
        target_type="alert",
        target_id=alert.id,
        actor_user_id=actor_user_id,
        from_status=old_status,
        to_status=AlertStatus.RESOLVED,
        note=note,
    )

    await db.commit()
    log.info("alert.resolved", alert_id=alert_id, actor=actor_user_id)
    return alert
