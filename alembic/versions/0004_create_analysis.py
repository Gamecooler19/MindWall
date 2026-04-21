"""Create analysis_runs and dimension_scores tables.

Revision ID: d4a7f3c2e891
Revises: c9f3b2e1d057
Create Date: 2026-04-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7f3c2e891"
down_revision: str | None = "c9f3b2e1d057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # analysis_runs
    # ------------------------------------------------------------------ #
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("analysis_version", sa.String(20), nullable=False, server_default="1.0"),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column("model_provider", sa.String(20), nullable=False, server_default="ollama"),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "is_degraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("deterministic_risk_score", sa.Float(), nullable=True),
        sa.Column("llm_risk_score", sa.Float(), nullable=True),
        sa.Column("overall_risk_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(30), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("deterministic_findings_json", sa.Text(), nullable=True),
        sa.Column("llm_raw_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_runs_message_id", "analysis_runs", ["message_id"])
    op.create_index("ix_analysis_runs_verdict", "analysis_runs", ["verdict"])

    # ------------------------------------------------------------------ #
    # dimension_scores
    # ------------------------------------------------------------------ #
    op.create_table(
        "dimension_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("dimension", sa.String(60), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("source", sa.String(20), nullable=False, server_default="llm"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dimension_scores_analysis_run_id",
        "dimension_scores",
        ["analysis_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dimension_scores_analysis_run_id", table_name="dimension_scores")
    op.drop_table("dimension_scores")

    op.drop_index("ix_analysis_runs_verdict", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_message_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
