"""Create mailbox_profiles table.

Revision ID: b7e4a1f0c832
Revises: a3f1d8e29c01
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4a1f0c832"
down_revision: Union[str, None] = "a3f1d8e29c01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mailbox_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("email_address", sa.String(255), nullable=False),
        # IMAP upstream
        sa.Column("imap_host", sa.String(255), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False),
        sa.Column("imap_username", sa.String(255), nullable=False),
        sa.Column("imap_password_enc", sa.Text(), nullable=False),
        # ImapSecurity enum — stored as VARCHAR (native_enum=False)
        sa.Column(
            "imap_security",
            sa.String(20),
            nullable=False,
            server_default="ssl_tls",
        ),
        # SMTP upstream
        sa.Column("smtp_host", sa.String(255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_username", sa.String(255), nullable=False),
        sa.Column("smtp_password_enc", sa.Text(), nullable=False),
        # SmtpSecurity enum — stored as VARCHAR (native_enum=False)
        sa.Column(
            "smtp_security",
            sa.String(20),
            nullable=False,
            server_default="starttls",
        ),
        # MailboxStatus enum — stored as VARCHAR (native_enum=False)
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="pending_verification",
        ),
        # Proxy credentials
        sa.Column("proxy_username", sa.String(100), nullable=True),
        sa.Column("proxy_password_hash", sa.String(255), nullable=True),
        # Connectivity check
        sa.Column("last_connection_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_connection_error", sa.String(500), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # Constraints
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mailbox_profiles_owner_id",
        "mailbox_profiles",
        ["owner_id"],
    )
    op.create_index(
        "ix_mailbox_profiles_email_address",
        "mailbox_profiles",
        ["email_address"],
    )
    op.create_index(
        "ix_mailbox_profiles_proxy_username",
        "mailbox_profiles",
        ["proxy_username"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_mailbox_profiles_proxy_username", table_name="mailbox_profiles")
    op.drop_index("ix_mailbox_profiles_email_address", table_name="mailbox_profiles")
    op.drop_index("ix_mailbox_profiles_owner_id", table_name="mailbox_profiles")
    op.drop_table("mailbox_profiles")
