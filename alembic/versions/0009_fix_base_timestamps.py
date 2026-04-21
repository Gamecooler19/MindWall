"""Alembic migration 0009: add missing Base timestamp columns.

Root cause: migration 0007 (policy_settings + alerts) was written before
the shared Base class gained automatic created_at/updated_at columns.
As a result:
  - policy_settings is missing created_at (timestamptz) and updated_at (timestamptz)
  - alerts is missing updated_at (timestamptz) — it has its own created_at as
    varchar(40) which the Alert model explicitly defines, overriding Base

This migration adds the missing columns with a server_default of now() so that
all existing rows are backfilled with the current timestamp, and future rows
get a real timestamp automatically.

Revision ID: i9f6g5h4c381
Revises: h8e5f4g3b270
Create Date: 2026-04-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i9f6g5h4c381"
down_revision: str | None = "h8e5f4g3b270"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # policy_settings — add created_at and updated_at
    # ------------------------------------------------------------------
    # Both columns added with server_default=now() so existing rows are
    # immediately backfilled.  After backfill we remove the server_default
    # so the application's Python-side default takes over for new rows
    # (consistent with how Base sets the default via mapped_column).
    op.add_column(
        "policy_settings",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
    )
    op.add_column(
        "policy_settings",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
    )
    # Drop the server_default so it matches the ORM expectation (Python sets it).
    op.alter_column("policy_settings", "created_at", server_default=None)
    op.alter_column("policy_settings", "updated_at", server_default=None)

    # ------------------------------------------------------------------
    # alerts — add updated_at only
    # (created_at already exists as varchar(40) per the Alert model override)
    # ------------------------------------------------------------------
    op.add_column(
        "alerts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
    )
    op.alter_column("alerts", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("alerts", "updated_at")
    op.drop_column("policy_settings", "updated_at")
    op.drop_column("policy_settings", "created_at")
