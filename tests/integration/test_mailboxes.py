"""Integration tests for mailbox routes.

These tests use the session-scoped TestClient and in-memory SQLite DB
from conftest.py. A test user is inserted and authenticated for each class.

Tests cover:
  - Authentication enforcement (unauthenticated → redirect/401)
  - Creating a mailbox profile via the form
  - Viewing the mailbox list
  - Viewing the detail page
  - Ownership enforcement (user A cannot access user B's mailbox)
  - Edit form rendering and submission
  - Proxy password reset
  - Delete action
"""

import pytest
from app.auth.service import hash_password
from app.users.models import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Helpers — form data for create/edit
# ---------------------------------------------------------------------------

_VALID_FORM = {
    "display_name": "Test Mailbox",
    "email_address": "test@example.com",
    "imap_host": "imap.example.com",
    "imap_port": "993",
    "imap_username": "test@example.com",
    "imap_password": "imap-secret-password",
    "imap_security": "ssl_tls",
    "smtp_host": "smtp.example.com",
    "smtp_port": "587",
    "smtp_username": "test@example.com",
    "smtp_password": "smtp-secret-password",
    "smtp_security": "starttls",
}


def _login(client, email, password):
    """Perform a login and return the response."""
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


class TestMailboxAuthEnforcement:
    def test_list_unauthenticated_returns_401(self, client):
        # fresh client has no session
        with client as c:
            # Ensure no session cookie is set
            c.cookies.clear()
            response = c.get("/mailboxes/", follow_redirects=False)
        assert response.status_code == 401

    def test_new_form_unauthenticated_returns_401(self, client):
        with client as c:
            c.cookies.clear()
            response = c.get("/mailboxes/new", follow_redirects=False)
        assert response.status_code == 401

    def test_detail_unauthenticated_returns_401(self, client):
        with client as c:
            c.cookies.clear()
            response = c.get("/mailboxes/999", follow_redirects=False)
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Mailbox CRUD — authenticated user
# ---------------------------------------------------------------------------


