"""User ORM model.

Represents an authenticated principal in Mindwall.
Roles are enforced at the dependency layer — see app/dependencies.py.
"""

import enum

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRole(enum.StrEnum):
    """RBAC roles for Mindwall principals.

    - ADMIN    — full system access, policy management, quarantine actions.
    - ANALYST  — can review quarantined mail and annotate verdicts.
    - OPERATOR — can view system health and mailbox status.
    - USER     — end user, can view their own mailbox through the proxy.
    """

    ADMIN = "admin"
    ANALYST = "analyst"
    OPERATOR = "operator"
    USER = "user"


class User(Base):
    """A registered Mindwall user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, length=20),
        nullable=False,
        default=UserRole.USER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
