"""Policy settings model — DB-backed overrides for runtime configuration.

One row per key (upsert pattern).  Keys map directly to the config field names
that are safe to edit at runtime without a redeploy.

The model intentionally stores all values as text; the service layer is
responsible for casting to the correct Python types on read.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PolicySetting(Base):
    """A single editable policy setting stored in the database.

    Runtime overrides take precedence over the static Settings object.
    Only settings explicitly whitelisted in the service are exposed via the
    policy editor; all others continue to come from environment variables.
    """

    __tablename__ = "policy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Stable identifier — matches the Settings field name.
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)

    # All values stored as text; service casts on read.
    value: Mapped[str] = mapped_column(Text, nullable=False)

    # Who last changed this setting (None = initial seed / no actor).
    changed_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # ISO-8601 timestamp of the last change — stored for display.
    changed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Human-readable note from the editor.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
