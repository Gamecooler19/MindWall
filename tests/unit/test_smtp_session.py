"""Unit tests for app.proxies.smtp.session.

Covers:
  - Successful authentication returns SmtpProxySession with correct fields.
  - Wrong username returns None.
  - Wrong password returns None.
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
from app.proxies.smtp.session import SmtpProxySession, authenticate_smtp_credentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture()
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


def _make_profile(
    *,
    owner_id: int = 1,
    proxy_username: str = "mw_smtp_test_abc",
    proxy_password: str = "correct-proxy-password",
    status: MailboxStatus = MailboxStatus.ACTIVE,
) -> MailboxProfile:
    proxy_password_hash = bcrypt.hashpw(
        proxy_password.encode(), bcrypt.gensalt()
    ).decode()
    return MailboxProfile(
        owner_id=owner_id,
        email_address=f"smtp_user{owner_id}@example.com",
        display_name="SMTP Test User",
        imap_host="imap.example.com",
        imap_port=993,
        imap_username=f"smtp_user{owner_id}@example.com",
        imap_password_enc="fakeenc",
        imap_security=ImapSecurity.SSL_TLS,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username=f"smtp_user{owner_id}@example.com",
        smtp_password_enc="fakeenc",
        smtp_security=SmtpSecurity.STARTTLS,
        proxy_username=proxy_username,
        proxy_password_hash=proxy_password_hash,
        status=status,
    )


class TestAuthenticateSmtpCredentials:
    def test_success_returns_smtp_proxy_session(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    owner_id=21,
                    proxy_username="mw_smtp_alice_xyz",
                    proxy_password="smtp-secret-123",
                )
                db.add(profile)
                await db.commit()
                await db.refresh(profile)

                session = await authenticate_smtp_credentials(
                    db, "mw_smtp_alice_xyz", "smtp-secret-123"
                )
            return session, profile.id, profile.owner_id

        session, profile_id, owner_id = asyncio.get_event_loop().run_until_complete(_run())
        assert session is not None
        assert isinstance(session, SmtpProxySession)
        assert session.proxy_username == "mw_smtp_alice_xyz"
        assert session.mailbox_profile_id == profile_id
        assert session.owner_user_id == owner_id
        assert session.email_address == "smtp_user21@example.com"

    def test_wrong_username_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                return await authenticate_smtp_credentials(
                    db, "mw_smtp_does_not_exist", "any-password"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_wrong_password_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    owner_id=22,
                    proxy_username="mw_smtp_bob_789",
                    proxy_password="correct-pass",
                )
                db.add(profile)
                await db.commit()

                return await authenticate_smtp_credentials(
                    db, "mw_smtp_bob_789", "wrong-password"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None

    def test_inactive_profile_returns_none(self, factory):
        async def _run():
            async with factory() as db:
                profile = _make_profile(
                    owner_id=23,
                    proxy_username="mw_smtp_carol_zzz",
                    proxy_password="pass",
                    status=MailboxStatus.INACTIVE,
                )
                db.add(profile)
                await db.commit()

                return await authenticate_smtp_credentials(
                    db, "mw_smtp_carol_zzz", "pass"
                )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is None
