"""Unit tests for the mailbox virtualization service.

Uses the in-memory SQLite test DB.
No real IMAP server or Ollama required.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.mailboxes.sync_models import ItemVisibility, MailboxItem
from app.mailboxes.view_service import (
    get_mailbox_item_counts,
    get_pending_items,
    get_quarantine_inbox,
    get_visible_inbox,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _insert_user(factory, email: str) -> int:
    from sqlalchemy import text

    async with factory() as db:
        result = await db.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, role, is_active, created_at, updated_at)"
                " VALUES (:e, :h, :r, :a, :now, :now) RETURNING id"
            ),
            {"e": email, "h": "$2b$12$x", "r": "ADMIN", "a": True, "now": datetime.now(UTC)},
        )
        uid = result.scalar_one()
        await db.commit()
    return uid


async def _insert_mailbox(factory, owner_id: int) -> int:
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
                "oid": owner_id, "dn": "View Test", "ea": f"v{owner_id}@example.com",
                "ih": "imap.x.com", "ip": 993, "iu": "u", "ienc": "x", "isec": "SSL_TLS",
                "sh": "smtp.x.com", "sp": 587, "su": "u", "senc": "x", "ssec": "STARTTLS",
                "st": "ACTIVE", "now": datetime.now(UTC),
            },
        )
        mid = result.scalar_one()
        await db.commit()
    return mid


async def _insert_message(factory, mailbox_id: int) -> int:
    import secrets

    from sqlalchemy import text

    sha = secrets.token_hex(32)
    async with factory() as db:
        result = await db.execute(
            text(
                "INSERT INTO messages "
                "(mailbox_profile_id, ingestion_source, raw_size_bytes, raw_sha256,"
                " has_text_plain, has_text_html, header_dkim_signature_present,"
                " num_attachments, num_urls, created_at, updated_at) "
                "VALUES (:mid, :src, :sz, :sha, 1, 0, 0, 0, 0, :now, :now) RETURNING id"
            ),
            {
                "mid": mailbox_id, "src": "IMAP_SYNC", "sz": 500,
                "sha": sha, "now": datetime.now(UTC),
            },
        )
        msg_id = result.scalar_one()
        await db.commit()
    return msg_id


async def _insert_mailbox_item(
    factory,
    mailbox_id: int,
    upstream_uid: int,
    visibility: ItemVisibility,
    message_id: int | None = None,
    folder: str = "INBOX",
) -> int:
    async with factory() as db:
        item = MailboxItem(
            mailbox_profile_id=mailbox_id,
            folder_name=folder,
            upstream_uid=upstream_uid,
            uid_validity=12345,
            message_id=message_id,
            visibility=visibility,
            mindwall_uid=upstream_uid,
        )
        db.add(item)
        await db.flush()
        item_id = item.id
        await db.commit()
    return item_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetVisibleInbox:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_returns_only_visible_items(self, factory):
        async def _run():
            uid = await _insert_user(factory, "viewtest1@example.com")
            mbid = await _insert_mailbox(factory, uid)
            msg1 = await _insert_message(factory, mbid)
            msg2 = await _insert_message(factory, mbid)

            await _insert_mailbox_item(factory, mbid, 1, ItemVisibility.VISIBLE, msg1)
            await _insert_mailbox_item(factory, mbid, 2, ItemVisibility.QUARANTINED, msg2)

            async with factory() as db:
                items = await get_visible_inbox(db, mbid)
            return items, mbid

        items, _mbid = asyncio.get_event_loop().run_until_complete(_run())
        assert len(items) == 1
        assert items[0].mailbox_item.visibility == ItemVisibility.VISIBLE

    def test_excludes_quarantined_and_hidden(self, factory):
        async def _run():
            uid = await _insert_user(factory, "viewtest2@example.com")
            mbid = await _insert_mailbox(factory, uid)
            msg = await _insert_message(factory, mbid)

            await _insert_mailbox_item(factory, mbid, 10, ItemVisibility.QUARANTINED, msg)
            await _insert_mailbox_item(factory, mbid, 11, ItemVisibility.HIDDEN)

            async with factory() as db:
                items = await get_visible_inbox(db, mbid)
            return items

        items = asyncio.get_event_loop().run_until_complete(_run())
        assert len(items) == 0

    def test_filters_by_folder_name(self, factory):
        async def _run():
            uid = await _insert_user(factory, "viewtest3@example.com")
            mbid = await _insert_mailbox(factory, uid)
            msg1 = await _insert_message(factory, mbid)
            msg2 = await _insert_message(factory, mbid)

            await _insert_mailbox_item(factory, mbid, 1, ItemVisibility.VISIBLE, msg1, "INBOX")
            await _insert_mailbox_item(factory, mbid, 2, ItemVisibility.VISIBLE, msg2, "Archive")

            async with factory() as db:
                items = await get_visible_inbox(db, mbid, folder_name="INBOX")
            return items

        items = asyncio.get_event_loop().run_until_complete(_run())
        assert len(items) == 1
        assert items[0].mailbox_item.folder_name == "INBOX"

    def test_ordered_newest_first(self, factory):
        async def _run():
            uid = await _insert_user(factory, "viewtest4@example.com")
            mbid = await _insert_mailbox(factory, uid)
            m1 = await _insert_message(factory, mbid)
            m2 = await _insert_message(factory, mbid)
            m3 = await _insert_message(factory, mbid)

            await _insert_mailbox_item(factory, mbid, 1, ItemVisibility.VISIBLE, m1)
            await _insert_mailbox_item(factory, mbid, 3, ItemVisibility.VISIBLE, m2)
            await _insert_mailbox_item(factory, mbid, 2, ItemVisibility.VISIBLE, m3)

            async with factory() as db:
                items = await get_visible_inbox(db, mbid)
            return [vi.mailbox_item.upstream_uid for vi in items]

        uids = asyncio.get_event_loop().run_until_complete(_run())
        assert uids == sorted(uids, reverse=True)


class TestGetQuarantineInbox:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_returns_quarantined_and_hidden(self, factory):
        async def _run():
            uid = await _insert_user(factory, "qvtest1@example.com")
            mbid = await _insert_mailbox(factory, uid)
            m1 = await _insert_message(factory, mbid)
            m2 = await _insert_message(factory, mbid)
            m3 = await _insert_message(factory, mbid)

            await _insert_mailbox_item(factory, mbid, 1, ItemVisibility.QUARANTINED, m1)
            await _insert_mailbox_item(factory, mbid, 2, ItemVisibility.HIDDEN, m2)
            await _insert_mailbox_item(factory, mbid, 3, ItemVisibility.VISIBLE, m3)

            async with factory() as db:
                items = await get_quarantine_inbox(db, mbid)
            return items

        items = asyncio.get_event_loop().run_until_complete(_run())
        assert len(items) == 2
        visibilities = {vi.mailbox_item.visibility for vi in items}
        assert ItemVisibility.QUARANTINED in visibilities
        assert ItemVisibility.HIDDEN in visibilities

    def test_excludes_visible_items(self, factory):
        async def _run():
            uid = await _insert_user(factory, "qvtest2@example.com")
            mbid = await _insert_mailbox(factory, uid)
            msg = await _insert_message(factory, mbid)
            await _insert_mailbox_item(factory, mbid, 5, ItemVisibility.VISIBLE, msg)

            async with factory() as db:
                items = await get_quarantine_inbox(db, mbid)
            return items

        items = asyncio.get_event_loop().run_until_complete(_run())
        assert len(items) == 0


class TestGetPendingItems:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_returns_pending_items_only(self, factory):
        async def _run():
            uid = await _insert_user(factory, "pendtest@example.com")
            mbid = await _insert_mailbox(factory, uid)

            await _insert_mailbox_item(factory, mbid, 1, ItemVisibility.PENDING)
            await _insert_mailbox_item(factory, mbid, 2, ItemVisibility.PENDING)
            await _insert_mailbox_item(factory, mbid, 3, ItemVisibility.VISIBLE)

            async with factory() as db:
                items = await get_pending_items(db, mbid)
            return items

        items = asyncio.get_event_loop().run_until_complete(_run())
        assert len(items) == 2
        for vi in items:
            assert vi.mailbox_item.visibility == ItemVisibility.PENDING


class TestGetMailboxItemCounts:
    @pytest.fixture()
    def factory(self, db_engine):
        return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    def test_counts_by_visibility(self, factory):
        async def _run():
            uid = await _insert_user(factory, "counttest@example.com")
            mbid = await _insert_mailbox(factory, uid)
            m1 = await _insert_message(factory, mbid)
            m2 = await _insert_message(factory, mbid)
            m3 = await _insert_message(factory, mbid)

            await _insert_mailbox_item(factory, mbid, 1, ItemVisibility.VISIBLE, m1)
            await _insert_mailbox_item(factory, mbid, 2, ItemVisibility.VISIBLE, m2)
            await _insert_mailbox_item(factory, mbid, 3, ItemVisibility.QUARANTINED, m3)

            async with factory() as db:
                return await get_mailbox_item_counts(db, mbid)

        counts = asyncio.get_event_loop().run_until_complete(_run())
        assert counts.get(ItemVisibility.VISIBLE) == 2
        assert counts.get(ItemVisibility.QUARANTINED) == 1
