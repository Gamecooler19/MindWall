"""Analysis orchestrator for Mindwall.

This service coordinates:
  1. Deterministic security checks (always runs)
  2. Ollama LLM analysis (optional, can be disabled or may fail)
  3. Score combination (deterministic + LLM → overall risk)
  4. Policy verdict computation
  5. Persistence of the AnalysisRun + DimensionScore records

Entry points:
  run_analysis          — run the full pipeline for a message
  get_latest_analysis   — load the most recent AnalysisRun for a message
"""

from __future__ import annotations

import json

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analysis.deterministic import run_deterministic_checks
from app.analysis.models import AnalysisRun, AnalysisStatus, DimensionScore, ModelProvider
from app.analysis.ollama_client import OllamaClient, OllamaError
from app.analysis.prompt import (
    PROMPT_VERSION,
    LLMAnalysisResponse,
    build_analysis_prompt,
    build_strict_retry_prompt,
    parse_llm_response,
)
from app.messages.models import Message
from app.messages.schemas import ParsedMessage
from app.policies.constants import ManipulationDimension
from app.policies.verdict import VerdictThresholds, compute_verdict

log = structlog.get_logger(__name__)

# Version of the analysis pipeline logic — bump when deterministic rules change.
ANALYSIS_VERSION = "1.0"


def _build_parsed_message_from_orm(msg: Message) -> ParsedMessage:
    """Reconstruct a ParsedMessage DTO from an ORM Message record.

    We need a ParsedMessage for the deterministic checks and prompt builder.
    Rather than re-reading from disk, we reconstruct it from what we already
    persisted.
    """
    import json as _json

    to_addresses: list[str] = _json.loads(msg.to_addresses) if msg.to_addresses else []
    cc_addresses: list[str] = _json.loads(msg.cc_addresses) if msg.cc_addresses else []
    bcc_addresses: list[str] = _json.loads(msg.bcc_addresses) if msg.bcc_addresses else []

    from app.messages.schemas import ExtractedAttachment, ExtractedUrl

    urls = [
        ExtractedUrl(
            raw_url=u.raw_url,
            normalized_url=u.normalized_url,
            scheme=u.scheme or "",
            host=u.host or "",
            path=u.path or "",
            source=u.source,
            link_text=u.link_text,
        )
        for u in (msg.urls or [])
    ]

    attachments = [
        ExtractedAttachment(
            filename=a.filename,
            content_type=a.content_type,
            size_bytes=a.size_bytes,
            sha256=a.sha256,
            is_inline=a.is_inline,
            content_id=a.content_id,
        )
        for a in (msg.attachments or [])
    ]

    return ParsedMessage(
        raw_size_bytes=msg.raw_size_bytes,
        raw_sha256=msg.raw_sha256,
        raw_storage_path=msg.raw_storage_path,
        message_id=msg.message_id,
        in_reply_to=msg.in_reply_to,
        references=msg.references,
        subject=msg.subject,
        from_address=msg.from_address,
        from_display_name=msg.from_display_name,
        reply_to_address=msg.reply_to_address,
        to_addresses=to_addresses,
        cc_addresses=cc_addresses,
        bcc_addresses=bcc_addresses,
        date=msg.date,
        has_text_plain=msg.has_text_plain,
        has_text_html=msg.has_text_html,
        text_plain=msg.text_plain,
        text_html_safe=msg.text_html_safe,
        header_authentication_results=msg.header_authentication_results,
        header_received_spf=msg.header_received_spf,
        header_dkim_signature_present=msg.header_dkim_signature_present,
        header_x_mailer=msg.header_x_mailer,
        urls=urls,
        attachments=attachments,
    )


def _combine_scores(
    det_risk: float,
    llm_result: LLMAnalysisResponse | None,
    is_degraded: bool,
) -> tuple[float, float, dict[str, float]]:
    """Combine deterministic and LLM scores into a single overall risk.

    Returns:
        (overall_risk, confidence, merged_dimension_scores)
    """
    all_dims = {d.value: 0.0 for d in ManipulationDimension}

    if llm_result is None or is_degraded:
        # Degraded: use deterministic score only, low confidence
        overall = det_risk
        confidence = 0.35
        return overall, confidence, all_dims

    # Weighted combination: LLM is more expressive but deterministic is hard evidence
    # 40% deterministic, 60% LLM
    overall = round(det_risk * 0.40 + llm_result.overall_risk * 0.60, 4)
    confidence = llm_result.confidence

    # Merge dimension scores: take the max of deterministic and LLM for each dim
    for d in ManipulationDimension:
        llm_score = llm_result.dimension_scores.get(d.value, 0.0)
        all_dims[d.value] = round(llm_score, 4)

    return overall, confidence, all_dims


