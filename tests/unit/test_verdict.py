"""Unit tests for the policy verdict engine (app.policies.verdict).

No database or network required — fully functional tests.
"""

from __future__ import annotations

import pytest
from app.policies.constants import Verdict
from app.policies.verdict import VerdictThresholds, compute_verdict

# Default thresholds for reference
_T = VerdictThresholds()


# ---------------------------------------------------------------------------
# Boundary tests with default thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "risk,expected",
    [
        (0.00, Verdict.ALLOW),
        (0.25, Verdict.ALLOW),           # exactly at allow boundary → ALLOW
        (0.26, Verdict.ALLOW_WITH_BANNER),
        (0.45, Verdict.ALLOW_WITH_BANNER),
        (0.46, Verdict.SOFT_HOLD),
        (0.65, Verdict.SOFT_HOLD),
        (0.66, Verdict.QUARANTINE),
        (0.85, Verdict.QUARANTINE),
        (0.86, Verdict.ESCALATE_TO_ADMIN),
        (1.00, Verdict.ESCALATE_TO_ADMIN),
    ],
)
def test_verdict_thresholds(risk: float, expected: str):
    result = compute_verdict(overall_risk=risk, confidence=0.8)
    assert result == expected


# ---------------------------------------------------------------------------
# Gateway mode — top tier becomes REJECT
# ---------------------------------------------------------------------------


def test_gateway_mode_high_risk_is_reject():
    assert compute_verdict(0.90, 0.8, gateway_mode=True) == Verdict.REJECT


def test_gateway_mode_low_risk_is_allow():
    assert compute_verdict(0.10, 0.9, gateway_mode=True) == Verdict.ALLOW


def test_gateway_mode_mid_risk_is_quarantine():
    assert compute_verdict(0.70, 0.9, gateway_mode=True) == Verdict.QUARANTINE


# ---------------------------------------------------------------------------
# Degraded mode — raises effective risk by 0.10 when confidence < 0.5
# ---------------------------------------------------------------------------


def test_degraded_low_confidence_pushes_risk_up():
    # Risk 0.20 + 0.10 = 0.30 → ALLOW_WITH_BANNER
    result = compute_verdict(overall_risk=0.20, confidence=0.35, is_degraded=True)
    assert result == Verdict.ALLOW_WITH_BANNER


def test_degraded_high_confidence_no_adjustment():
    # Confidence >= 0.5: no adjustment; 0.20 → ALLOW
    result = compute_verdict(overall_risk=0.20, confidence=0.7, is_degraded=True)
    assert result == Verdict.ALLOW


def test_degraded_adjustment_capped_at_one():
    # Risk 0.95 + 0.10 would be 1.05, but capped at 1.0 — still ESCALATE
    result = compute_verdict(overall_risk=0.95, confidence=0.30, is_degraded=True)
    assert result == Verdict.ESCALATE_TO_ADMIN


def test_non_degraded_no_adjustment():
    # is_degraded=False: no risk adjustment regardless of confidence
    result = compute_verdict(overall_risk=0.20, confidence=0.10, is_degraded=False)
    assert result == Verdict.ALLOW


# ---------------------------------------------------------------------------
# Custom thresholds
# ---------------------------------------------------------------------------


def test_custom_thresholds():
    strict = VerdictThresholds(
        allow=0.10,
        allow_with_banner=0.20,
        soft_hold=0.30,
        quarantine=0.40,
    )
    assert compute_verdict(0.05, 0.9, thresholds=strict) == Verdict.ALLOW
    assert compute_verdict(0.15, 0.9, thresholds=strict) == Verdict.ALLOW_WITH_BANNER
    assert compute_verdict(0.25, 0.9, thresholds=strict) == Verdict.SOFT_HOLD
    assert compute_verdict(0.35, 0.9, thresholds=strict) == Verdict.QUARANTINE
    assert compute_verdict(0.50, 0.9, thresholds=strict) == Verdict.ESCALATE_TO_ADMIN


# ---------------------------------------------------------------------------
# VerdictThresholds defaults
# ---------------------------------------------------------------------------


def test_verdict_thresholds_defaults():
    t = VerdictThresholds()
    assert t.allow == 0.25
    assert t.allow_with_banner == 0.45
    assert t.soft_hold == 0.65
    assert t.quarantine == 0.85
