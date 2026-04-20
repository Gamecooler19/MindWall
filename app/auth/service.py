"""Authentication service.

Handles password hashing (bcrypt) and credential verification.
This module contains no HTTP concerns — it is pure domain logic.
"""

import bcrypt
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User

log = structlog.get_logger(__name__)

# Pre-computed hash used for constant-time dummy verification.
# Prevents user-enumeration via timing differences when no account exists.
_DUMMY_HASH: bytes = bcrypt.hashpw(b"mindwall-dummy-verify-constant-time", bcrypt.gensalt())


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if plain_password matches the stored bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def _dummy_verify() -> None:
    """Perform a constant-time dummy bcrypt verify for timing equalization."""
    bcrypt.checkpw(b"mindwall-dummy-verify-constant-time", _DUMMY_HASH)


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User | None:
    """Authenticate a user by email and password.

    Returns the User on success, or None on failure.
    A constant-time dummy verify is performed when the user is not found
    to prevent user-enumeration via timing differences.
    """
    result = await db.execute(
        select(User).where(User.email == email, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Perform dummy hash to equalise timing regardless of whether the
        # account exists. This prevents user enumeration attacks.
        _dummy_verify()
        log.warning("auth.login_failed", reason="user_not_found")
        return None

    if not verify_password(password, user.hashed_password):
        log.warning("auth.login_failed", user_id=user.id, reason="bad_password")
        return None

    log.info("auth.login_success", user_id=user.id, role=user.role)
    return user
