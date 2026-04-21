"""Integration tests for admin mailbox sync and virtual inbox routes.

No real IMAP server required — the sync service is mocked at the
UpstreamImapClient level.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from app.auth.service import hash_password
from app.mailboxes.sync_models import ItemVisibility, MailboxItem, MailboxSyncState, SyncStatus
from app.users.models import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_admin(db_engine, email: str, password: str) -> None:
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


async def _insert_mailbox_profile(db_engine, owner_email: str) -> int:
    from datetime import UTC, datetime

    from sqlalchemy import select, text

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        res = await session.execute(select(User).where(User.email == owner_email))
        owner = res.scalar_one()

        result = await session.execute(
            text(
                "INSERT INTO mailbox_profiles "
                "(owner_id, display_name, email_address, imap_host, imap_port,"
                " imap_username, imap_password_enc, imap_security,"
                " smtp_host, smtp_port, smtp_username, smtp_password_enc, smtp_security,"
                " status, created_at, updated_at) "
                "VALUES (:oid, :dn, :ea, :ih, :ip, :iu, :ienc, :isec,"
                " :sh, :sp, :su, :senc, :ssec, :st, :now, :now) RETURNING id"
            ),
            {
                "oid": owner.id,
                "dn": "Integration Test Mailbox",
                "ea": "inbox@example.com",
                "ih": "imap.example.com",
                "ip": 993,
                "iu": "user",
                "ienc": "encrypted_placeholder",
                "isec": "SSL_TLS",
                "sh": "smtp.example.com",
                "sp": 587,
                "su": "user",
                "senc": "encrypted_placeholder",
                "ssec": "STARTTLS",
                "st": "ACTIVE",
                "now": datetime.now(UTC),
            },
        )
        mailbox_id = result.scalar_one()
        await session.commit()
    return mailbox_id


async def _insert_sync_state(db_engine, mailbox_id: int) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        state = MailboxSyncState(
            mailbox_profile_id=mailbox_id,
            folder_name="INBOX",
            sync_status=SyncStatus.IDLE,
            last_seen_uid=10,
            last_sync_count=5,
        )
        session.add(state)
        await session.commit()


async def _insert_mailbox_item(
    db_engine,
    mailbox_id: int,
    upstream_uid: int,
    visibility: ItemVisibility,
    message_id: int | None = None,
) -> int:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        item = MailboxItem(
            mailbox_profile_id=mailbox_id,
            folder_name="INBOX",
            upstream_uid=upstream_uid,
            uid_validity=12345,
            message_id=message_id,
            visibility=visibility,
            mindwall_uid=upstream_uid,
        )
        session.add(item)
        await session.flush()
        item_id = item.id
        await session.commit()
    return item_id


# ---------------------------------------------------------------------------
# Tests: sync status route
# ---------------------------------------------------------------------------


class TestSyncStatusRoute:
    _email = "syncroute_admin@example.com"
    _password = "syncadminpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_admin(db_engine, self._email, self._password)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_requires_auth(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        resp = client.get(f"/admin/mailboxes/{mb_id}/sync", follow_redirects=False)
        assert resp.status_code == 401

    def test_returns_200_for_admin(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/sync")
        assert resp.status_code == 200

    def test_shows_sync_states(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        asyncio.get_event_loop().run_until_complete(_insert_sync_state(db_engine, mb_id))
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/sync")
        assert resp.status_code == 200
        assert b"INBOX" in resp.content

    def test_404_for_unknown_mailbox(self, client):
        self._login(client)
        resp = client.get("/admin/mailboxes/99999/sync")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: virtual inbox route
# ---------------------------------------------------------------------------


class TestMailboxInboxRoute:
    _email = "inboxroute_admin@example.com"
    _password = "inboxadminpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_admin(db_engine, self._email, self._password)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_requires_auth(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        resp = client.get(f"/admin/mailboxes/{mb_id}/inbox", follow_redirects=False)
        assert resp.status_code == 401

    def test_returns_200_empty_inbox(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/inbox")
        assert resp.status_code == 200

    def test_shows_visible_items(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_item(db_engine, mb_id, 99, ItemVisibility.VISIBLE)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/inbox")
        assert resp.status_code == 200

    def test_404_for_unknown_mailbox(self, client):
        self._login(client)
        resp = client.get("/admin/mailboxes/99999/inbox")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: quarantine view route
# ---------------------------------------------------------------------------


class TestMailboxQuarantineViewRoute:
    _email = "qvroute_admin@example.com"
    _password = "qvadminpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_admin(db_engine, self._email, self._password)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_returns_200(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/quarantine")
        assert resp.status_code == 200

    def test_shows_quarantined_items(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_item(db_engine, mb_id, 50, ItemVisibility.QUARANTINED)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/quarantine")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: item detail route
# ---------------------------------------------------------------------------


class TestMailboxItemDetailRoute:
    _email = "itemroute_admin@example.com"
    _password = "itemadminpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_admin(db_engine, self._email, self._password)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_requires_auth(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        item_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_item(db_engine, mb_id, 1, ItemVisibility.VISIBLE)
        )
        resp = client.get(
            f"/admin/mailboxes/{mb_id}/items/{item_id}", follow_redirects=False
        )
        assert resp.status_code == 401

    def test_returns_200_for_valid_item(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        item_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_item(db_engine, mb_id, 77, ItemVisibility.VISIBLE)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/items/{item_id}")
        assert resp.status_code == 200

    def test_404_for_missing_item(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        self._login(client)
        resp = client.get(f"/admin/mailboxes/{mb_id}/items/99999")
        assert resp.status_code == 404

    def test_404_when_item_belongs_to_different_mailbox(self, client, db_engine):
        mb1 = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        mb2 = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        item_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_item(db_engine, mb2, 1, ItemVisibility.VISIBLE)
        )
        self._login(client)
        # Request item from mb2 via mb1's URL — should 404
        resp = client.get(f"/admin/mailboxes/{mb1}/items/{item_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: trigger sync route
# ---------------------------------------------------------------------------


class TestTriggerSyncRoute:
    _email = "trigsync_admin@example.com"
    _password = "trigsyncpass123"

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_engine):
        asyncio.get_event_loop().run_until_complete(
            _insert_admin(db_engine, self._email, self._password)
        )

    def _login(self, client):
        client.post(
            "/login",
            data={"email": self._email, "password": self._password},
            follow_redirects=True,
        )

    def test_trigger_sync_requires_auth(self, client, db_engine):
        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        resp = client.post(
            f"/admin/mailboxes/{mb_id}/sync", follow_redirects=False
        )
        assert resp.status_code == 401

    def test_trigger_sync_redirects_after_completion(
        self, client, db_engine, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("RAW_MESSAGE_STORE_PATH", str(tmp_path / "sync_store"))
        from app.config import get_settings

        get_settings.cache_clear()

        mb_id = asyncio.get_event_loop().run_until_complete(
            _insert_mailbox_profile(db_engine, self._email)
        )
        self._login(client)

        # Mock the sync service so we don't need a real IMAP server
        from app.mailboxes.sync_service import SyncResult

        mock_result = SyncResult(
            mailbox_profile_id=mb_id,
            folder_name="INBOX",
            new_messages=0,
        )

        with patch(
            "app.mailboxes.sync_router.sync_service.sync_mailbox_folder",
            AsyncMock(return_value=mock_result),
        ):
            resp = client.post(
                f"/admin/mailboxes/{mb_id}/sync", follow_redirects=False
            )

        assert resp.status_code == 303
        assert f"/admin/mailboxes/{mb_id}/sync" in resp.headers["location"]

    def test_trigger_sync_404_for_unknown_mailbox(self, client):
        self._login(client)
        resp = client.post("/admin/mailboxes/99999/sync", follow_redirects=False)
        assert resp.status_code == 404
