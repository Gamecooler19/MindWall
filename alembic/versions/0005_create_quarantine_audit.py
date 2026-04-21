"""Create quarantine_items and audit_events tables.

Revision ID: e5b2c1d8f047
Revises: d4a7f3c2e891
Create Date: 2026-04-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b2c1d8f047"
down_revision: str | None = "d4a7f3c2e891"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # quarantine_items
    # ------------------------------------------------------------------ #
    op.create_table(
        "quarantine_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column("trigger_verdict", sa.String(30), nullable=False),
        sa.Column("risk_score_snapshot", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_quarantine_items_message_id"),
    )
    op.create_index("ix_quarantine_items_message_id", "quarantine_items", ["message_id"])
    op.create_index(
        "ix_quarantine_items_analysis_run_id", "quarantine_items", ["analysis_run_id"]
    )
    op.create_index("ix_quarantine_items_status", "quarantine_items", ["status"])
    op.create_index(
        "ix_quarantine_items_reviewed_by_user_id",
        "quarantine_items",
        ["reviewed_by_user_id"],
    )

    # ------------------------------------------------------------------ #
    # audit_events
    # ------------------------------------------------------------------ #
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_type", sa.String(60), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_target_type", "audit_events", ["target_type"])
    op.create_index("ix_audit_events_target_id", "audit_events", ["target_id"])
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])
    # Composite index for fetching all events for a specific object
    op.create_index(
        "ix_audit_events_target_type_id",
        "audit_events",
        ["target_type", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_target_type_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_target_id", table_name="audit_events")
    op.drop_index("ix_audit_events_target_type", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index(
        "ix_quarantine_items_reviewed_by_user_id", table_name="quarantine_items"
    )
    op.drop_index("ix_quarantine_items_status", table_name="quarantine_items")
    op.drop_index(
        "ix_quarantine_items_analysis_run_id", table_name="quarantine_items"
    )
    op.drop_index("ix_quarantine_items_message_id", table_name="quarantine_items")
    op.drop_table("quarantine_items")
