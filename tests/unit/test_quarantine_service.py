"""Unit tests for the quarantine service.

Tests state machine logic, transition validation, should_quarantine helper,
and audit event creation — no network or live Ollama required.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from app.policies.constants import Verdict
from app.quarantine.models import QuarantineAction, QuarantineStatus
from app.quarantine.service import (
    InvalidTransitionError,
    apply_action,
    count_pending_review,
    get_or_create_quarantine_item,
    get_quarantine_item_by_id,
    should_quarantine,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# should_quarantine — no DB needed
# ---------------------------------------------------------------------------


class TestShouldQuarantine:
    def test_quarantine_verdict_triggers(self):
        assert should_quarantine(Verdict.QUARANTINE) is True

    def test_escalate_triggers(self):
        assert should_quarantine(Verdict.ESCALATE_TO_ADMIN) is True

    def test_reject_triggers(self):
        assert should_quarantine(Verdict.REJECT) is True

    def test_allow_does_not_trigger(self):
        assert should_quarantine(Verdict.ALLOW) is False

    def test_allow_with_banner_does_not_trigger(self):
        assert should_quarantine(Verdict.ALLOW_WITH_BANNER) is False

    def test_soft_hold_no_trigger_by_default(self):
        assert should_quarantine(Verdict.SOFT_HOLD) is False

    def test_soft_hold_triggers_when_flag_set(self):
        assert should_quarantine(Verdict.SOFT_HOLD, quarantine_soft_hold=True) is True


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


# Minimal message_id used by all service tests — must exist in the DB.
# Because the session DB has messages table, we insert a stub message using
# the ingest service via a fixture.
@pytest_asyncio.fixture()
async def stub_message(db_engine):
    """Insert a minimal message row and return its id."""
    from datetime import UTC, datetime

    from sqlalchemy import text

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        result = await session.execute(
            text(
                "INSERT INTO messages "
                "(ingestion_source, raw_size_bytes, raw_sha256, has_text_plain,"
                " has_text_html, header_dkim_signature_present, num_attachments,"
                " num_urls, created_at, updated_at) "
                "VALUES (:src, :size, :sha, :tp, :th, :dkim, :na, :nu, :now, :now) "
                "RETURNING id"
            ),
            {
                "src": "message_lab",
                "size": 100,
                "sha": "aabbcc" + "0" * 58,
                "tp": True,
                "th": False,
                "dkim": False,
                "na": 0,
                "nu": 0,
                "now": datetime.now(UTC),
            },
        )
        msg_id = result.scalar_one()
        await session.commit()
    return msg_id


@pytest_asyncio.fixture()
async def stub_analysis_run(db_engine, stub_message):
    """Insert a minimal analysis_run row and return its id."""
    from datetime import UTC, datetime

    from sqlalchemy import text

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        result = await session.execute(
            text(
                "INSERT INTO analysis_runs "
                "(message_id, analysis_version, model_provider, status,"
                " is_degraded, verdict, created_at, updated_at) "
                "VALUES (:mid, :av, :mp, :st, :deg, :verdict, :now, :now) "
                "RETURNING id"
            ),
            {
                "mid": stub_message,
                "av": "1.0",
                "mp": "none",
                "st": "complete",
                "deg": False,
                "verdict": "quarantine",
                "now": datetime.now(UTC),
            },
        )
        run_id = result.scalar_one()
        await session.commit()
    return run_id


class TestGetOrCreateQuarantineItem:
    def test_creates_new_item(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                item, created = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()
                return item, created

        item, created = asyncio.get_event_loop().run_until_complete(_run())
        assert created is True
        assert item.id is not None
        assert item.message_id == stub_message
        assert item.status == QuarantineStatus.PENDING_REVIEW
        assert item.trigger_verdict == "quarantine"
        assert item.risk_score_snapshot == pytest.approx(0.9)

    def test_idempotent_on_second_call(self, factory, stub_message, stub_analysis_run):
        """Second call for the same message should return the same item."""

        async def _run():
            async with factory() as db:
                item1, created1 = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.88,
                )
                await db.commit()

            async with factory() as db:
                item2, created2 = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.92,
                )
                await db.commit()
                return item1, created1, item2, created2

        item1, created1, item2, created2 = asyncio.get_event_loop().run_until_complete(
            _run()
        )
        assert created1 is True
        assert created2 is False
        assert item1.id == item2.id

    def test_re_analysis_updates_risk_snapshot(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.80,
                )
                await db.commit()

            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.95,
                )
                await db.commit()
                return item

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.risk_score_snapshot == pytest.approx(0.95)


class TestStateTransitions:
    @pytest_asyncio.fixture()
    async def pending_item(self, factory, stub_message, stub_analysis_run):
        """Create a quarantine item in PENDING_REVIEW state."""
        async with factory() as db:
            item, _ = await get_or_create_quarantine_item(
                db,
                message_id=stub_message,
                analysis_run_id=stub_analysis_run,
                trigger_verdict="quarantine",
                risk_score_snapshot=0.9,
            )
            await db.commit()
            return item.id

    def test_mark_in_review_from_pending(self, factory, pending_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, pending_item)
                return await apply_action(db, item=item, action=QuarantineAction.MARK_IN_REVIEW)

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.status == QuarantineStatus.IN_REVIEW

    def test_release_from_pending(self, factory, pending_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, pending_item)
                return await apply_action(db, item=item, action=QuarantineAction.RELEASE)

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.status == QuarantineStatus.RELEASED

    def test_false_positive_from_pending(self, factory, pending_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, pending_item)
                return await apply_action(
                    db, item=item, action=QuarantineAction.MARK_FALSE_POSITIVE
                )

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.status == QuarantineStatus.FALSE_POSITIVE

    def test_confirm_malicious_from_pending(self, factory, pending_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, pending_item)
                return await apply_action(
                    db, item=item, action=QuarantineAction.CONFIRM_MALICIOUS
                )

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.status == QuarantineStatus.CONFIRMED_MALICIOUS

    def test_delete_from_pending(self, factory, pending_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, pending_item)
                return await apply_action(db, item=item, action=QuarantineAction.DELETE)

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.status == QuarantineStatus.DELETED

    def test_delete_from_released(self, factory, pending_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, pending_item)
                item = await apply_action(db, item=item, action=QuarantineAction.RELEASE)
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, item.id)
                return await apply_action(db, item=item, action=QuarantineAction.DELETE)

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.status == QuarantineStatus.DELETED


class TestInvalidTransitions:
    @pytest_asyncio.fixture()
    async def deleted_item(self, factory, stub_message, stub_analysis_run):
        async with factory() as db:
            item, _ = await get_or_create_quarantine_item(
                db,
                message_id=stub_message,
                analysis_run_id=stub_analysis_run,
                trigger_verdict="quarantine",
                risk_score_snapshot=0.9,
            )
            await db.commit()

        async with factory() as db:
            item = await get_quarantine_item_by_id(db, item.id)
            await apply_action(db, item=item, action=QuarantineAction.DELETE)
            return item.id

    def test_cannot_release_deleted_item(self, factory, deleted_item):
        async def _run():
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, deleted_item)
                await apply_action(db, item=item, action=QuarantineAction.RELEASE)

        with pytest.raises(InvalidTransitionError):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_cannot_mark_in_review_from_released(self, factory, stub_message, stub_analysis_run):
        async def _setup():
            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, item.id)
                return await apply_action(db, item=item, action=QuarantineAction.RELEASE)

        async def _act(item_id):
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, item_id)
                await apply_action(db, item=item, action=QuarantineAction.MARK_IN_REVIEW)

        released = asyncio.get_event_loop().run_until_complete(_setup())
        with pytest.raises(InvalidTransitionError):
            asyncio.get_event_loop().run_until_complete(_act(released.id))

    def test_unknown_action_raises_value_error(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()
            async with factory() as db:
                item = await get_quarantine_item_by_id(db, item.id)
                await apply_action(db, item=item, action="fly_to_moon")

        with pytest.raises(ValueError, match="Unknown quarantine action"):
            asyncio.get_event_loop().run_until_complete(_run())


class TestAuditTrail:
    def test_create_produces_audit_event(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()

            async with factory() as db:
                loaded = await get_quarantine_item_by_id(db, item.id)
                return loaded

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert len(item.audit_events) >= 1
        assert item.audit_events[0].action == "quarantine.created"
        assert item.audit_events[0].to_status == QuarantineStatus.PENDING_REVIEW

    def test_action_produces_audit_event(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()

            async with factory() as db:
                item = await get_quarantine_item_by_id(db, item.id)
                await apply_action(
                    db,
                    item=item,
                    action=QuarantineAction.RELEASE,
                    actor_user_id=42,
                    note="Cleared by analyst",
                )

            async with factory() as db:
                return await get_quarantine_item_by_id(db, item.id)

        item = asyncio.get_event_loop().run_until_complete(_run())
        actions = [e.action for e in item.audit_events]
        assert "quarantine.release" in actions
        release_event = next(e for e in item.audit_events if e.action == "quarantine.release")
        assert release_event.from_status == QuarantineStatus.PENDING_REVIEW
        assert release_event.to_status == QuarantineStatus.RELEASED
        assert release_event.actor_user_id == 42
        assert release_event.note == "Cleared by analyst"

    def test_note_appended_on_action(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()

            async with factory() as db:
                item = await get_quarantine_item_by_id(db, item.id)
                return await apply_action(
                    db,
                    item=item,
                    action=QuarantineAction.RELEASE,
                    note="Confirmed safe",
                )

        item = asyncio.get_event_loop().run_until_complete(_run())
        assert item.notes == "Confirmed safe"


class TestCountPendingReview:
    def test_count_increases_on_quarantine(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                before = await count_pending_review(db)
            async with factory() as db:
                await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()
            async with factory() as db:
                after = await count_pending_review(db)
            return before, after

        before, after = asyncio.get_event_loop().run_until_complete(_run())
        assert after == before + 1

    def test_count_decreases_after_release(self, factory, stub_message, stub_analysis_run):
        async def _run():
            async with factory() as db:
                item, _ = await get_or_create_quarantine_item(
                    db,
                    message_id=stub_message,
                    analysis_run_id=stub_analysis_run,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()

            async with factory() as db:
                before = await count_pending_review(db)
                item = await get_quarantine_item_by_id(db, item.id)
                await apply_action(db, item=item, action=QuarantineAction.RELEASE)

            async with factory() as db:
                after = await count_pending_review(db)
            return before, after

        before, after = asyncio.get_event_loop().run_until_complete(_run())
        assert after == before - 1
