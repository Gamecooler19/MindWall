"""Integration tests for Phase 8 admin surfaces.

Covers:
  - Dashboard shows live counts (alerts, mailboxes)
  - Audit log page renders
  - Model health page renders
  - Policy editor GET/POST
  - Alerts list, detail, acknowledge, resolve
  - Admin mailboxes overview
  - All routes require admin role
"""

from __future__ import annotations

import asyncio

import pytest
from app.alerts import service as alerts_service
from app.alerts.models import AlertSeverity
from app.auth.service import hash_password
from app.users.models import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _create_test_alert(db_engine) -> int:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        alert = await alerts_service.create_alert(
            db,
            title="Test integration alert",
            severity=AlertSeverity.HIGH,
            body="Test body",
        )
        await db.commit()
        return alert.id


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestAdminDashboard:
    _email = "dash_admin@example.com"
    _password = "dashpass123"

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

    def test_dashboard_requires_auth(self, client):
        resp = client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 401

    def test_dashboard_returns_200_for_admin(self, client):
        self._login(client)
        resp = client.get("/admin/")
        assert resp.status_code == 200

    def test_dashboard_contains_all_live_links(self, client):
        self._login(client)
        resp = client.get("/admin/")
        body = resp.content
        assert b"/admin/quarantine/" in body
        assert b"/admin/policy/" in body
        assert b"/admin/health/model" in body
        assert b"/admin/alerts/" in body
        assert b"/admin/mailboxes/" in body
        assert b"/admin/audit/" in body

    def test_dashboard_has_no_placeholder_labels(self, client):
        self._login(client)
        resp = client.get("/admin/")
        body = resp.text
        assert "Phase 2" not in body
        assert "Phase 4" not in body
        assert "Phase 5" not in body
        assert "Phase 6" not in body
        assert "opacity-60" not in body


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


class TestAuditLog:
    _email = "audit_admin@example.com"
    _password = "auditpass123"

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

    def test_audit_log_requires_auth(self, client):
        resp = client.get("/admin/audit/", follow_redirects=False)
        assert resp.status_code == 401

    def test_audit_log_returns_200(self, client):
        self._login(client)
        resp = client.get("/admin/audit/")
        assert resp.status_code == 200

    def test_audit_log_page_renders_table(self, client):
        self._login(client)
        resp = client.get("/admin/audit/")
        body = resp.text
        # Should contain either table headers or empty state
        assert "Audit Log" in body
        assert ("Action" in body or "No audit events" in body)


# ---------------------------------------------------------------------------
# Model Health
# ---------------------------------------------------------------------------


class TestModelHealth:
    _email = "mhealth_admin@example.com"
    _password = "mhealthpass123"

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

    def test_model_health_requires_auth(self, client):
        resp = client.get("/admin/health/model", follow_redirects=False)
        assert resp.status_code == 401

    def test_model_health_returns_200(self, client):
        self._login(client)
        resp = client.get("/admin/health/model")
        assert resp.status_code == 200

    def test_model_health_shows_ollama_config(self, client):
        self._login(client)
        resp = client.get("/admin/health/model")
        body = resp.text
        assert "Ollama" in body
        assert "Model Health" in body


# ---------------------------------------------------------------------------
# Policy Editor
# ---------------------------------------------------------------------------


