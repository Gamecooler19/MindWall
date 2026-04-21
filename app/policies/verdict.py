"""Policy verdict engine for Mindwall.

Maps a combined risk score, confidence, and dimension scores to a stable
policy verdict using configurable thresholds.

Verdict identifiers are defined in app.policies.constants.Verdict.

Design:
  - Thresholds are passed in as arguments (loaded from Settings) so tests
    can override them without monkey-patching.
  - The verdict algorithm is simple and auditable — no ML, no black boxes.
  - Degraded mode (LLM unavailable) can push towards more conservative
    verdicts based on deterministic score alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.policies.constants import Verdict


@dataclass
class VerdictThresholds:
    """Risk-score thresholds for each verdict level (inclusive upper bound)."""

    allow: float = 0.25
    allow_with_banner: float = 0.45
    soft_hold: float = 0.65
    quarantine: float = 0.85
    # Scores above quarantine threshold → escalate_to_admin (or reject in gateway mode)


def compute_verdict(
    overall_risk: float,
    confidence: float,
    is_degraded: bool = False,
    thresholds: VerdictThresholds | None = None,
    gateway_mode: bool = False,
) -> str:
    """Return the appropriate Verdict value for the given risk parameters.

    Args:
        overall_risk:  Combined risk score in [0.0, 1.0].
        confidence:    Model/analysis confidence in [0.0, 1.0].
        is_degraded:   True when LLM was unavailable (uses conservative adjustment).
        thresholds:    Configurable verdict thresholds (defaults used if None).
        gateway_mode:  If True, top-tier verdict becomes REJECT instead of ESCALATE.

    Returns:
        One of the stable Verdict string values.
    """
    t = thresholds or VerdictThresholds()

    # When degraded, shift the effective score upward slightly to prefer caution
    effective_risk = overall_risk
    if is_degraded and confidence < 0.5:
        effective_risk = min(1.0, overall_risk + 0.10)

    if effective_risk <= t.allow:
        return Verdict.ALLOW
    if effective_risk <= t.allow_with_banner:
        return Verdict.ALLOW_WITH_BANNER
    if effective_risk <= t.soft_hold:
        return Verdict.SOFT_HOLD
    if effective_risk <= t.quarantine:
        return Verdict.QUARANTINE
    # Above quarantine threshold
    if gateway_mode:
        return Verdict.REJECT
    return Verdict.ESCALATE_TO_ADMIN
