"""Unit tests for the mailbox sync service.

Uses the in-memory SQLite test DB — no real IMAP connection required.
The UpstreamImapClient is mocked throughout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.mailboxes.sync_models import ItemVisibility, MailboxItem, SyncStatus
from app.mailboxes.sync_service import SyncResult, get_sync_state
from app.proxies.imap.client import UpstreamImapError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"
_IMAP_CLIENT_PATCH = "app.mailboxes.sync_service.UpstreamImapClient"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_test_user(factory, email: str = "synctest@example.com") -> int:
    from datetime import UTC, datetime

    from sqlalchemy import text

    async with factory() as db:
        result = await db.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, role, is_active, created_at, updated_at)"
                " VALUES (:e, :h, :r, :a, :now, :now) RETURNING id"
            ),
            {
                "e": email,
                "h": "$2b$12$fakehash",
                "r": "ADMIN",
                "a": True,
                "now": datetime.now(UTC),
            },
        )
        user_id = result.scalar_one()
        await db.commit()
    return user_id


async def _insert_test_mailbox(factory, owner_id: int) -> int:
    from datetime import UTC, datetime

    from sqlalchemy import text

    async with factory() as db:
        result = await db.execute(
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
                "oid": owner_id,
                "dn": "Test Mailbox",
                "ea": f"user{owner_id}@example.com",
                "ih": "imap.example.com",
                "ip": 993,
                "iu": "user@example.com",
                "ienc": "gAAAAAA=",  # dummy encrypted value
                "isec": "SSL_TLS",
                "sh": "smtp.example.com",
                "sp": 587,
                "su": "user@example.com",
                "senc": "gAAAAAA=",
                "ssec": "STARTTLS",
                "st": "ACTIVE",
                "now": datetime.now(UTC),
            },
        )
        mailbox_id = result.scalar_one()
        await db.commit()
    return mailbox_id


def _make_mock_imap_client(uids: list[int], raw_bytes: bytes) -> MagicMock:
    """Build a mock UpstreamImapClient."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.connect = AsyncMock()
    client.logout = AsyncMock()
    client.select_folder = AsyncMock(return_value=(12345, uids))
    client.fetch_uids_in_range = AsyncMock(return_value=uids)
    client.fetch_raw_message = AsyncMock(return_value=raw_bytes)
    return client


# ---------------------------------------------------------------------------
# Test: get_sync_state
# ---------------------------------------------------------------------------


