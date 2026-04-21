"""Alembic migration 0008: create outbound_messages table.

Revision ID: h8e5f4g3b270
Revises: g7d4e3f2a169
Create Date: 2026-04-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h8e5f4g3b270"
down_revision: str | None = "g7d4e3f2a169"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mailbox_profile_id", sa.Integer(), nullable=False),
        sa.Column("proxy_username", sa.String(100), nullable=False),
        sa.Column("envelope_from", sa.String(998), nullable=False),
        sa.Column("envelope_to_json", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(998), nullable=True),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("raw_storage_path", sa.String(500), nullable=True),
        sa.Column(
            "delivery_mode",
            sa.String(10),
            nullable=False,
            server_default="capture",
        ),
        sa.Column(
            "delivery_status",
            sa.String(10),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("relay_error", sa.String(500), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mailbox_profile_id"],
            ["mailbox_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbound_messages_mailbox_profile_id",
        "outbound_messages",
        ["mailbox_profile_id"],
    )
    op.create_index(
        "ix_outbound_messages_submitted_at",
        "outbound_messages",
        ["submitted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbound_messages_submitted_at", table_name="outbound_messages")
    op.drop_index(
        "ix_outbound_messages_mailbox_profile_id", table_name="outbound_messages"
    )
    op.drop_table("outbound_messages")
