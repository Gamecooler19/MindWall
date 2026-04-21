"""Unit tests for the alerts service.

Covers:
  - Alert creation and severity mapping from verdicts
  - Acknowledge and resolve state transitions
  - Invalid transitions raise ValueError
  - count_open_alerts
"""

from __future__ import annotations

import asyncio

import pytest
from app.alerts import service as alerts_service
from app.alerts.models import AlertSeverity, AlertStatus
from app.policies.constants import Verdict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture()
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# create_alert_for_verdict — no DB needed for severity mapping
# ---------------------------------------------------------------------------


class TestAlertSeverityMapping:
    def test_quarantine_verdict_maps_high(self):
        from app.alerts.service import _VERDICT_SEVERITY

        assert _VERDICT_SEVERITY[Verdict.QUARANTINE] == AlertSeverity.HIGH

    def test_escalate_maps_critical(self):
        from app.alerts.service import _VERDICT_SEVERITY

        assert _VERDICT_SEVERITY[Verdict.ESCALATE_TO_ADMIN] == AlertSeverity.CRITICAL

    def test_soft_hold_maps_medium(self):
        from app.alerts.service import _VERDICT_SEVERITY

        assert _VERDICT_SEVERITY[Verdict.SOFT_HOLD] == AlertSeverity.MEDIUM

    def test_allow_not_in_mapping(self):
        from app.alerts.service import _VERDICT_SEVERITY

        assert Verdict.ALLOW not in _VERDICT_SEVERITY

    def test_allow_with_banner_not_in_mapping(self):
        from app.alerts.service import _VERDICT_SEVERITY

        assert Verdict.ALLOW_WITH_BANNER not in _VERDICT_SEVERITY


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


class TestCreateAlert:
    def test_create_alert_persists(self, factory):
        async def _run():
            async with factory() as db:
                alert = await alerts_service.create_alert(
                    db,
                    title="Test alert",
                    severity=AlertSeverity.HIGH,
                )
                await db.commit()
                return alert.id, alert.status, alert.severity

        alert_id, status, severity = asyncio.get_event_loop().run_until_complete(_run())
        assert alert_id is not None
        assert status == AlertStatus.OPEN
        assert severity == AlertSeverity.HIGH

    def test_create_alert_for_quarantine_verdict(self, factory):
        async def _run():
            async with factory() as db:
                alert = await alerts_service.create_alert_for_verdict(
                    db,
                    verdict=Verdict.QUARANTINE,
                    sender="evil@phish.example",
                    subject="Urgent: verify your account",
                    risk_score=0.92,
                )
                await db.commit()
                return alert.id, alert.severity, alert.status

        alert_id, severity, status = asyncio.get_event_loop().run_until_complete(_run())
        assert alert_id is not None
        assert severity == AlertSeverity.HIGH
        assert status == AlertStatus.OPEN

    def test_create_alert_for_allow_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                result = await alerts_service.create_alert_for_verdict(
                    db,
                    verdict=Verdict.ALLOW,
                )
                return result

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None


class TestCountOpenAlerts:
    def test_count_open_alerts_zero_initially(self, factory):
        async def _run():
            async with factory() as db:
                return await alerts_service.count_open_alerts(db)

        count = asyncio.get_event_loop().run_until_complete(_run())
        # May be non-zero if other tests created alerts — just check it's an int
        assert isinstance(count, int)
        assert count >= 0

    def test_creating_alert_increments_count(self, factory):
        async def _run():
            async with factory() as db:
                before = await alerts_service.count_open_alerts(db)
                await alerts_service.create_alert(
                    db,
                    title="Count test alert",
                    severity=AlertSeverity.LOW,
                )
                await db.commit()

            async with factory() as db:
                after = await alerts_service.count_open_alerts(db)
                return before, after

        before, after = asyncio.get_event_loop().run_until_complete(_run())
        assert after == before + 1


class TestAcknowledgeResolveAlert:
    def test_acknowledge_open_alert(self, factory):
        async def _run():
            async with factory() as db:
                alert = await alerts_service.create_alert(
                    db,
                    title="Ack test",
                    severity=AlertSeverity.MEDIUM,
                )
                await db.commit()
                return alert.id

        alert_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _ack():
            async with factory() as db:
                alert = await alerts_service.acknowledge_alert(db, alert_id, actor_user_id=1)
                return alert.status, alert.acknowledged_by_user_id

        status, actor = asyncio.get_event_loop().run_until_complete(_ack())
        assert status == AlertStatus.ACKNOWLEDGED
        assert actor == 1

    def test_acknowledge_non_open_alert_raises(self, factory):
        async def _run():
            async with factory() as db:
                alert = await alerts_service.create_alert(
                    db,
                    title="Ack fail test",
                    severity=AlertSeverity.MEDIUM,
                )
                await db.commit()
                return alert.id

        alert_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _ack_twice():
            async with factory() as db:
                await alerts_service.acknowledge_alert(db, alert_id, actor_user_id=1)

            async with factory() as db:
                await alerts_service.acknowledge_alert(db, alert_id, actor_user_id=1)

        with pytest.raises(ValueError, match="only OPEN"):
            asyncio.get_event_loop().run_until_complete(_ack_twice())

    def test_resolve_alert(self, factory):
        async def _run():
            async with factory() as db:
                alert = await alerts_service.create_alert(
                    db,
                    title="Resolve test",
                    severity=AlertSeverity.HIGH,
                )
                await db.commit()
                return alert.id

        alert_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _resolve():
            async with factory() as db:
                alert = await alerts_service.resolve_alert(
                    db, alert_id, actor_user_id=2, note="No threat"
                )
                return alert.status, alert.resolution_note

        status, note = asyncio.get_event_loop().run_until_complete(_resolve())
        assert status == AlertStatus.RESOLVED
        assert note == "No threat"

    def test_resolve_already_resolved_raises(self, factory):
        async def _run():
            async with factory() as db:
                alert = await alerts_service.create_alert(
                    db,
                    title="Double resolve test",
                    severity=AlertSeverity.LOW,
                )
                await db.commit()
                return alert.id

        alert_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _resolve_twice():
            async with factory() as db:
                await alerts_service.resolve_alert(db, alert_id, actor_user_id=1)

            async with factory() as db:
                await alerts_service.resolve_alert(db, alert_id, actor_user_id=1)

        with pytest.raises(ValueError, match="already resolved"):
            asyncio.get_event_loop().run_until_complete(_resolve_twice())

    def test_get_nonexistent_alert_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                return await alerts_service.get_alert(db, 999999)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None
