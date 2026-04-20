"""Unit tests for app.mailboxes.service.

Tests run against an in-memory SQLite database via pytest-asyncio.
Connectivity functions are NOT called in this module — those are tested separately.
"""

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.service import hash_password, verify_password
from app.db.base import Base
from app.mailboxes import service as svc
from app.mailboxes.models import ImapSecurity, MailboxProfile, MailboxStatus, SmtpSecurity
from app.mailboxes.schemas import MailboxFormData
from app.security.crypto import CredentialEncryptor
from app.users.models import User, UserRole

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_KEY = Fernet.generate_key().decode()
_ENCRYPTOR = CredentialEncryptor(_KEY)


# ---------------------------------------------------------------------------
# Local fixtures — isolated from the session-scoped conftest fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def engine():
    eng = create_async_engine(_TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    """Insert a test user and return it."""
    user = User(
        email="test@example.com",
        hashed_password=hash_password("test-password"),
        role=UserRole.USER,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


def _make_form(**overrides) -> MailboxFormData:
    """Return a valid MailboxFormData with sensible defaults."""
    defaults = dict(
        display_name="Work",
        email_address="alice@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        imap_username="alice@example.com",
        imap_password="imap-secret",
        imap_security=ImapSecurity.SSL_TLS,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="alice@example.com",
        smtp_password="smtp-secret",
        smtp_security=SmtpSecurity.STARTTLS,
    )
    defaults.update(overrides)
    return MailboxFormData(**defaults)


# ---------------------------------------------------------------------------
# Proxy credential generation
# ---------------------------------------------------------------------------


class TestProxyCredentialGeneration:
    def test_generate_proxy_username_format(self):
        username = svc.generate_proxy_username("alice@example.com")
        assert username.startswith("mw_")
        assert len(username) > 6
        # Must contain only safe characters
        assert all(c.isalnum() or c == "_" for c in username)

    def test_generate_proxy_username_strips_special_chars(self):
        username = svc.generate_proxy_username("ali-ce+tag@example.com")
        assert "+" not in username
        assert "-" not in username

    def test_generate_proxy_username_is_unique(self):
        u1 = svc.generate_proxy_username("alice@example.com")
        u2 = svc.generate_proxy_username("alice@example.com")
        assert u1 != u2, "Proxy usernames should be unique due to random suffix"

    def test_generate_proxy_password_length(self):
        pw = svc.generate_proxy_password()
        assert len(pw) >= 24

    def test_generate_proxy_password_is_unique(self):
        assert svc.generate_proxy_password() != svc.generate_proxy_password()

    def test_generate_proxy_password_url_safe(self):
        pw = svc.generate_proxy_password()
        # URL-safe base64 uses only A-Z a-z 0-9 - _
        assert all(c.isalnum() or c in "-_" for c in pw)


# ---------------------------------------------------------------------------
# create_mailbox
# ---------------------------------------------------------------------------


class TestCreateMailbox:
    async def test_create_stores_encrypted_imap_password(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(imap_password="secret-imap-123")
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        assert profile.imap_password_enc != "secret-imap-123"
        assert _ENCRYPTOR.decrypt(profile.imap_password_enc) == "secret-imap-123"

    async def test_create_stores_encrypted_smtp_password(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(smtp_password="secret-smtp-456")
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        assert profile.smtp_password_enc != "secret-smtp-456"
        assert _ENCRYPTOR.decrypt(profile.smtp_password_enc) == "secret-smtp-456"

    async def test_create_returns_plaintext_proxy_password_once(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form()
        profile, plain_pw = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        assert plain_pw  # non-empty
        assert isinstance(plain_pw, str)
        # The plaintext should verify against the stored hash
        assert verify_password(plain_pw, profile.proxy_password_hash)

    async def test_create_proxy_password_not_stored_as_plaintext(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form()
        profile, plain_pw = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        # The hash must differ from the plaintext
        assert profile.proxy_password_hash != plain_pw

    async def test_create_sets_owner_id(self, db: AsyncSession, test_user: User):
        form = _make_form()
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        assert profile.owner_id == test_user.id

    async def test_create_sets_status_pending(self, db: AsyncSession, test_user: User):
        form = _make_form()
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        assert profile.status == MailboxStatus.PENDING

    async def test_create_generates_proxy_username(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form()
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        assert profile.proxy_username is not None
        assert profile.proxy_username.startswith("mw_")

    async def test_create_raises_if_imap_password_blank(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(imap_password="")
        with pytest.raises(ValueError, match="IMAP password"):
            await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

    async def test_create_raises_if_smtp_password_blank(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(smtp_password="")
        with pytest.raises(ValueError, match="SMTP password"):
            await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)


# ---------------------------------------------------------------------------
# get_mailbox_by_id — ownership enforcement
# ---------------------------------------------------------------------------


class TestGetMailboxById:
    async def test_returns_profile_for_owner(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form()
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        fetched = await svc.get_mailbox_by_id(db, profile.id, test_user.id)
        assert fetched is not None
        assert fetched.id == profile.id

    async def test_returns_none_for_wrong_owner(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form()
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        fetched = await svc.get_mailbox_by_id(db, profile.id, owner_id=9999)
        assert fetched is None

    async def test_returns_none_for_nonexistent_mailbox(
        self, db: AsyncSession, test_user: User
    ):
        fetched = await svc.get_mailbox_by_id(db, 99999, test_user.id)
        assert fetched is None


# ---------------------------------------------------------------------------
# update_mailbox
# ---------------------------------------------------------------------------


class TestUpdateMailbox:
    async def test_update_changes_display_name(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(display_name="Old Name")
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        updated_form = _make_form(display_name="New Name")
        await svc.update_mailbox(db, profile, updated_form, _ENCRYPTOR)
        assert profile.display_name == "New Name"

    async def test_update_keeps_imap_password_if_blank(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(imap_password="original-secret")
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        original_enc = profile.imap_password_enc

        updated_form = _make_form(imap_password="")
        await svc.update_mailbox(db, profile, updated_form, _ENCRYPTOR)

        # Encrypted value must not change when password field is left blank
        assert profile.imap_password_enc == original_enc

    async def test_update_re_encrypts_imap_password_if_provided(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form(imap_password="old-password")
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        updated_form = _make_form(imap_password="new-password")
        await svc.update_mailbox(db, profile, updated_form, _ENCRYPTOR)

        assert _ENCRYPTOR.decrypt(profile.imap_password_enc) == "new-password"


# ---------------------------------------------------------------------------
# reset_proxy_password
# ---------------------------------------------------------------------------


class TestResetProxyPassword:
    async def test_reset_returns_new_plaintext(
        self, db: AsyncSession, test_user: User
    ):
        form = _make_form()
        profile, original_plain = await svc.create_mailbox(
            db, test_user.id, form, _ENCRYPTOR
        )

        new_plain = await svc.reset_proxy_password(db, profile)
        assert new_plain != original_plain
        assert verify_password(new_plain, profile.proxy_password_hash)

    async def test_reset_updates_hash(self, db: AsyncSession, test_user: User):
        form = _make_form()
        profile, original_plain = await svc.create_mailbox(
            db, test_user.id, form, _ENCRYPTOR
        )
        original_hash = profile.proxy_password_hash

        await svc.reset_proxy_password(db, profile)
        assert profile.proxy_password_hash != original_hash


# ---------------------------------------------------------------------------
# list_mailboxes_for_user
# ---------------------------------------------------------------------------


class TestListMailboxes:
    async def test_lists_only_owner_mailboxes(
        self, db: AsyncSession, test_user: User
    ):
        # Add two mailboxes for the test user
        for addr in ["a@example.com", "b@example.com"]:
            form = _make_form(email_address=addr)
            await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)

        profiles = await svc.list_mailboxes_for_user(db, test_user.id)
        assert len(profiles) >= 2
        for p in profiles:
            assert p.owner_id == test_user.id


# ---------------------------------------------------------------------------
# delete_mailbox
# ---------------------------------------------------------------------------


class TestDeleteMailbox:
    async def test_delete_removes_profile(self, db: AsyncSession, test_user: User):
        form = _make_form(email_address="delete-me@example.com")
        profile, _ = await svc.create_mailbox(db, test_user.id, form, _ENCRYPTOR)
        mailbox_id = profile.id

        await svc.delete_mailbox(db, profile)

        # Should not be findable after deletion
        fetched = await svc.get_mailbox_by_id(db, mailbox_id, test_user.id)
        assert fetched is None
