"""Integration tests for the admin Message Lab routes.

These tests verify:
  - Authentication and admin-role enforcement
  - Upload form rendering
  - .eml file upload and ingestion
  - Detail page rendering
  - Oversized / empty upload rejection
  - 404 handling for unknown message IDs
"""

from pathlib import Path

import pytest
from app.auth.service import hash_password
from app.users.models import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client, email: str, password: str):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def _load_eml(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---------------------------------------------------------------------------
# Auth enforcement — unauthenticated requests
# ---------------------------------------------------------------------------


class TestMessageLabAuthEnforcement:
    def test_list_unauthenticated_returns_401(self, client):
        with client as c:
            c.cookies.clear()
            response = c.get("/admin/messages/", follow_redirects=False)
        assert response.status_code == 401

    def test_upload_form_unauthenticated_returns_401(self, client):
        with client as c:
            c.cookies.clear()
            response = c.get("/admin/messages/upload", follow_redirects=False)
        assert response.status_code == 401

    def test_upload_post_unauthenticated_returns_401(self, client):
        with client as c:
            c.cookies.clear()
            response = c.post(
                "/admin/messages/upload",
                files={"eml_file": ("test.eml", b"fake", "message/rfc822")},
                follow_redirects=False,
            )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Admin-role enforcement — non-admin user blocked
# ---------------------------------------------------------------------------


class TestMessageLabRoleEnforcement:
    @pytest.fixture(autouse=True)
    def _setup_regular_user(self, app, db_engine):
        import asyncio

        async def _insert():
            factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                from sqlalchemy import select

                result = await session.execute(
                    select(User).where(User.email == "regular_msg_lab@example.com")
                )
                if result.scalar_one_or_none() is None:
                    session.add(
                        User(
                            email="regular_msg_lab@example.com",
                            hashed_password=hash_password("password123"),
                            role=UserRole.USER,
                            is_active=True,
                        )
                    )
                    await session.commit()

        asyncio.get_event_loop().run_until_complete(_insert())

    def test_list_regular_user_returns_403(self, client):
        _login(client, "regular_msg_lab@example.com", "password123")
        response = client.get("/admin/messages/", follow_redirects=False)
        assert response.status_code == 403

    def test_upload_form_regular_user_returns_403(self, client):
        _login(client, "regular_msg_lab@example.com", "password123")
        response = client.get("/admin/messages/upload", follow_redirects=False)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Admin user flows
# ---------------------------------------------------------------------------


class TestMessageLabAdmin:
    @pytest.fixture(autouse=True)
    def _setup_admin_user(self, app, db_engine):
        import asyncio

        async def _insert():
            factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                from sqlalchemy import select

                result = await session.execute(
                    select(User).where(User.email == "admin_msg_lab@example.com")
                )
                if result.scalar_one_or_none() is None:
                    session.add(
                        User(
                            email="admin_msg_lab@example.com",
                            hashed_password=hash_password("password123"),
                            role=UserRole.ADMIN,
                            is_active=True,
                        )
                    )
                    await session.commit()

        asyncio.get_event_loop().run_until_complete(_insert())

    def _auth(self, client):
        _login(client, "admin_msg_lab@example.com", "password123")
        return client

    # -----------------------------------------------------------------------
    # List page
    # -----------------------------------------------------------------------

    def test_list_page_returns_200(self, client):
        self._auth(client)
        response = client.get("/admin/messages/")
        assert response.status_code == 200

    def test_list_page_contains_upload_link(self, client):
        self._auth(client)
        response = client.get("/admin/messages/")
        assert b"/admin/messages/upload" in response.content

    # -----------------------------------------------------------------------
    # Upload form
    # -----------------------------------------------------------------------

    def test_upload_form_returns_200(self, client):
        self._auth(client)
        response = client.get("/admin/messages/upload")
        assert response.status_code == 200

    def test_upload_form_contains_file_input(self, client):
        self._auth(client)
        response = client.get("/admin/messages/upload")
        assert b'type="file"' in response.content

    # -----------------------------------------------------------------------
    # Upload .eml — success
    # -----------------------------------------------------------------------

    def test_upload_plain_text_eml_redirects(self, client, tmp_path, monkeypatch):
        # Override the raw message store path to use tmp_path
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "raw_msgs"))
        from app.config import get_settings
        get_settings.cache_clear()

        self._auth(client)
        eml_bytes = _load_eml("plain_text.eml")
        response = client.post(
            "/admin/messages/upload",
            files={"eml_file": ("plain_text.eml", eml_bytes, "message/rfc822")},
            follow_redirects=False,
        )
        # Should redirect to detail page on success
        assert response.status_code in (302, 303)
        assert "/admin/messages/" in response.headers["location"]

    def test_uploaded_message_detail_accessible(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "raw_msgs2"))
        from app.config import get_settings
        get_settings.cache_clear()

        self._auth(client)
        eml_bytes = _load_eml("plain_text.eml")
        upload_resp = client.post(
            "/admin/messages/upload",
            files={"eml_file": ("plain_text.eml", eml_bytes, "message/rfc822")},
            follow_redirects=False,
        )
        assert upload_resp.status_code in (302, 303)

        # Follow redirect to detail page
        detail_url = upload_resp.headers["location"]
        detail_resp = client.get(detail_url, follow_redirects=True)
        assert detail_resp.status_code == 200
        assert b"plain_text" in detail_resp.content or b"Plain text" in detail_resp.content

    def test_uploaded_multipart_message_shows_urls(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "raw_msgs3"))
        from app.config import get_settings
        get_settings.cache_clear()

        self._auth(client)
        eml_bytes = _load_eml("multipart.eml")
        upload_resp = client.post(
            "/admin/messages/upload",
            files={"eml_file": ("multipart.eml", eml_bytes, "message/rfc822")},
            follow_redirects=False,
        )
        assert upload_resp.status_code in (302, 303)
        detail_url = upload_resp.headers["location"]
        detail_resp = client.get(detail_url, follow_redirects=True)
        assert detail_resp.status_code == 200
        # The page should list extracted URLs
        assert b"example.com" in detail_resp.content

    # -----------------------------------------------------------------------
    # Upload validation errors
    # -----------------------------------------------------------------------

    def test_empty_upload_returns_upload_form_with_error(self, client):
        self._auth(client)
        response = client.post(
            "/admin/messages/upload",
            files={"eml_file": ("empty.eml", b"", "message/rfc822")},
            follow_redirects=True,
        )
        assert response.status_code == 200
        # Should re-render the upload form with an error
        assert b"empty" in response.content.lower() or b"upload" in response.content.lower()

    def test_oversized_upload_returns_upload_form_with_error(self, client, tmp_path, monkeypatch):
        # Send a file larger than the configured limit (default 25 MB)
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "raw_msgs_big"))
        from app.config import get_settings
        get_settings.cache_clear()

        self._auth(client)
        # Send 26 MB (exceeds default 25 MB limit)
        oversized = b"X" * (26 * 1024 * 1024)
        response = client.post(
            "/admin/messages/upload",
            files={"eml_file": ("big.eml", oversized, "message/rfc822")},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"upload" in response.content.lower()

    # -----------------------------------------------------------------------
    # Detail — 404 for nonexistent message
    # -----------------------------------------------------------------------

    def test_detail_404_for_nonexistent_message(self, client):
        self._auth(client)
        response = client.get("/admin/messages/999999", follow_redirects=False)
        assert response.status_code == 404
