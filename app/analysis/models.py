"""SQLAlchemy ORM models for the analysis domain.

Two tables:
  analysis_runs    — one per analysis execution for a message
  dimension_scores — per-dimension score rows (one-to-many with analysis_runs)

Design choices:
  - One AnalysisRun per message (latest wins; re-analysis creates a new row).
  - DimensionScore rows are child records for clean querying and future trend analysis.
  - Deterministic findings and LLM evidence are stored as JSON text.
  - All enums use native_enum=False for SQLite test compatibility.
  - The degraded flag marks runs where the LLM was unavailable or returned invalid output.
"""

import enum

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AnalysisStatus(enum.StrEnum):
    """Lifecycle state of an analysis run."""

    PENDING = "pending"
    COMPLETE = "complete"
    DEGRADED = "degraded"   # LLM unavailable or output invalid — deterministic only
    FAILED = "failed"       # Unrecoverable error (rare)


class ModelProvider(enum.StrEnum):
    """LLM backend used for this analysis run."""

    OLLAMA = "ollama"
    NONE = "none"           # Deterministic-only run


class AnalysisRun(Base):
    """A single analysis execution for one ingested message.

    Multiple runs may exist for the same message (re-analysis is supported).
    The latest run is used for display.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Analysis versioning — bump when prompt or rule logic changes.
    analysis_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Model metadata
    model_provider: Mapped[str] = mapped_column(
        SAEnum(ModelProvider, native_enum=False, length=20),
        nullable=False,
        default=ModelProvider.OLLAMA,
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        SAEnum(AnalysisStatus, native_enum=False, length=20),
        nullable=False,
        default=AnalysisStatus.PENDING,
    )
    is_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Scores (0.0 - 1.0)
    deterministic_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Policy verdict
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Textual outputs
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON array of evidence strings
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON array of deterministic finding dicts
    deterministic_findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Raw LLM response (truncated to 8 KB for safety)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Child dimension scores
    dimension_scores: Mapped[list["DimensionScore"]] = relationship(
        "DimensionScore",
        back_populates="analysis_run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<AnalysisRun id={self.id} message_id={self.message_id} "
            f"status={self.status} verdict={self.verdict}>"
        )


class DimensionScore(Base):
    """Per-dimension score for one AnalysisRun."""

    __tablename__ = "dimension_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    analysis_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable identifier from ManipulationDimension enum
    dimension: Mapped[str] = mapped_column(String(60), nullable=False)

    # Score from 0.0 (not present) to 1.0 (maximally present)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Source: "llm", "deterministic", or "combined"
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")

    analysis_run: Mapped["AnalysisRun"] = relationship(
        "AnalysisRun", back_populates="dimension_scores"
    )

    def __repr__(self) -> str:
        return f"<DimensionScore dim={self.dimension} score={self.score:.2f}>"
