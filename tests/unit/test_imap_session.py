"""Unit tests for the IMAP proxy session authentication layer.

Covers:
  - Successful authentication returns ProxySession with correct fields.
  - Username not found returns None with constant-time behaviour.
  - Wrong password returns None.
  - Missing proxy_password_hash returns None.
  - Inactive mailbox profile returns None.
"""

from __future__ import annotations

import asyncio

import bcrypt
import pytest
from app.mailboxes.models import (
    ImapSecurity,
    MailboxProfile,
    MailboxStatus,
    SmtpSecurity,
)
from app.proxies.imap.session import ProxySession, authenticate_proxy_credentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture()
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    owner_id: int = 1,
    proxy_username: str = "mw_test_abc123",
    proxy_password: str = "correct-proxy-password",
    status: MailboxStatus = MailboxStatus.ACTIVE,
    no_hash: bool = False,
) -> MailboxProfile:
    """Build a MailboxProfile suitable for auth tests."""
    proxy_password_hash = (
        None if no_hash
        else bcrypt.hashpw(proxy_password.encode(), bcrypt.gensalt()).decode()
    )
    return MailboxProfile(
        owner_id=owner_id,
        email_address=f"user{owner_id}@example.com",
        display_name="Test User",
        imap_host="imap.example.com",
        imap_port=993,
        imap_username=f"user{owner_id}@example.com",
        imap_password_enc="fakeencrypted",
        imap_security=ImapSecurity.SSL_TLS,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username=f"user{owner_id}@example.com",
        smtp_password_enc="fakeencrypted",
        smtp_security=SmtpSecurity.STARTTLS,
        proxy_username=proxy_username,
        proxy_password_hash=proxy_password_hash,
        status=status,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuthenticateProxyCredentials:
    def test_success_returns_proxy_session(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    proxy_username="mw_alice_abc123",
                    proxy_password="secret123",
                )
                db.add(profile)
                await db.commit()
                await db.refresh(profile)

                session = await authenticate_proxy_credentials(
                    db, "mw_alice_abc123", "secret123"
                )

            assert session is not None
            assert isinstance(session, ProxySession)
            assert session.proxy_username == "mw_alice_abc123"
            assert session.mailbox_profile_id == profile.id
            assert session.owner_user_id == profile.owner_id

        asyncio.get_event_loop().run_until_complete(_run())

    def test_wrong_username_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                session = await authenticate_proxy_credentials(
                    db, "mw_does_not_exist", "any-password"
                )
            return session

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_wrong_password_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    proxy_username="mw_bob_xyz789",
                    proxy_password="correct-pass",
                )
                db.add(profile)
                await db.commit()

                return await authenticate_proxy_credentials(
                    db, "mw_bob_xyz789", "wrong-password"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_no_proxy_hash_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    proxy_username="mw_nohash_111",
                    no_hash=True,
                )
                db.add(profile)
                await db.commit()

                return await authenticate_proxy_credentials(
                    db, "mw_nohash_111", "anypassword"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_inactive_mailbox_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    owner_id=99,
                    proxy_username="mw_inactive_222",
                    proxy_password="secret",
                    status=MailboxStatus.CONNECTION_ERROR,
                )
                db.add(profile)
                await db.commit()

                return await authenticate_proxy_credentials(
                    db, "mw_inactive_222", "secret"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_pending_mailbox_allowed(self, factory):
        """PENDING mailboxes should still be able to authenticate."""
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    owner_id=50,
                    proxy_username="mw_pending_555",
                    proxy_password="secret",
                    status=MailboxStatus.PENDING,
                )
                db.add(profile)
                await db.commit()

                return await authenticate_proxy_credentials(
                    db, "mw_pending_555", "secret"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is not None
        assert isinstance(result, ProxySession)
