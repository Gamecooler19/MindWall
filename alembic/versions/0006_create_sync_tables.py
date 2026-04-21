"""Alembic migration 0006: create mailbox_sync_states and mailbox_items tables.

Revision ID: f6c3d2e9a158
Revises: e5b2c1d8f047
Create Date: 2026-04-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c3d2e9a158"
down_revision: str | None = "e5b2c1d8f047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # mailbox_sync_states
    # ------------------------------------------------------------------ #
    op.create_table(
        "mailbox_sync_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mailbox_profile_id", sa.Integer(), nullable=False),
        sa.Column("folder_name", sa.String(255), nullable=False),
        sa.Column("uid_validity", sa.Integer(), nullable=True),
        sa.Column("last_seen_uid", sa.Integer(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(20),
            nullable=False,
            server_default="idle",
        ),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_sync_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mailbox_profile_id"],
            ["mailbox_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox_profile_id",
            "folder_name",
            name="uq_sync_state_mailbox_folder",
        ),
    )
    op.create_index(
        "ix_mailbox_sync_states_mailbox_profile_id",
        "mailbox_sync_states",
        ["mailbox_profile_id"],
    )

    # ------------------------------------------------------------------ #
    # mailbox_items
    # ------------------------------------------------------------------ #
    op.create_table(
        "mailbox_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mailbox_profile_id", sa.Integer(), nullable=False),
        sa.Column("folder_name", sa.String(255), nullable=False),
        sa.Column("upstream_uid", sa.Integer(), nullable=False),
        sa.Column("uid_validity", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("rfc_message_id", sa.String(500), nullable=True),
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("mindwall_uid", sa.Integer(), nullable=True),
        sa.Column("ingestion_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mailbox_profile_id"],
            ["mailbox_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox_profile_id",
            "folder_name",
            "upstream_uid",
            name="uq_mailbox_item_uid",
        ),
    )
    op.create_index(
        "ix_mailbox_items_mailbox_profile_id",
        "mailbox_items",
        ["mailbox_profile_id"],
    )
    op.create_index(
        "ix_mailbox_items_message_id",
        "mailbox_items",
        ["message_id"],
    )
    op.create_index(
        "ix_mailbox_items_rfc_message_id",
        "mailbox_items",
        ["rfc_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mailbox_items_rfc_message_id", table_name="mailbox_items")
    op.drop_index("ix_mailbox_items_message_id", table_name="mailbox_items")
    op.drop_index("ix_mailbox_items_mailbox_profile_id", table_name="mailbox_items")
    op.drop_table("mailbox_items")

    op.drop_index(
        "ix_mailbox_sync_states_mailbox_profile_id",
        table_name="mailbox_sync_states",
    )
    op.drop_table("mailbox_sync_states")
