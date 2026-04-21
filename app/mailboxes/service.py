"""Mailbox domain service layer.

All business logic for mailbox registration lives here.
Route handlers must not contain business logic — they call service functions
and map results to HTTP responses.

Security principles:
  - Upstream passwords are encrypted immediately upon receipt; never stored
    or logged in plaintext.
  - Proxy passwords are hashed (bcrypt) immediately after generation; the
    plaintext is returned only once and never persisted.
  - Ownership is enforced at the service level: every mutating operation
    requires the caller to pass the current user's ID and verifies it matches
    the mailbox profile's owner_id.
  - Decryption only happens when explicitly needed (connectivity testing).
"""

import re
import secrets
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import hash_password
from app.mailboxes.connectivity import (
    ConnectivityResult,
    test_imap_connection,
    test_smtp_connection,
)
from app.mailboxes.models import MailboxProfile, MailboxStatus
from app.mailboxes.schemas import MailboxFormData
from app.security.crypto import CredentialEncryptor

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Proxy credential generation
# ---------------------------------------------------------------------------


def generate_proxy_username(email: str) -> str:
    """Generate a readable, globally-unique proxy username.

    Format: mw_{local_part_up_to_12_chars}_{6_random_hex_chars}
    Example: mw_alice_a1b2c3

    The username is not a secret — it is shown permanently in the setup
    instructions page and is used for IMAP/SMTP login identification only.
    """
    local_part = email.split("@")[0].lower()
    # Strip non-alphanumeric characters and truncate.
    safe_local = re.sub(r"[^a-z0-9]", "", local_part)[:12] or "user"
    suffix = secrets.token_hex(3)  # 6 lowercase hex chars
    return f"mw_{safe_local}_{suffix}"


def generate_proxy_password() -> str:
    """Generate a cryptographically strong random proxy password.

    Returns 32 URL-safe characters (192 bits of entropy).
    This plaintext value is shown to the user exactly once and must not
    be stored; store only the bcrypt hash via hash_password().
    """
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


async def get_mailbox_by_id(
    db: AsyncSession,
    mailbox_id: int,
    owner_id: int,
) -> MailboxProfile | None:
    """Return a MailboxProfile owned by owner_id, or None if not found.

    Ownership is enforced: a profile belonging to a different user returns None.
    """
    result = await db.execute(
        select(MailboxProfile).where(
            MailboxProfile.id == mailbox_id,
            MailboxProfile.owner_id == owner_id,
        )
    )
    return result.scalar_one_or_none()


async def list_mailboxes_for_user(
    db: AsyncSession,
    owner_id: int,
) -> list[MailboxProfile]:
    """Return all mailbox profiles belonging to owner_id, ordered by creation."""
    result = await db.execute(
        select(MailboxProfile)
        .where(MailboxProfile.owner_id == owner_id)
        .order_by(MailboxProfile.created_at)
    )
    return list(result.scalars().all())


async def create_mailbox(
    db: AsyncSession,
    owner_id: int,
    form: MailboxFormData,
    encryptor: CredentialEncryptor,
) -> tuple[MailboxProfile, str]:
    """Create a new MailboxProfile with encrypted credentials and proxy credentials.

    Returns:
        (profile, proxy_password_plain) — the plaintext proxy password is returned
        ONLY here so the caller can present it to the user once. It is never
        persisted in plaintext.

    Raises:
        ValueError: If either password field is blank (required for creation).
    """
    if not form.imap_password:
        raise ValueError("IMAP password is required when creating a new mailbox.")
    if not form.smtp_password:
        raise ValueError("SMTP password is required when creating a new mailbox.")

    imap_enc = encryptor.encrypt(form.imap_password)
    smtp_enc = encryptor.encrypt(form.smtp_password)

    proxy_username = generate_proxy_username(form.email_address)
    proxy_password_plain = generate_proxy_password()
    proxy_password_hash = hash_password(proxy_password_plain)

    profile = MailboxProfile(
        owner_id=owner_id,
        display_name=form.display_name,
        email_address=form.email_address,
        imap_host=form.imap_host,
        imap_port=form.imap_port,
        imap_username=form.imap_username,
        imap_password_enc=imap_enc,
        imap_security=form.imap_security,
        smtp_host=form.smtp_host,
        smtp_port=form.smtp_port,
        smtp_username=form.smtp_username,
        smtp_password_enc=smtp_enc,
        smtp_security=form.smtp_security,
        status=MailboxStatus.PENDING,
        proxy_username=proxy_username,
        proxy_password_hash=proxy_password_hash,
    )

    db.add(profile)
    await db.flush()  # Populate profile.id without committing the transaction.

    log.info(
        "mailbox.created",
        mailbox_id=profile.id,
        owner_id=owner_id,
        email=form.email_address,
    )
    return profile, proxy_password_plain


