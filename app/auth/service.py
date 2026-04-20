"""Authentication service.

Handles password hashing (bcrypt) and credential verification.
This module contains no HTTP concerns — it is pure domain logic.
"""

import structlog
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User

log = structlog.get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if plain_password matches the stored bcrypt hash."""
    return _pwd_context.verify(plain_password, hashed_password)


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
        _pwd_context.dummy_verify()
        log.warning("auth.login_failed", reason="user_not_found")
        return None

    if not verify_password(password, user.hashed_password):
        log.warning("auth.login_failed", user_id=user.id, reason="bad_password")
        return None

    log.info("auth.login_success", user_id=user.id, role=user.role)
    return user