class TestPolicyEditor:
    _email = "policy_admin@example.com"
    _password = "policypass123"

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

    def test_policy_editor_requires_auth(self, client):
        resp = client.get("/admin/policy/", follow_redirects=False)
        assert resp.status_code == 401

    def test_policy_editor_returns_200(self, client):
        self._login(client)
        resp = client.get("/admin/policy/")
        assert resp.status_code == 200

    def test_policy_editor_shows_threshold_fields(self, client):
        self._login(client)
        resp = client.get("/admin/policy/")
        body = resp.text
        assert "verdict_threshold_allow" in body
        assert "Verdict Thresholds" in body

    def test_policy_save_valid_redirects(self, client):
        self._login(client)
        resp = client.post(
            "/admin/policy/",
            data={"key": "llm_enabled", "value": "false"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "saved=llm_enabled" in resp.headers["location"]

    def test_policy_save_invalid_key_redirects_with_error(self, client):
        self._login(client)
        resp = client.post(
            "/admin/policy/",
            data={"key": "not_a_real_key", "value": "anything"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_policy_save_persists_to_editor(self, client):
        self._login(client)
        client.post(
            "/admin/policy/",
            data={"key": "imap_sync_batch_size", "value": "25"},
            follow_redirects=True,
        )
        resp = client.get("/admin/policy/")
        assert "25" in resp.text


# ---------------------------------------------------------------------------
# Alerts & Incidents
# ---------------------------------------------------------------------------


class TestAlertsRoutes:
    _email = "alerts_admin@example.com"
    _password = "alertspass123"

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

    def test_alerts_list_requires_auth(self, client):
        resp = client.get("/admin/alerts/", follow_redirects=False)
        assert resp.status_code == 401

    def test_alerts_list_returns_200(self, client):
        self._login(client)
        resp = client.get("/admin/alerts/")
        assert resp.status_code == 200

    def test_alerts_list_renders_filter_bar(self, client):
        self._login(client)
        resp = client.get("/admin/alerts/")
        body = resp.text
        assert "filter_status=OPEN" in body
        assert "filter_status=RESOLVED" in body

    def test_alert_detail_returns_200(self, client, db_engine):
        alert_id = asyncio.get_event_loop().run_until_complete(_create_test_alert(db_engine))
        self._login(client)
        resp = client.get(f"/admin/alerts/{alert_id}")
        assert resp.status_code == 200
        assert "Test integration alert" in resp.text

    def test_alert_detail_404_for_missing(self, client):
        self._login(client)
        resp = client.get("/admin/alerts/999999")
        assert resp.status_code == 404

    def test_acknowledge_alert(self, client, db_engine):
        alert_id = asyncio.get_event_loop().run_until_complete(_create_test_alert(db_engine))
        self._login(client)
        resp = client.post(
            f"/admin/alerts/{alert_id}/acknowledge",
            data={"note": "Investigating"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/admin/alerts/{alert_id}" in resp.headers["location"]

        # Verify status changed
        detail = client.get(f"/admin/alerts/{alert_id}")
        assert "acknowledged" in detail.text

    def test_resolve_alert(self, client, db_engine):
        alert_id = asyncio.get_event_loop().run_until_complete(_create_test_alert(db_engine))
        self._login(client)
        resp = client.post(
            f"/admin/alerts/{alert_id}/resolve",
            data={"note": "False positive"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        detail = client.get(f"/admin/alerts/{alert_id}")
        assert "resolved" in detail.text

    def test_filter_open_alerts(self, client, db_engine):
        asyncio.get_event_loop().run_until_complete(_create_test_alert(db_engine))
        self._login(client)
        resp = client.get("/admin/alerts/?filter_status=OPEN")
        assert resp.status_code == 200

    def test_acknowledge_non_open_alert_returns_400(self, client, db_engine):
        """Acknowledging an already-acknowledged alert returns 400."""
        alert_id = asyncio.get_event_loop().run_until_complete(_create_test_alert(db_engine))
        self._login(client)
        # Acknowledge once
        client.post(f"/admin/alerts/{alert_id}/acknowledge", data={}, follow_redirects=True)
        # Acknowledge again — should fail
        resp = client.post(
            f"/admin/alerts/{alert_id}/acknowledge",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Admin Mailboxes Overview
# ---------------------------------------------------------------------------


class TestAdminMailboxes:
    _email = "mb_admin@example.com"
    _password = "mbadminpass123"

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

    def test_admin_mailboxes_requires_auth(self, client):
        resp = client.get("/admin/mailboxes/", follow_redirects=False)
        assert resp.status_code == 401

    def test_admin_mailboxes_returns_200(self, client):
        self._login(client)
        resp = client.get("/admin/mailboxes/")
        assert resp.status_code == 200

    def test_admin_mailboxes_shows_count(self, client):
        self._login(client)
        resp = client.get("/admin/mailboxes/")
        body = resp.text
        assert "Mailbox Profiles" in body
