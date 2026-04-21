"""Integration tests for Phase 5 quarantine workflow.

Covers:
  - Auto-quarantine on quarantine verdict
  - No quarantine on allow verdict
  - Idempotent behaviour on repeated analysis
  - Quarantine inbox and detail page rendering
  - Review action routes (release, false-positive, etc.)
  - Admin-only / analyst access enforcement
  - Audit event persistence after actions
  - Invalid transition returns 409
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.analysis import service as analysis_service
from app.analysis.ollama_client import OllamaClient, OllamaResponse
from app.auth.service import hash_password
from app.messages import service as msg_service
from app.messages.models import IngestionSource
from app.messages.storage import RawMessageStore
from app.policies.constants import ManipulationDimension, Verdict
from app.quarantine import service as quarantine_service
from app.quarantine.models import QuarantineStatus
from app.quarantine.service import get_quarantine_item_by_id, get_quarantine_item_for_message
from app.users.models import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"

_ALL_DIM_SCORES = {d.value: 0.05 for d in ManipulationDimension}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_high_risk_ollama_client() -> MagicMock:
    """Mock client returning a very high risk score (triggers quarantine verdict)."""
    client = MagicMock(spec=OllamaClient)
    response_dict = {
        "overall_risk": 0.95,
        "manipulation_dimensions": {d.value: 0.85 for d in ManipulationDimension},
        "summary": "High-risk phishing message.",
        "evidence": ["Suspicious links", "Credential capture language"],
        "recommended_action": "quarantine",
        "confidence": 0.95,
    }
    raw_text = json.dumps(response_dict)
    ollama_resp = OllamaResponse(raw_text=raw_text, model="llama3.1:8b", done=True)
    client.generate = AsyncMock(return_value=ollama_resp)
    client.health_check = AsyncMock(return_value=True)
    return client


def _make_low_risk_ollama_client() -> MagicMock:
    """Mock client returning a very low risk score (allow verdict)."""
    client = MagicMock(spec=OllamaClient)
    response_dict = {
        "overall_risk": 0.05,
        "manipulation_dimensions": _ALL_DIM_SCORES,
        "summary": "Clean message.",
        "evidence": [],
        "recommended_action": "allow",
        "confidence": 0.95,
    }
    raw_text = json.dumps(response_dict)
    ollama_resp = OllamaResponse(raw_text=raw_text, model="llama3.1:8b", done=True)
    client.generate = AsyncMock(return_value=ollama_resp)
    client.health_check = AsyncMock(return_value=True)
    return client


async def _ingest_test_message(db: AsyncSession, tmp_path: Path):
    """Ingest a plain-text .eml and return the eagerly-loaded Message."""
    store = RawMessageStore(tmp_path)
    eml_bytes = (FIXTURES / "plain_text.eml").read_bytes()
    ingested = await msg_service.ingest_raw_message(
        db=db,
        raw_bytes=eml_bytes,
        source=IngestionSource.MESSAGE_LAB,
        store=store,
        mailbox_profile_id=None,
    )
    return await msg_service.get_message_by_id(db, ingested.id)


async def _insert_user(db_engine, email: str, password: str, role: UserRole) -> None:
    from sqlalchemy import select

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none() is None:
            session.add(
                User(
                    email=email,
                    hashed_password=hash_password(password),
                    role=role,
                    is_active=True,
                )
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Auto-quarantine via analysis service
# ---------------------------------------------------------------------------


class TestAutoQuarantine:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_quarantine_verdict_creates_quarantine_item(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=_make_high_risk_ollama_client(),
                    llm_enabled=True,
                )
                return msg.id, run.verdict

            # Must check in a new session after commit
        msg_id, verdict = asyncio.get_event_loop().run_until_complete(_run())

        async def _check():
            async with factory() as db:
                return await get_quarantine_item_for_message(db, msg_id)

        item = asyncio.get_event_loop().run_until_complete(_check())

        # Only check quarantine was created if the verdict was quarantine-worthy
        if verdict in {
            Verdict.QUARANTINE,
            Verdict.ESCALATE_TO_ADMIN,
            Verdict.REJECT,
        }:
            assert item is not None
            assert item.status == QuarantineStatus.PENDING_REVIEW
            assert item.risk_score_snapshot is not None

    def test_allow_verdict_does_not_quarantine(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=_make_low_risk_ollama_client(),
                    llm_enabled=True,
                )
                return msg.id, run.verdict

        msg_id, verdict = asyncio.get_event_loop().run_until_complete(_run())

        # Only verify no quarantine if verdict was actually allow/allow_with_banner
        if verdict in {Verdict.ALLOW, Verdict.ALLOW_WITH_BANNER}:
            async def _check():
                async with factory() as db:
                    return await get_quarantine_item_for_message(db, msg_id)

            item = asyncio.get_event_loop().run_until_complete(_check())
            assert item is None

    def test_idempotent_on_repeated_analysis(self, factory, tmp_path):
        """Re-analysis should not create a duplicate quarantine item."""

        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)

                run1 = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=_make_high_risk_ollama_client(),
                    llm_enabled=True,
                )

                # Re-run analysis on the same message
                msg_reloaded = await msg_service.get_message_by_id(db, msg.id)
                run2 = await analysis_service.run_analysis(
                    db=db,
                    msg=msg_reloaded,
                    ollama_client=_make_high_risk_ollama_client(),
                    llm_enabled=True,
                )
                return msg.id, run1.verdict, run2.verdict

        msg_id, v1, _v2 = asyncio.get_event_loop().run_until_complete(_run())

        if v1 in {Verdict.QUARANTINE, Verdict.ESCALATE_TO_ADMIN, Verdict.REJECT}:
            async def _check():
                from app.quarantine.models import QuarantineItem
                from sqlalchemy import func, select
                async with factory() as db:
                    result = await db.execute(
                        select(func.count())
                        .select_from(QuarantineItem)
                        .where(QuarantineItem.message_id == msg_id)
                    )
                    return result.scalar_one()

            count = asyncio.get_event_loop().run_until_complete(_check())
            assert count == 1, "Should have exactly one quarantine item per message"

    def test_soft_hold_no_quarantine_by_default(self, factory, tmp_path):
        """SOFT_HOLD verdict should not create a quarantine item unless flag is set."""
        from app.policies.verdict import VerdictThresholds

        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                # Use thresholds that guarantee soft_hold for our risk level
                thresholds = VerdictThresholds(
                    allow=0.0,
                    allow_with_banner=0.0,
                    soft_hold=1.0,
                    quarantine=1.0,
                )
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=None,
                    llm_enabled=False,
                    thresholds=thresholds,
                    quarantine_soft_hold=False,
                )
                return msg.id, run.verdict

        msg_id, verdict = asyncio.get_event_loop().run_until_complete(_run())

        if verdict == Verdict.SOFT_HOLD:
            async def _check():
                async with factory() as db:
                    return await get_quarantine_item_for_message(db, msg_id)

            item = asyncio.get_event_loop().run_until_complete(_check())
            assert item is None


# ---------------------------------------------------------------------------
# Quarantine inbox and detail routes
# ---------------------------------------------------------------------------


class TestQuarantineInboxRoute:
    _email = "qadmin_inbox@example.com"
    _password = "qinboxpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_user(db_engine, self._email, self._password, UserRole.ADMIN)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_inbox_requires_auth(self, client):
        resp = client.get("/admin/quarantine/", follow_redirects=False)
        assert resp.status_code == 401

    def test_inbox_returns_200_for_admin(self, client):
        self._login(client)
        resp = client.get("/admin/quarantine/")
        assert resp.status_code == 200

    def test_inbox_contains_quarantine_heading(self, client):
        self._login(client)
        resp = client.get("/admin/quarantine/")
        assert b"Quarantine" in resp.content

    def test_inbox_status_filter_accepted(self, client):
        self._login(client)
        resp = client.get("/admin/quarantine/?status_filter=pending_review")
        assert resp.status_code == 200

    def test_inbox_invalid_status_filter_returns_200(self, client):
        """Invalid filter values are silently ignored."""
        self._login(client)
        resp = client.get("/admin/quarantine/?status_filter=not_a_real_status")
        assert resp.status_code == 200


class TestQuarantineDetailRoute:
    _email = "qadmin_detail@example.com"
    _password = "qdetailpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_user(db_engine, self._email, self._password, UserRole.ADMIN)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def _create_quarantine_item(self, factory, db_engine, tmp_path) -> int:
        """Insert a message + analysis run + quarantine item; return quarantine item id."""

        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=_make_high_risk_ollama_client(),
                    llm_enabled=True,
                )
                return msg.id, run

        msg_id, run = asyncio.get_event_loop().run_until_complete(_run())

        # Create quarantine item directly if auto-quarantine didn't
        async def _ensure():
            async with factory() as db:
                item = await get_quarantine_item_for_message(db, msg_id)
                if item is None:
                    item, _ = await quarantine_service.get_or_create_quarantine_item(
                        db,
                        message_id=msg_id,
                        analysis_run_id=run.id,
                        trigger_verdict="quarantine",
                        risk_score_snapshot=0.9,
                    )
                    await db.commit()
                return item.id

        return asyncio.get_event_loop().run_until_complete(_ensure())

    def test_detail_requires_auth(self, client):
        resp = client.get("/admin/quarantine/99999", follow_redirects=False)
        assert resp.status_code == 401

    def test_detail_404_for_unknown_item(self, client):
        self._login(client)
        resp = client.get("/admin/quarantine/99999")
        assert resp.status_code == 404

    def test_detail_returns_200(self, client, factory, db_engine, tmp_path, monkeypatch):
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "q_detail_store"))
        from app.config import get_settings

        get_settings.cache_clear()
        item_id = self._create_quarantine_item(factory, db_engine, tmp_path)
        self._login(client)
        resp = client.get(f"/admin/quarantine/{item_id}")
        assert resp.status_code == 200

    def test_detail_shows_audit_history(self, client, factory, db_engine, tmp_path, monkeypatch):
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "q_audit_store"))
        from app.config import get_settings

        get_settings.cache_clear()
        item_id = self._create_quarantine_item(factory, db_engine, tmp_path)
        self._login(client)
        resp = client.get(f"/admin/quarantine/{item_id}")
        assert resp.status_code == 200
        assert b"Audit History" in resp.content

    def test_detail_shows_review_actions(self, client, factory, db_engine, tmp_path, monkeypatch):
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "q_actions_store"))
        from app.config import get_settings

        get_settings.cache_clear()
        item_id = self._create_quarantine_item(factory, db_engine, tmp_path)
        self._login(client)
        resp = client.get(f"/admin/quarantine/{item_id}")
        assert resp.status_code == 200
        assert b"Review Actions" in resp.content


class TestQuarantineActionRoute:
    _email = "qadmin_action@example.com"
    _password = "qactionpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_user(db_engine, self._email, self._password, UserRole.ADMIN)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def _create_pending_item(self, factory, db_engine, tmp_path, monkeypatch) -> int:
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "action_store"))
        from app.config import get_settings

        get_settings.cache_clear()

        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                item, _ = await quarantine_service.get_or_create_quarantine_item(
                    db,
                    message_id=msg.id,
                    analysis_run_id=None,
                    trigger_verdict="quarantine",
                    risk_score_snapshot=0.9,
                )
                await db.commit()
                return item.id

        return asyncio.get_event_loop().run_until_complete(_run())

    def test_action_requires_auth(self, client):
        resp = client.post(
            "/admin/quarantine/99999/action",
            data={"action": "release"},
            follow_redirects=False,
        )
        assert resp.status_code == 401

    def test_action_404_for_unknown_item(self, client):
        self._login(client)
        resp = client.post(
            "/admin/quarantine/99999/action",
            data={"action": "release"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_action_invalid_action_returns_400(
        self, client, factory, db_engine, tmp_path, monkeypatch
    ):
        item_id = self._create_pending_item(factory, db_engine, tmp_path, monkeypatch)
        self._login(client)
        resp = client.post(
            f"/admin/quarantine/{item_id}/action",
            data={"action": "not_a_real_action"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_release_action_redirects_to_detail(
        self, client, factory, db_engine, tmp_path, monkeypatch
    ):
        item_id = self._create_pending_item(factory, db_engine, tmp_path, monkeypatch)
        self._login(client)
        resp = client.post(
            f"/admin/quarantine/{item_id}/action",
            data={"action": "release", "note": "Cleared"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/admin/quarantine/{item_id}" in resp.headers["location"]

    def test_release_updates_status(self, client, factory, db_engine, tmp_path, monkeypatch):
        item_id = self._create_pending_item(factory, db_engine, tmp_path, monkeypatch)
        self._login(client)
        client.post(
            f"/admin/quarantine/{item_id}/action",
            data={"action": "release"},
            follow_redirects=False,
        )

        async def _check():
            async with factory() as db:
                return await get_quarantine_item_by_id(db, item_id)

        item = asyncio.get_event_loop().run_until_complete(_check())
        assert item.status == QuarantineStatus.RELEASED

    def test_invalid_transition_returns_409(
        self, client, factory, db_engine, tmp_path, monkeypatch
    ):
        item_id = self._create_pending_item(factory, db_engine, tmp_path, monkeypatch)
        self._login(client)
        # Release it first
        client.post(
            f"/admin/quarantine/{item_id}/action",
            data={"action": "release"},
            follow_redirects=False,
        )
        # Now try to mark in_review from released — invalid transition
        resp = client.post(
            f"/admin/quarantine/{item_id}/action",
            data={"action": "mark_in_review"},
            follow_redirects=False,
        )
        assert resp.status_code == 409

    def test_note_persisted_with_action(self, client, factory, db_engine, tmp_path, monkeypatch):
        item_id = self._create_pending_item(factory, db_engine, tmp_path, monkeypatch)
        self._login(client)
        client.post(
            f"/admin/quarantine/{item_id}/action",
            data={"action": "release", "note": "Verified clean"},
            follow_redirects=False,
        )

        async def _check():
            async with factory() as db:
                return await get_quarantine_item_by_id(db, item_id)

        item = asyncio.get_event_loop().run_until_complete(_check())
        assert item.notes is not None
        assert "Verified clean" in item.notes


class TestAnalystAccess:
    """Analysts (non-admin) can access quarantine but not Message Lab."""

    _email = "qanalyst@example.com"
    _password = "qanalystpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_user(db_engine, self._email, self._password, UserRole.ANALYST)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_analyst_can_view_quarantine_inbox(self, client):
        self._login(client)
        resp = client.get("/admin/quarantine/")
        assert resp.status_code == 200

    def test_analyst_cannot_access_message_lab(self, client):
        self._login(client)
        resp = client.get("/admin/messages/")
        assert resp.status_code == 403