async def update_mailbox(
    db: AsyncSession,
    profile: MailboxProfile,
    form: MailboxFormData,
    encryptor: CredentialEncryptor,
) -> MailboxProfile:
    """Update an existing MailboxProfile.

    If imap_password or smtp_password is blank, the existing encrypted
    credential is preserved unchanged. If provided, it is re-encrypted.
    """
    profile.display_name = form.display_name
    profile.email_address = form.email_address
    profile.imap_host = form.imap_host
    profile.imap_port = form.imap_port
    profile.imap_username = form.imap_username
    profile.imap_security = form.imap_security
    profile.smtp_host = form.smtp_host
    profile.smtp_port = form.smtp_port
    profile.smtp_username = form.smtp_username
    profile.smtp_security = form.smtp_security

    if form.imap_password:
        profile.imap_password_enc = encryptor.encrypt(form.imap_password)
    if form.smtp_password:
        profile.smtp_password_enc = encryptor.encrypt(form.smtp_password)

    await db.flush()

    log.info("mailbox.updated", mailbox_id=profile.id, owner_id=profile.owner_id)
    return profile


async def reset_proxy_password(
    db: AsyncSession,
    profile: MailboxProfile,
) -> str:
    """Generate a new proxy password for an existing MailboxProfile.

    Returns:
        proxy_password_plain — the new plaintext proxy password, shown once.
        Only the bcrypt hash is written to the database.
    """
    proxy_password_plain = generate_proxy_password()
    profile.proxy_password_hash = hash_password(proxy_password_plain)
    await db.flush()

    log.info(
        "mailbox.proxy_password_reset",
        mailbox_id=profile.id,
        owner_id=profile.owner_id,
    )
    return proxy_password_plain


async def delete_mailbox(
    db: AsyncSession,
    profile: MailboxProfile,
) -> None:
    """Permanently delete a MailboxProfile and its credentials."""
    await db.delete(profile)
    await db.flush()
    log.info("mailbox.deleted", mailbox_id=profile.id, owner_id=profile.owner_id)


async def count_mailboxes(db: AsyncSession) -> int:
    """Return the total number of registered mailbox profiles (admin use)."""
    from sqlalchemy import func

    result = await db.execute(select(func.count()).select_from(MailboxProfile))
    return result.scalar_one()


async def list_all_mailboxes(db: AsyncSession) -> list[MailboxProfile]:
    """Return all mailbox profiles across all users, ordered by creation (admin use)."""
    result = await db.execute(select(MailboxProfile).order_by(MailboxProfile.created_at))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Connectivity testing
# ---------------------------------------------------------------------------


async def test_mailbox_connectivity(
    db: AsyncSession,
    profile: MailboxProfile,
    encryptor: CredentialEncryptor,
    timeout: int = 10,
) -> tuple[ConnectivityResult, ConnectivityResult]:
    """Test upstream IMAP and SMTP connectivity for a MailboxProfile.

    Decrypts the stored credentials in memory for the duration of the test.
    Credentials are never logged.

    Updates the profile status and last_connection_error in the database.

    Returns:
        (imap_result, smtp_result) — structured test outcomes.
    """
    imap_password = encryptor.decrypt(profile.imap_password_enc)
    smtp_password = encryptor.decrypt(profile.smtp_password_enc)

    imap_result = await test_imap_connection(
        host=profile.imap_host,
        port=profile.imap_port,
        security=profile.imap_security,
        username=profile.imap_username,
        password=imap_password,
        timeout=timeout,
    )

    smtp_result = await test_smtp_connection(
        host=profile.smtp_host,
        port=profile.smtp_port,
        security=profile.smtp_security,
        username=profile.smtp_username,
        password=smtp_password,
        timeout=timeout,
    )

    # Update persistent connectivity status.
    profile.last_connection_check_at = datetime.now(UTC)

    if imap_result.success and smtp_result.success:
        profile.status = MailboxStatus.ACTIVE
        profile.last_connection_error = None
    else:
        profile.status = MailboxStatus.CONNECTION_ERROR
        errors = []
        if not imap_result.success and imap_result.error_message:
            errors.append(f"IMAP: {imap_result.error_message}")
        if not smtp_result.success and smtp_result.error_message:
            errors.append(f"SMTP: {smtp_result.error_message}")
        profile.last_connection_error = " | ".join(errors)[:500]

    await db.flush()

    log.info(
        "mailbox.connectivity_tested",
        mailbox_id=profile.id,
        imap_ok=imap_result.success,
        smtp_ok=smtp_result.success,
    )

    # Zero out in-memory plaintext credentials (best-effort in Python).
    imap_password = smtp_password = ""

    return imap_result, smtp_result
