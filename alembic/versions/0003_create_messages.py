"""Create messages, message_urls, and message_attachments tables.

Revision ID: c9f3b2e1d057
Revises: b7e4a1f0c832
Create Date: 2026-04-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f3b2e1d057"
down_revision: str | None = "b7e4a1f0c832"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # messages
    # ------------------------------------------------------------------ #
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mailbox_profile_id", sa.Integer(), nullable=True),
        # RFC 5322 identifiers
        sa.Column("message_id", sa.String(998), nullable=True),
        sa.Column("in_reply_to", sa.String(998), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        # Envelope
        sa.Column("subject", sa.String(998), nullable=True),
        sa.Column("from_address", sa.String(320), nullable=True),
        sa.Column("from_display_name", sa.String(255), nullable=True),
        sa.Column("reply_to_address", sa.String(320), nullable=True),
        sa.Column("to_addresses", sa.Text(), nullable=True),
        sa.Column("cc_addresses", sa.Text(), nullable=True),
        sa.Column("bcc_addresses", sa.Text(), nullable=True),
        sa.Column("date", sa.DateTime(timezone=True), nullable=True),
        # Body
        sa.Column(
            "has_text_plain",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "has_text_html",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("text_plain", sa.Text(), nullable=True),
        sa.Column("text_html_safe", sa.Text(), nullable=True),
        # Raw storage
        sa.Column("raw_size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("raw_storage_path", sa.String(500), nullable=True),
        # Authentication headers
        sa.Column("header_authentication_results", sa.Text(), nullable=True),
        sa.Column("header_received_spf", sa.String(500), nullable=True),
        sa.Column(
            "header_dkim_signature_present",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("header_x_mailer", sa.String(255), nullable=True),
        # Counters
        sa.Column(
            "num_attachments",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "num_urls",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Ingestion metadata
        sa.Column("ingestion_source", sa.String(30), nullable=False),
        # Base timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # Constraints
        sa.ForeignKeyConstraint(
            ["mailbox_profile_id"],
            ["mailbox_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_message_id", "messages", ["message_id"])
    op.create_index("ix_messages_from_address", "messages", ["from_address"])
    op.create_index("ix_messages_raw_sha256", "messages", ["raw_sha256"])
    op.create_index(
        "ix_messages_mailbox_profile_id", "messages", ["mailbox_profile_id"]
    )

    # ------------------------------------------------------------------ #
    # message_urls
    # ------------------------------------------------------------------ #
    op.create_table(
        "message_urls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("raw_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("scheme", sa.String(20), nullable=True),
        sa.Column("host", sa.String(253), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("link_text", sa.String(500), nullable=True),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_urls_message_id", "message_urls", ["message_id"])
    op.create_index("ix_message_urls_host", "message_urls", ["host"])

    # ------------------------------------------------------------------ #
    # message_attachments
    # ------------------------------------------------------------------ #
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=True),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column(
            "is_inline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("content_id", sa.String(500), nullable=True),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_message_attachments_message_id",
        "message_attachments",
        ["message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_attachments_message_id", table_name="message_attachments"
    )
    op.drop_table("message_attachments")

    op.drop_index("ix_message_urls_host", table_name="message_urls")
    op.drop_index("ix_message_urls_message_id", table_name="message_urls")
    op.drop_table("message_urls")

    op.drop_index("ix_messages_mailbox_profile_id", table_name="messages")
    op.drop_index("ix_messages_raw_sha256", table_name="messages")
    op.drop_index("ix_messages_from_address", table_name="messages")
    op.drop_index("ix_messages_message_id", table_name="messages")
    op.drop_table("messages")
