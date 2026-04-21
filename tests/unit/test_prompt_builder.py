"""Unit tests for app.analysis.prompt.

Tests the prompt builder, strict retry prompt, and LLM response parser.
No network or database access.
"""

from __future__ import annotations

import json

from app.analysis.deterministic import DeterministicResult, Finding
from app.analysis.prompt import (
    PROMPT_VERSION,
    LLMAnalysisResponse,
    build_analysis_prompt,
    build_strict_retry_prompt,
    parse_llm_response,
)
from app.messages.schemas import ParsedMessage
from app.policies.constants import ManipulationDimension

_ALL_DIMS = {d.value: 0.0 for d in ManipulationDimension}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_msg(**overrides) -> ParsedMessage:
    defaults = {
        "raw_size_bytes": 100,
        "raw_sha256": "abc",
        "raw_storage_path": None,
        "message_id": "<test@example.com>",
        "in_reply_to": None,
        "references": None,
        "subject": "Test subject",
        "from_address": "sender@example.com",
        "from_display_name": None,
        "reply_to_address": None,
        "to_addresses": ["user@example.com"],
        "cc_addresses": [],
        "bcc_addresses": [],
        "date": None,
        "has_text_plain": True,
        "has_text_html": False,
        "text_plain": "Hello, this is a test message.",
        "text_html_safe": None,
        "header_authentication_results": None,
        "header_received_spf": None,
        "header_dkim_signature_present": True,
        "header_x_mailer": None,
        "urls": [],
        "attachments": [],
    }
    defaults.update(overrides)
    return ParsedMessage(**defaults)


def _empty_det_result() -> DeterministicResult:
    return DeterministicResult(
        findings=[],
        risk_score=0.0,
        dimension_scores={},
    )


def _valid_llm_response_dict(**overrides) -> dict:
    base = {
        "overall_risk": 0.5,
        "manipulation_dimensions": {d.value: 0.1 for d in ManipulationDimension},
        "summary": "This is a test summary.",
        "evidence": ["Signal 1", "Signal 2"],
        "recommended_action": "allow_with_banner",
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# build_analysis_prompt
# ---------------------------------------------------------------------------


def test_prompt_contains_all_12_dimensions():
    msg = _base_msg()
    det = _empty_det_result()
    prompt = build_analysis_prompt(msg, det)
    for dim in ManipulationDimension:
        assert dim.value in prompt, f"Dimension {dim.value!r} missing from prompt"


def test_prompt_contains_from_address():
    msg = _base_msg(from_address="phisher@evil.com")
    det = _empty_det_result()
    prompt = build_analysis_prompt(msg, det)
    assert "phisher@evil.com" in prompt


def test_prompt_contains_subject():
    msg = _base_msg(subject="Urgent: verify your account")
    det = _empty_det_result()
    prompt = build_analysis_prompt(msg, det)
    assert "Urgent: verify your account" in prompt


def test_prompt_contains_deterministic_evidence():
    det = DeterministicResult(
        findings=[
            Finding(
                rule="urgency_language",
                description="Urgency detected",
                severity=0.6,
                dimensions=["urgency_pressure"],
                evidence="Urgency words found in body",
            )
        ],
        risk_score=0.6,
        dimension_scores={"urgency_pressure": 0.6},
    )
    msg = _base_msg()
    prompt = build_analysis_prompt(msg, det)
    assert "Urgency words found in body" in prompt


def test_prompt_body_truncated():
    long_body = "x" * 5_000
    msg = _base_msg(text_plain=long_body)
    det = _empty_det_result()
    prompt = build_analysis_prompt(msg, det)
    # Body should be truncated (2000 char limit) so prompt is reasonable
    assert len(prompt) < 15_000


def test_prompt_contains_json_instruction():
    msg = _base_msg()
    det = _empty_det_result()
    prompt = build_analysis_prompt(msg, det)
    assert "JSON" in prompt or "json" in prompt


# ---------------------------------------------------------------------------
# build_strict_retry_prompt
# ---------------------------------------------------------------------------


def test_strict_retry_prompt_extends_original():
    original = "Analyse this email."
    retry = build_strict_retry_prompt(original)
    assert original in retry
    assert len(retry) > len(original)


# ---------------------------------------------------------------------------
# parse_llm_response — success path
# ---------------------------------------------------------------------------


def test_parse_valid_json():
    raw = json.dumps(_valid_llm_response_dict())
    result = parse_llm_response(raw)
    assert result is not None
    assert result.overall_risk == 0.5
    assert result.confidence == 0.8
    assert result.is_valid()


def test_parse_strips_markdown_code_fences():
    raw = "```json\n" + json.dumps(_valid_llm_response_dict()) + "\n```"
    result = parse_llm_response(raw)
    assert result is not None
    assert result.is_valid()


def test_parse_strips_json_code_fence_label():
    raw = "Here is my analysis:\n```json\n" + json.dumps(_valid_llm_response_dict()) + "\n```\n"
    result = parse_llm_response(raw)
    assert result is not None


def test_parse_scores_clamped_to_0_1():
    d = _valid_llm_response_dict(overall_risk=1.5, confidence=-0.1)
    raw = json.dumps(d)
    result = parse_llm_response(raw)
    assert result is not None
    assert result.overall_risk == 1.0
    assert result.confidence == 0.0

# ---------------------------------------------------------------------------
# parse_llm_response — failure paths
# ---------------------------------------------------------------------------


def test_parse_returns_none_on_empty_string():
    assert parse_llm_response("") is None


def test_parse_returns_none_on_plain_text():
    assert parse_llm_response("Sorry, I cannot process that request.") is None


def test_parse_returns_none_on_missing_dimensions():
    d = _valid_llm_response_dict()
    # Remove one dimension
    del d["manipulation_dimensions"]["urgency_pressure"]
    result = parse_llm_response(json.dumps(d))
    assert result is None


def test_parse_returns_none_on_invalid_json():
    assert parse_llm_response("{not valid json}") is None


def test_parse_returns_none_on_wrong_structure():
    assert parse_llm_response(json.dumps({"foo": "bar"})) is None


# ---------------------------------------------------------------------------
# LLMAnalysisResponse.is_valid
# ---------------------------------------------------------------------------


def test_llm_response_is_valid_all_present():
    r = LLMAnalysisResponse(
        overall_risk=0.5,
        dimension_scores={d.value: 0.1 for d in ManipulationDimension},
        summary="ok",
        evidence=[],
        recommended_action="allow",
        confidence=0.9,
    )
    assert r.is_valid()


def test_llm_response_not_valid_missing_dimension():
    scores = {d.value: 0.1 for d in ManipulationDimension}
    del scores["urgency_pressure"]
    r = LLMAnalysisResponse(
        overall_risk=0.5,
        dimension_scores=scores,
        summary="ok",
        evidence=[],
        recommended_action="allow",
        confidence=0.9,
    )
    assert not r.is_valid()


def test_llm_response_not_valid_out_of_range():
    scores = {d.value: 1.5 for d in ManipulationDimension}  # out of range
    r = LLMAnalysisResponse(
        overall_risk=0.5,
        dimension_scores=scores,
        summary="ok",
        evidence=[],
        recommended_action="allow",
        confidence=0.9,
    )
    assert not r.is_valid()


# ---------------------------------------------------------------------------
# PROMPT_VERSION is a string
# ---------------------------------------------------------------------------


def test_prompt_version_is_string():
    assert isinstance(PROMPT_VERSION, str)
    assert len(PROMPT_VERSION) > 0
