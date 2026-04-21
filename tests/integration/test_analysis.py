"""Integration tests for the Phase 4 analysis pipeline.

Covers:
  - Deterministic-only run (llm_enabled=False) via the service layer
  - LLM-enabled run with a mocked OllamaClient
  - Degraded mode when OllamaClient raises OllamaError
  - Degraded mode when LLM returns unparseable JSON (retry + fail)
  - POST /admin/messages/{id}/analyze route (mocks OllamaClient)
  - Detail page shows analysis section after analysis is run
  - get_latest_analysis returns most recent run
  - DimensionScore rows are persisted

All tests use the shared in-memory SQLite database and mock Ollama — no
live network or real Ollama instance required.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.analysis import service as analysis_service
from app.analysis.models import AnalysisStatus, ModelProvider
from app.analysis.ollama_client import OllamaClient, OllamaError, OllamaResponse
from app.auth.service import hash_password
from app.messages import service as msg_service
from app.messages.models import IngestionSource
from app.policies.constants import ManipulationDimension, Verdict
from app.users.models import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"

_ALL_DIM_SCORES = {d.value: 0.05 for d in ManipulationDimension}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_ollama_client(
    response_dict: dict | None = None,
    raise_error: bool = False,
) -> MagicMock:
    """Create a mock OllamaClient that returns a controlled response."""
    client = MagicMock(spec=OllamaClient)

    if raise_error:
        client.generate = AsyncMock(side_effect=OllamaError("Ollama unreachable"))
        client.health_check = AsyncMock(return_value=False)
        return client

    if response_dict is None:
        response_dict = {
            "overall_risk": 0.35,
            "dimension_scores": _ALL_DIM_SCORES,
            "summary": "Low-risk message detected by mock LLM.",
            "evidence": ["No suspicious signals found."],
            "recommended_action": "allow_with_banner",
            "confidence": 0.85,
        }

    raw_text = json.dumps(response_dict)
    ollama_resp = OllamaResponse(raw_text=raw_text, model="llama3.1:8b", done=True)
    client.generate = AsyncMock(return_value=ollama_resp)
    client.health_check = AsyncMock(return_value=True)
    return client


def _make_bad_json_ollama_client() -> MagicMock:
    """Mock client that always returns unparseable output (triggers retry + degrade)."""
    client = MagicMock(spec=OllamaClient)
    ollama_resp = OllamaResponse(
        raw_text="Sorry, I cannot provide a JSON response at this time.",
        model="llama3.1:8b",
        done=True,
    )
    client.generate = AsyncMock(return_value=ollama_resp)
    client.health_check = AsyncMock(return_value=True)
    return client


async def _ingest_test_message(db: AsyncSession, tmp_path: Path) -> Message:  # noqa: F821
    """Ingest a plain-text .eml file and return the ORM record."""
    from app.messages.storage import FileSystemRawMessageStore

    store = FileSystemRawMessageStore(tmp_path)
    eml_bytes = (FIXTURES / "plain_text.eml").read_bytes()
    return await msg_service.ingest_raw_message(
        db=db,
        raw_bytes=eml_bytes,
        source=IngestionSource.LAB,
        store=store,
        mailbox_profile_id=None,
    )


async def _insert_admin_user(db_engine, email: str, password: str) -> None:
    """Insert an admin user for route tests if they don't already exist."""
    from sqlalchemy import select

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none() is None:
            session.add(
                User(
                    email=email,
                    hashed_password=hash_password(password),
                    role=UserRole.ADMIN,
                    is_active=True,
                )
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Service: deterministic-only mode
# ---------------------------------------------------------------------------


class TestAnalysisServiceDeterministicOnly:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_run_analysis_no_llm_returns_complete_run(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=None,
                    llm_enabled=False,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        # When LLM is disabled, the run should still succeed
        assert run.id is not None
        assert run.status in (AnalysisStatus.COMPLETE, AnalysisStatus.DEGRADED)
        assert run.overall_risk_score is not None
        assert run.verdict is not None
        assert run.verdict in {v.value for v in Verdict}

    def test_run_analysis_no_llm_verdict_is_valid(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=None,
                    llm_enabled=False,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        valid_verdicts = {v.value for v in Verdict}
        assert run.verdict in valid_verdicts

    def test_run_analysis_persists_dimension_scores(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=None,
                    llm_enabled=False,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        # Dimension scores must be present (12 dimensions)
        assert len(run.dimension_scores) == len(ManipulationDimension)


# ---------------------------------------------------------------------------
# Service: LLM-enabled mode with mock client
# ---------------------------------------------------------------------------


class TestAnalysisServiceWithLLM:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_run_analysis_with_llm_uses_model_scores(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                mock_client = _make_mock_ollama_client()
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=mock_client,
                    llm_enabled=True,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        assert run.id is not None
        # The LLM returned 0.35 overall_risk; combined risk should be blend of det + llm
        assert run.llm_risk_score == pytest.approx(0.35, abs=0.01)
        assert run.status == AnalysisStatus.COMPLETE
        assert not run.is_degraded
        assert run.model_provider == ModelProvider.OLLAMA
        assert run.model_name == "llama3.1:8b"

    def test_run_analysis_llm_persists_rationale(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                mock_client = _make_mock_ollama_client()
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=mock_client,
                    llm_enabled=True,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        assert run.rationale is not None
        assert len(run.rationale) > 0

    def test_run_analysis_llm_stores_evidence_json(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                mock_client = _make_mock_ollama_client()
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=mock_client,
                    llm_enabled=True,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        assert run.evidence_json is not None
        evidence = json.loads(run.evidence_json)
        assert isinstance(evidence, list)


# ---------------------------------------------------------------------------
# Service: degraded mode
# ---------------------------------------------------------------------------


class TestAnalysisServiceDegradedMode:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_run_analysis_ollama_error_marks_degraded(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                mock_client = _make_mock_ollama_client(raise_error=True)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=mock_client,
                    llm_enabled=True,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        assert run.is_degraded is True
        assert run.status == AnalysisStatus.DEGRADED
        assert run.overall_risk_score is not None
        assert run.verdict is not None

    def test_run_analysis_bad_json_retries_then_degrades(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                mock_client = _make_bad_json_ollama_client()
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=mock_client,
                    llm_enabled=True,
                )
                return run, mock_client

        run, mock_client = asyncio.get_event_loop().run_until_complete(_run())
        # Should have called generate twice (original + retry)
        assert mock_client.generate.call_count == 2
        assert run.is_degraded is True

    def test_degraded_run_still_has_verdict(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                mock_client = _make_mock_ollama_client(raise_error=True)
                run = await analysis_service.run_analysis(
                    db=db,
                    msg=msg,
                    ollama_client=mock_client,
                    llm_enabled=True,
                )
                return run

        run = asyncio.get_event_loop().run_until_complete(_run())
        valid_verdicts = {v.value for v in Verdict}
        assert run.verdict in valid_verdicts


# ---------------------------------------------------------------------------
# Service: get_latest_analysis
# ---------------------------------------------------------------------------


class TestGetLatestAnalysis:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_get_latest_analysis_returns_none_when_no_run(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                return await analysis_service.get_latest_analysis(db, msg.id)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_get_latest_analysis_returns_most_recent(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                # Run analysis twice
                await analysis_service.run_analysis(
                    db=db, msg=msg, ollama_client=None, llm_enabled=False
                )
                run2 = await analysis_service.run_analysis(
                    db=db, msg=msg, ollama_client=None, llm_enabled=False
                )
                latest = await analysis_service.get_latest_analysis(db, msg.id)
                return latest, run2

        latest, run2 = asyncio.get_event_loop().run_until_complete(_run())
        assert latest is not None
        assert latest.id == run2.id

    def test_get_latest_analysis_loads_dimension_scores(self, factory, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await _ingest_test_message(db, tmp_path)
                await analysis_service.run_analysis(
                    db=db, msg=msg, ollama_client=None, llm_enabled=False
                )
                return await analysis_service.get_latest_analysis(db, msg.id)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is not None
        assert len(result.dimension_scores) == len(ManipulationDimension)


# ---------------------------------------------------------------------------
# Route: POST /admin/messages/{id}/analyze
# ---------------------------------------------------------------------------


class TestAnalyzeRoute:
    _email = "admin_analysis@example.com"
    _password = "analysispass123"

    @pytest.fixture(autouse=True)
    def _setup_admin(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_admin_user(db_engine, self._email, self._password)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def _upload_eml(self, client, tmp_path, monkeypatch) -> str:
        """Upload a test .eml file and return the detail URL."""
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "analysis_store"))
        from app.config import get_settings

        get_settings.cache_clear()
        eml_bytes = (FIXTURES / "plain_text.eml").read_bytes()
        resp = client.post(
            "/admin/messages/upload",
            files={"eml_file": ("plain_text.eml", eml_bytes, "message/rfc822")},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        return resp.headers["location"]

    def test_analyze_route_redirects_to_detail(self, client, tmp_path, monkeypatch):
        self._login(client)
        detail_url = self._upload_eml(client, tmp_path, monkeypatch)

        # Extract message ID from detail URL e.g. /admin/messages/3
        message_id = int(detail_url.rstrip("/").split("/")[-1])
        response = client.post(
            f"/admin/messages/{message_id}/analyze",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/admin/messages/{message_id}" in response.headers["location"]

    def test_analyze_route_detail_shows_analysis_section(self, client, tmp_path, monkeypatch):
        self._login(client)
        detail_url = self._upload_eml(client, tmp_path, monkeypatch)
        message_id = int(detail_url.rstrip("/").split("/")[-1])

        client.post(
            f"/admin/messages/{message_id}/analyze",
            follow_redirects=False,
        )
        detail_resp = client.get(f"/admin/messages/{message_id}")
        assert detail_resp.status_code == 200
        body = detail_resp.text
        # Analysis section header must be present
        assert "Security Analysis" in body
        # Should show overall risk and verdict
        assert "Overall Risk" in body
        assert "Verdict" in body

    def test_analyze_route_unauthenticated_returns_401(self, client, tmp_path, monkeypatch):
        with client as c:
            c.cookies.clear()
            response = c.post("/admin/messages/999/analyze", follow_redirects=False)
        assert response.status_code == 401

    def test_analyze_route_unknown_message_returns_404(self, client):
        self._login(client)
        response = client.post("/admin/messages/99999/analyze", follow_redirects=False)
        assert response.status_code == 404

    def test_detail_page_shows_analyze_button_before_analysis(
        self, client, tmp_path, monkeypatch
    ):
        self._login(client)
        detail_url = self._upload_eml(client, tmp_path, monkeypatch)
        detail_resp = client.get(detail_url)
        assert detail_resp.status_code == 200
        # Should have an Analyse button
        body = detail_resp.text
        assert "Analyse" in body or "analyze" in body.lower()