class TestGetSyncState:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_creates_new_state_on_first_call(self, factory):
        async def _run():
            user_id = await _insert_test_user(factory, "syncstate1@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)

            async with factory() as db:
                state = await get_sync_state(db, mailbox_id, "INBOX")
                await db.commit()
            return state.id, state.sync_status, state.folder_name

        state_id, status, folder = asyncio.get_event_loop().run_until_complete(_run())
        assert state_id is not None
        assert status == SyncStatus.IDLE
        assert folder == "INBOX"

    def test_returns_existing_state_on_second_call(self, factory):
        async def _run():
            user_id = await _insert_test_user(factory, "syncstate2@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)

            async with factory() as db:
                s1 = await get_sync_state(db, mailbox_id, "INBOX")
                await db.commit()
            async with factory() as db:
                s2 = await get_sync_state(db, mailbox_id, "INBOX")
                await db.commit()
            return s1.id, s2.id

        id1, id2 = asyncio.get_event_loop().run_until_complete(_run())
        assert id1 == id2

    def test_different_folders_get_different_rows(self, factory):
        async def _run():
            user_id = await _insert_test_user(factory, "syncstate3@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)

            async with factory() as db:
                s1 = await get_sync_state(db, mailbox_id, "INBOX")
                await db.commit()
            async with factory() as db:
                s2 = await get_sync_state(db, mailbox_id, "Sent")
                await db.commit()
            return s1.id, s2.id

        id1, id2 = asyncio.get_event_loop().run_until_complete(_run())
        assert id1 != id2


# ---------------------------------------------------------------------------
# Test: sync orchestrator
# ---------------------------------------------------------------------------


class TestSyncMailboxFolder:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def _get_eml(self) -> bytes:
        return (FIXTURES / "plain_text.eml").read_bytes()

    def _make_profile(self, mailbox_id: int, imap_host: str = "imap.example.com") -> MagicMock:
        """Build a mock MailboxProfile-like object."""
        profile = MagicMock()
        profile.id = mailbox_id
        profile.imap_host = imap_host
        profile.imap_port = 993
        profile.imap_security = "ssl_tls"
        profile.imap_username = "user@example.com"
        profile.imap_password_enc = "encrypted"
        return profile

    def _make_encryptor(self) -> MagicMock:
        enc = MagicMock()
        enc.decrypt.return_value = "plaintext-password"
        return enc

    def test_new_messages_are_ingested(self, factory, tmp_path):
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        raw = self._get_eml()
        mock_client = _make_mock_imap_client(uids=[1], raw_bytes=raw)

        async def _run():
            user_id = await _insert_test_user(factory, "syncnew@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            async with factory() as db:
                with patch(_IMAP_CLIENT_PATCH, return_value=mock_client):
                    result = await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        batch_size=50,
                        imap_timeout=5,
                        llm_enabled=False,
                    )
            return result, mailbox_id

        result, mailbox_id = asyncio.get_event_loop().run_until_complete(_run())
        assert isinstance(result, SyncResult)
        assert result.new_messages == 1
        assert result.errors == 0
        assert result.failed_auth is False

        # Verify MailboxItem was created
        async def _check():
            async with factory() as db:
                from sqlalchemy import select

                res = await db.execute(
                    select(MailboxItem).where(MailboxItem.mailbox_profile_id == mailbox_id)
                )
                return list(res.scalars().all())

        items = asyncio.get_event_loop().run_until_complete(_check())
        assert len(items) == 1
        assert items[0].upstream_uid == 1
        assert items[0].visibility != ItemVisibility.INGESTION_ERROR

    def test_idempotent_repeated_sync(self, factory, tmp_path):
        """Syncing the same UID twice does not create a duplicate MailboxItem."""
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        raw = self._get_eml()
        mock_client = _make_mock_imap_client(uids=[5], raw_bytes=raw)

        async def _run():
            user_id = await _insert_test_user(factory, "syncidem@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            # First sync
            async with factory() as db:
                with patch(_IMAP_CLIENT_PATCH, return_value=mock_client):
                    r1 = await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        batch_size=50,
                        imap_timeout=5,
                        llm_enabled=False,
                    )

            # Second sync — same UID should be skipped
            mock_client2 = _make_mock_imap_client(uids=[5], raw_bytes=raw)
            mock_client2.fetch_uids_in_range = AsyncMock(return_value=[])

            async with factory() as db:
                with patch(_IMAP_CLIENT_PATCH, return_value=mock_client2):
                    r2 = await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        batch_size=50,
                        imap_timeout=5,
                        llm_enabled=False,
                    )

            return r1, r2, mailbox_id

        r1, r2, mailbox_id = asyncio.get_event_loop().run_until_complete(_run())
        assert r1.new_messages == 1
        assert r2.new_messages == 0

        async def _count():
            from sqlalchemy import select

            async with factory() as db:
                res = await db.execute(
                    select(MailboxItem).where(MailboxItem.mailbox_profile_id == mailbox_id)
                )
                return len(list(res.scalars().all()))

        count = asyncio.get_event_loop().run_until_complete(_count())
        assert count == 1

    def test_auth_failure_records_error_status(self, factory, tmp_path):
        """When IMAP auth fails, sync state is ERROR and failed_auth is True."""
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        async def _run():
            user_id = await _insert_test_user(factory, "syncauth@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            failing_client = MagicMock()
            failing_client.connect = AsyncMock(
                side_effect=UpstreamImapError("IMAP authentication failed")
            )
            failing_client.logout = AsyncMock()

            async with factory() as db:
                with patch(
                    "app.mailboxes.sync_service.UpstreamImapClient",
                    return_value=failing_client,
                ):
                    result = await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        imap_timeout=5,
                        llm_enabled=False,
                    )
            return result, mailbox_id

        result, mailbox_id = asyncio.get_event_loop().run_until_complete(_run())
        assert result.failed_auth is True
        assert result.new_messages == 0

        # Sync state should be ERROR
        async def _check_state():
            async with factory() as db:
                return await get_sync_state(db, mailbox_id, "INBOX")

        state = asyncio.get_event_loop().run_until_complete(_check_state())
        assert state.sync_status == SyncStatus.ERROR
        assert state.last_error is not None

    def test_connection_failure_records_error(self, factory, tmp_path):
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        async def _run():
            user_id = await _insert_test_user(factory, "syncconn@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            failing_client = MagicMock()
            failing_client.connect = AsyncMock(
                side_effect=UpstreamImapError("IMAP connection refused")
            )
            failing_client.logout = AsyncMock()

            async with factory() as db:
                with patch(
                    "app.mailboxes.sync_service.UpstreamImapClient",
                    return_value=failing_client,
                ):
                    result = await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        imap_timeout=5,
                        llm_enabled=False,
                    )
            return result

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result.failed_connection is True
        assert result.error_summary is not None

    def test_per_message_fetch_failure_continues_sync(self, factory, tmp_path):
        """If one message fails to fetch, the sync continues for the rest."""
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        raw = self._get_eml()

        async def _run():
            user_id = await _insert_test_user(factory, "syncpartial@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            # UID 10 fails; UID 11 succeeds
            failing_client = _make_mock_imap_client(uids=[10, 11], raw_bytes=raw)
            failing_client.fetch_uids_in_range = AsyncMock(return_value=[10, 11])

            call_count = 0

            async def _fetch(uid):
                nonlocal call_count
                call_count += 1
                if uid == 10:
                    raise UpstreamImapError("FETCH failed for UID 10")
                return raw

            failing_client.fetch_raw_message = _fetch

            async with factory() as db:
                with patch(
                    "app.mailboxes.sync_service.UpstreamImapClient",
                    return_value=failing_client,
                ):
                    result = await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        batch_size=50,
                        imap_timeout=5,
                        llm_enabled=False,
                    )
            return result

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result.new_messages == 1
        assert result.errors == 1

    def test_sync_state_updates_checkpoint(self, factory, tmp_path):
        """After a successful sync, last_seen_uid is updated."""
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        raw = self._get_eml()
        mock_client = _make_mock_imap_client(uids=[100, 200, 300], raw_bytes=raw)
        mock_client.fetch_uids_in_range = AsyncMock(return_value=[100, 200, 300])

        async def _run():
            user_id = await _insert_test_user(factory, "synccp@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            async with factory() as db:
                with patch(_IMAP_CLIENT_PATCH, return_value=mock_client):
                    await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        batch_size=50,
                        imap_timeout=5,
                        llm_enabled=False,
                    )
            return mailbox_id

        mailbox_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _check():
            async with factory() as db:
                return await get_sync_state(db, mailbox_id, "INBOX")

        state = asyncio.get_event_loop().run_until_complete(_check())
        assert state.last_seen_uid == 300
        assert state.sync_status == SyncStatus.IDLE
        assert state.last_successful_sync_at is not None

    def test_quarantine_verdict_sets_quarantined_visibility(self, factory, tmp_path):
        """Messages with quarantine verdict get QUARANTINED visibility."""

        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore
        from app.policies.constants import Verdict

        raw = self._get_eml()
        mock_client = _make_mock_imap_client(uids=[42], raw_bytes=raw)
        mock_client.fetch_uids_in_range = AsyncMock(return_value=[42])

        # Mock a quarantine verdict from the analysis run
        mock_run = MagicMock()
        mock_run.verdict = Verdict.QUARANTINE
        mock_run.id = 1

        async def _run():
            user_id = await _insert_test_user(factory, "syncqv@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            async with factory() as db:
                with patch(_IMAP_CLIENT_PATCH, return_value=mock_client):
                    with patch(
                        "app.mailboxes.sync_service.analysis_service.run_analysis",
                        AsyncMock(return_value=mock_run),
                    ):
                        await sync_mailbox_folder(
                            db=db,
                            profile=profile,
                            folder_name="INBOX",
                            encryptor=self._make_encryptor(),
                            store=RawMessageStore(tmp_path),
                            batch_size=50,
                            imap_timeout=5,
                            llm_enabled=False,
                        )
            return mailbox_id

        mailbox_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _check():
            from sqlalchemy import select

            async with factory() as db:
                res = await db.execute(
                    select(MailboxItem).where(MailboxItem.mailbox_profile_id == mailbox_id)
                )
                return list(res.scalars().all())

        items = asyncio.get_event_loop().run_until_complete(_check())
        assert len(items) == 1
        assert items[0].visibility == ItemVisibility.QUARANTINED

    def test_mindwall_uids_are_sequential(self, factory, tmp_path):
        """Each new MailboxItem gets a unique, sequential Mindwall UID."""
        from app.mailboxes.sync_service import sync_mailbox_folder
        from app.messages.storage import RawMessageStore

        raw = self._get_eml()
        mock_client = _make_mock_imap_client(uids=[1, 2, 3], raw_bytes=raw)
        mock_client.fetch_uids_in_range = AsyncMock(return_value=[1, 2, 3])

        async def _run():
            user_id = await _insert_test_user(factory, "syncuid@example.com")
            mailbox_id = await _insert_test_mailbox(factory, user_id)
            profile = self._make_profile(mailbox_id)

            async with factory() as db:
                with patch(_IMAP_CLIENT_PATCH, return_value=mock_client):
                    await sync_mailbox_folder(
                        db=db,
                        profile=profile,
                        folder_name="INBOX",
                        encryptor=self._make_encryptor(),
                        store=RawMessageStore(tmp_path),
                        batch_size=50,
                        imap_timeout=5,
                        llm_enabled=False,
                    )
            return mailbox_id

        mailbox_id = asyncio.get_event_loop().run_until_complete(_run())

        async def _check():
            from sqlalchemy import select

            async with factory() as db:
                res = await db.execute(
                    select(MailboxItem)
                    .where(MailboxItem.mailbox_profile_id == mailbox_id)
                    .order_by(MailboxItem.upstream_uid)
                )
                return [i.mindwall_uid for i in res.scalars().all()]

        muids = asyncio.get_event_loop().run_until_complete(_check())
        assert muids == sorted(set(muids))  # unique and sequential
        assert all(u is not None for u in muids)
