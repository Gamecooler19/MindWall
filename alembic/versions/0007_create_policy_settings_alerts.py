"""Alembic migration 0007: create policy_settings and alerts tables.

Revision ID: g7d4e3f2a169
Revises: f6c3d2e9a158
Create Date: 2026-04-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g7d4e3f2a169"
down_revision: str | None = "f6c3d2e9a158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # policy_settings
    # ------------------------------------------------------------------ #
    op.create_table(
        "policy_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.String(40), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_policy_settings_key", "policy_settings", ["key"], unique=True)

    # ------------------------------------------------------------------ #
    # alerts
    # ------------------------------------------------------------------ #
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger_action", sa.String(120), nullable=True),
        sa.Column("quarantine_item_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("acknowledged_at", sa.String(40), nullable=True),
        sa.Column("resolved_at", sa.String(40), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["quarantine_item_id"],
            ["quarantine_items.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_quarantine_item_id", "alerts", ["quarantine_item_id"])
    op.create_index("ix_alerts_message_id", "alerts", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_message_id", table_name="alerts")
    op.drop_index("ix_alerts_quarantine_item_id", table_name="alerts")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_index("ix_alerts_severity", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_policy_settings_key", table_name="policy_settings")
    op.drop_table("policy_settings")