async def run_analysis(
    db: AsyncSession,
    msg: Message,
    ollama_client: OllamaClient | None,
    llm_enabled: bool = True,
    thresholds: VerdictThresholds | None = None,
    gateway_mode: bool = False,
) -> AnalysisRun:
    """Run the full Mindwall analysis pipeline for a single message.

    Steps:
      1. Reconstruct ParsedMessage from ORM record
      2. Run deterministic checks
      3. Build LLM prompt
      4. Call Ollama (if enabled and client provided)
      5. Parse + validate LLM response (retry once if malformed)
      6. Combine scores
      7. Compute verdict
      8. Persist AnalysisRun + DimensionScore records

    Args:
        db:             Database session.
        msg:            ORM Message with URLs and attachments loaded.
        ollama_client:  Local Ollama client instance. Pass None to skip LLM.
        llm_enabled:    Feature flag — False forces deterministic-only mode.
        thresholds:     Verdict thresholds (uses defaults if None).
        gateway_mode:   True enables REJECT verdict at the top end.

    Returns:
        The committed AnalysisRun record.
    """
    log.info(
        "analysis.starting",
        message_id=msg.id,
        subject=msg.subject,
        llm_enabled=llm_enabled,
    )

    # ------------------------------------------------------------------ #
    # Step 1: Build ParsedMessage from ORM
    # ------------------------------------------------------------------ #
    parsed = _build_parsed_message_from_orm(msg)

    # ------------------------------------------------------------------ #
    # Step 2: Deterministic checks
    # ------------------------------------------------------------------ #
    det_result = run_deterministic_checks(parsed)
    log.debug(
        "analysis.deterministic_complete",
        message_id=msg.id,
        num_findings=len(det_result.findings),
        det_risk=det_result.risk_score,
    )

    # ------------------------------------------------------------------ #
    # Step 3 & 4: LLM analysis
    # ------------------------------------------------------------------ #
    llm_result: LLMAnalysisResponse | None = None
    llm_raw: str | None = None
    is_degraded = False
    model_name: str | None = None

    if llm_enabled and ollama_client is not None:
        prompt = build_analysis_prompt(parsed, det_result)
        try:
            response = await ollama_client.generate(prompt)
            llm_raw = response.raw_text
            model_name = response.model
            llm_result = parse_llm_response(response.raw_text)

            if llm_result is None:
                # Retry with stricter instructions
                log.warning("analysis.llm_parse_failed_retrying", message_id=msg.id)
                retry_prompt = build_strict_retry_prompt(prompt)
                retry_response = await ollama_client.generate(retry_prompt)
                llm_raw = retry_response.raw_text
                llm_result = parse_llm_response(retry_response.raw_text)

            if llm_result is None:
                log.warning(
                    "analysis.llm_parse_failed_after_retry",
                    message_id=msg.id,
                )
                is_degraded = True

        except OllamaError as exc:
            log.warning(
                "analysis.ollama_error_degraded",
                message_id=msg.id,
                error=str(exc),
            )
            is_degraded = True
        except Exception:
            log.exception("analysis.llm_unexpected_error", message_id=msg.id)
            is_degraded = True
    else:
        is_degraded = ollama_client is None or not llm_enabled
        if not llm_enabled:
            log.info("analysis.llm_disabled", message_id=msg.id)

    # ------------------------------------------------------------------ #
    # Step 5: Combine scores
    # ------------------------------------------------------------------ #
    overall_risk, confidence, merged_dims = _combine_scores(
        det_result.risk_score, llm_result, is_degraded
    )

    # ------------------------------------------------------------------ #
    # Step 6: Policy verdict
    # ------------------------------------------------------------------ #
    verdict = compute_verdict(
        overall_risk=overall_risk,
        confidence=confidence,
        is_degraded=is_degraded,
        thresholds=thresholds,
        gateway_mode=gateway_mode,
    )

    # ------------------------------------------------------------------ #
    # Step 7: Build evidence list
    # ------------------------------------------------------------------ #
    evidence: list[str] = det_result.to_evidence_list()
    if llm_result:
        evidence.extend(llm_result.evidence)

    rationale = (llm_result.summary if llm_result else None) or (
        f"Deterministic-only analysis. Risk score: {overall_risk:.2f}. "
        f"{len(det_result.findings)} finding(s) detected."
        if det_result.findings
        else "No significant risk indicators detected."
    )

    # ------------------------------------------------------------------ #
    # Step 8: Persist
    # ------------------------------------------------------------------ #
    provider = ModelProvider.OLLAMA if (llm_enabled and not is_degraded) else ModelProvider.NONE
    status = AnalysisStatus.DEGRADED if is_degraded else AnalysisStatus.COMPLETE

    run = AnalysisRun(
        message_id=msg.id,
        analysis_version=ANALYSIS_VERSION,
        prompt_version=PROMPT_VERSION if not is_degraded else None,
        model_provider=provider,
        model_name=model_name,
        status=status,
        is_degraded=is_degraded,
        deterministic_risk_score=round(det_result.risk_score, 4),
        llm_risk_score=round(llm_result.overall_risk, 4) if llm_result else None,
        overall_risk_score=round(overall_risk, 4),
        confidence=round(confidence, 4),
        verdict=verdict,
        rationale=rationale,
        evidence_json=json.dumps(evidence[:20]),
        deterministic_findings_json=json.dumps(
            [f.to_dict() for f in det_result.findings]
        ),
        llm_raw_response=llm_raw[:8000] if llm_raw else None,
    )
    db.add(run)
    await db.flush()

    for dim, score in merged_dims.items():
        source = "llm" if (llm_result and not is_degraded) else "deterministic"
        # Override with deterministic score for dims where we had findings
        det_score = det_result.dimension_scores.get(dim, 0.0)
        final_score = max(score, det_score)
        db.add(
            DimensionScore(
                analysis_run_id=run.id,
                dimension=dim,
                score=round(final_score, 4),
                source=source,
            )
        )

    await db.commit()
    await db.refresh(run)

    log.info(
        "analysis.complete",
        message_id=msg.id,
        run_id=run.id,
        verdict=verdict,
        overall_risk=overall_risk,
        is_degraded=is_degraded,
    )

    return run


async def get_latest_analysis(
    db: AsyncSession,
    message_id: int,
) -> AnalysisRun | None:
    """Return the most recent AnalysisRun for a message, with dimension scores loaded."""
    result = await db.execute(
        select(AnalysisRun)
        .where(AnalysisRun.message_id == message_id)
        .options(selectinload(AnalysisRun.dimension_scores))
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