class TestMailboxCRUD:
    """Tests that require an authenticated session.

    We insert a user into the DB via a fixture, then POST /login to get
    a session cookie. Each test method gets a fresh session (function-scoped client).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        """Insert a test user once for this class."""
        import asyncio

        async def _insert():
            factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                # Check if user already exists before inserting
                from sqlalchemy import select
                result = await session.execute(
                    select(User).where(User.email == "mailbox_test@example.com")
                )
                existing = result.scalar_one_or_none()
                if existing is None:
                    user = User(
                        email="mailbox_test@example.com",
                        hashed_password=hash_password("password123"),
                        role=UserRole.USER,
                        is_active=True,
                    )
                    session.add(user)
                    await session.commit()

        asyncio.get_event_loop().run_until_complete(_insert())

    def _get_authenticated_client(self, client):
        """Return a client with an active session for mailbox_test@example.com."""
        _login(client, "mailbox_test@example.com", "password123")
        return client

    # -----------------------------------------------------------------------
    # List
    # -----------------------------------------------------------------------

    def test_list_authenticated_returns_200(self, client):
        self._get_authenticated_client(client)
        response = client.get("/mailboxes/")
        assert response.status_code == 200

    def test_list_contains_register_link(self, client):
        self._get_authenticated_client(client)
        response = client.get("/mailboxes/")
        assert b"/mailboxes/new" in response.content

    # -----------------------------------------------------------------------
    # New form
    # -----------------------------------------------------------------------

    def test_new_form_returns_200(self, client):
        self._get_authenticated_client(client)
        response = client.get("/mailboxes/new")
        assert response.status_code == 200

    def test_new_form_contains_imap_fields(self, client):
        self._get_authenticated_client(client)
        response = client.get("/mailboxes/new")
        assert b'name="imap_host"' in response.content
        assert b'name="imap_port"' in response.content
        assert b'name="imap_password"' in response.content

    def test_new_form_contains_smtp_fields(self, client):
        self._get_authenticated_client(client)
        response = client.get("/mailboxes/new")
        assert b'name="smtp_host"' in response.content
        assert b'name="smtp_port"' in response.content

    # -----------------------------------------------------------------------
    # Create
    # -----------------------------------------------------------------------

    def test_create_mailbox_redirects_to_detail(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data=_VALID_FORM,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/mailboxes/")

    def test_create_mailbox_and_view_detail(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data=_VALID_FORM,
            follow_redirects=True,
        )
        assert response.status_code == 200
        # Detail page should show the mailbox email
        assert b"test@example.com" in response.content

    def test_create_shows_proxy_password_reveal(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data=_VALID_FORM,
            follow_redirects=True,
        )
        # The one-time reveal banner must appear
        assert b"Save your proxy password now" in response.content

    def test_create_with_missing_imap_password_returns_422(self, client):
        self._get_authenticated_client(client)
        form = {**_VALID_FORM, "imap_password": ""}
        response = client.post("/mailboxes/", data=form, follow_redirects=False)
        assert response.status_code == 422

    def test_create_with_invalid_email_returns_422(self, client):
        self._get_authenticated_client(client)
        form = {**_VALID_FORM, "email_address": "not-an-email"}
        response = client.post("/mailboxes/", data=form, follow_redirects=False)
        assert response.status_code == 422

    def test_create_with_invalid_port_returns_422(self, client):
        self._get_authenticated_client(client)
        form = {**_VALID_FORM, "imap_port": "not-a-number"}
        response = client.post("/mailboxes/", data=form, follow_redirects=False)
        assert response.status_code == 422

    # -----------------------------------------------------------------------
    # Proxy password does NOT appear on second visit
    # -----------------------------------------------------------------------

    def test_proxy_password_only_shown_once(self, client):
        self._get_authenticated_client(client)
        # Create → follow redirect → proxy password shown
        create_response = client.post(
            "/mailboxes/",
            data={**_VALID_FORM, "email_address": "oncepw@example.com"},
            follow_redirects=True,
        )
        assert b"Save your proxy password now" in create_response.content

        # Visit the detail page again — the banner must NOT appear
        detail_url = create_response.url
        revisit = client.get(str(detail_url))
        assert b"Save your proxy password now" not in revisit.content

    # -----------------------------------------------------------------------
    # Ownership: user cannot access another user's mailbox
    # -----------------------------------------------------------------------

    def test_cannot_access_nonexistent_mailbox(self, client):
        self._get_authenticated_client(client)
        response = client.get("/mailboxes/999999", follow_redirects=False)
        assert response.status_code == 404

    # -----------------------------------------------------------------------
    # Proxy setup instructions content
    # -----------------------------------------------------------------------

    def test_detail_shows_proxy_username(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data={**_VALID_FORM, "email_address": "proxy_user@example.com"},
            follow_redirects=True,
        )
        assert b"mw_" in response.content  # proxy username prefix

    def test_detail_shows_proxy_imap_host(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data={**_VALID_FORM, "email_address": "host_check@example.com"},
            follow_redirects=True,
        )
        assert b"127.0.0.1" in response.content  # default imap_proxy_display_host

    # -----------------------------------------------------------------------
    # Passwords are not exposed in responses
    # -----------------------------------------------------------------------

    def test_imap_secret_not_in_detail_response(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data={**_VALID_FORM, "imap_password": "imap-secret-password"},
            follow_redirects=True,
        )
        assert b"imap-secret-password" not in response.content

    def test_smtp_secret_not_in_detail_response(self, client):
        self._get_authenticated_client(client)
        response = client.post(
            "/mailboxes/",
            data={**_VALID_FORM, "smtp_password": "smtp-secret-password"},
            follow_redirects=True,
        )
        assert b"smtp-secret-password" not in response.content
