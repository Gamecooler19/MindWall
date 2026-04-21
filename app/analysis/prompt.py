"""Prompt builder and structured response parser for Mindwall LLM analysis.

Design constraints:
  - Prompts are compact and deterministic (same input → same prompt).
  - The expected output is strict JSON matching the LLMAnalysisResponse schema.
  - Malformed output triggers one retry with a stricter prompt, then degradation.
  - All 12 ManipulationDimension identifiers are baked into the prompt contract.
  - Prompt version is tracked so re-analysis after prompt changes is detectable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.analysis.deterministic import DeterministicResult
from app.messages.schemas import ParsedMessage
from app.policies.constants import DIMENSION_LABELS, ManipulationDimension

# Bump this when the prompt schema changes — triggers re-analysis in future.
PROMPT_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

_ALL_DIMENSIONS = [d.value for d in ManipulationDimension]

_DIMENSION_DESCRIPTIONS: dict[str, str] = {
    ManipulationDimension.AUTHORITY_PRESSURE: (
        "Invokes authority figures, organisations, or officials to compel action"
    ),
    ManipulationDimension.URGENCY_PRESSURE: (
        "Creates artificial time pressure or urgency to prevent careful reflection"
    ),
    ManipulationDimension.SCARCITY: (
        "Implies limited availability of an opportunity, resource, or outcome"
    ),
    ManipulationDimension.FEAR_THREAT: (
        "Uses fear, threats, or warnings of negative consequences"
    ),
    ManipulationDimension.REWARD_LURE: (
        "Promises reward, prize, compensation, or financial gain"
    ),
    ManipulationDimension.CURIOSITY_BAIT: (
        "Baits with incomplete information or intrigue to drive clicks"
    ),
    ManipulationDimension.RECIPROCITY_OBLIGATION: (
        "Creates a sense of obligation or debt to prompt compliance"
    ),
    ManipulationDimension.SOCIAL_PROOF: (
        "Claims that others have already complied or approved"
    ),
    ManipulationDimension.SECRECY_ISOLATION: (
        "Asks recipient to keep the message private or act alone"
    ),
    ManipulationDimension.IMPERSONATION: (
        "Pretends to be a trusted entity, brand, or individual"
    ),
    ManipulationDimension.COMPLIANCE_ESCALATION: (
        "Starts with a small request and escalates to larger compliance"
    ),
    ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE: (
        "Attempts to obtain credentials, personal data, or money"
    ),
}


@dataclass
class LLMAnalysisResponse:
    """Parsed, validated output from the local LLM analysis."""

    overall_risk: float
    dimension_scores: dict[str, float]
    summary: str
    evidence: list[str]
    recommended_action: str
    confidence: float

    def is_valid(self) -> bool:
        """Basic sanity check on the parsed output."""
        if not (0.0 <= self.overall_risk <= 1.0):
            return False
        if not (0.0 <= self.confidence <= 1.0):
            return False
        if len(self.dimension_scores) != len(ManipulationDimension):
            return False
        for d, s in self.dimension_scores.items():
            if d not in _ALL_DIMENSIONS:
                return False
            if not (0.0 <= s <= 1.0):
                return False
        return True


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_MAX_BODY_CHARS = 2_000    # Limit body chars sent to LLM
_MAX_URLS = 10             # Max URLs listed in prompt
_MAX_EVIDENCE_ITEMS = 8    # Max deterministic evidence items in prompt


def build_analysis_prompt(
    msg: ParsedMessage,
    det_result: DeterministicResult,
) -> str:
    """Build a compact, structured prompt for Mindwall phishing analysis.

    The prompt instructs the model to return strict JSON matching the
    LLMAnalysisResponse schema.  It includes:
      - Envelope metadata (sender, subject, date)
      - Truncated body text
      - Extracted URLs
      - Deterministic evidence gathered before the LLM call

    Returns:
        The complete prompt string ready to send to Ollama.
    """
    # ---- Envelope section ----
    envelope_lines = [
        f"Subject: {msg.subject or '(none)'}",
        f"From: {msg.from_display_name or ''} <{msg.from_address or ''}>",
    ]
    if msg.reply_to_address:
        envelope_lines.append(f"Reply-To: {msg.reply_to_address}")
    if msg.to_addresses:
        envelope_lines.append(f"To: {', '.join(msg.to_addresses[:3])}")

    # ---- Auth signals ----
    auth_lines = []
    if msg.header_dkim_signature_present:
        auth_lines.append("DKIM-Signature: present")
    else:
        auth_lines.append("DKIM-Signature: absent")
    if msg.header_received_spf:
        auth_lines.append(f"SPF: {msg.header_received_spf[:80]}")
    if msg.header_authentication_results:
        auth_lines.append(f"Auth-Results: {msg.header_authentication_results[:80]}")

    # ---- Body ----
    body_text = ""
    if msg.text_plain:
        body_text = msg.text_plain[:_MAX_BODY_CHARS]
    elif msg.text_html_safe:
        body_text = msg.text_html_safe[:_MAX_BODY_CHARS]
    if len(body_text) == _MAX_BODY_CHARS:
        body_text += "\n[... body truncated ...]"

    # ---- URLs ----
    url_lines = []
    for url in msg.urls[:_MAX_URLS]:
        entry = f"  - {url.raw_url[:80]}"
        if url.link_text:
            entry += f"  (link text: '{url.link_text[:40]}')"
        url_lines.append(entry)

    # ---- Deterministic evidence ----
    det_evidence = det_result.to_evidence_list()[:_MAX_EVIDENCE_ITEMS]

    # ---- Dimension spec ----
    dim_spec_lines = []
    for d in ManipulationDimension:
        label = DIMENSION_LABELS[d.value]
        desc = _DIMENSION_DESCRIPTIONS[d.value]
        dim_spec_lines.append(f'  "{d.value}": <0.0-1.0>  // {label}: {desc}')

    prompt = f"""You are Mindwall, a security-focused email analysis system running locally.
Analyse the email below for phishing and psychological manipulation.
Respond with ONLY a valid JSON object — no prose, no markdown, no code fences.

=== EMAIL ===
{chr(10).join(envelope_lines)}

Authentication:
{chr(10).join(auth_lines) or "  (none)"}

Body:
{body_text or "(empty)"}

URLs in message ({len(msg.urls)} total, showing up to {_MAX_URLS}):
{chr(10).join(url_lines) or "  (none)"}

Pre-computed security findings ({len(det_evidence)} items):
{chr(10).join("  - " + e for e in det_evidence) or "  (none)"}

=== TASK ===
Score each of the 12 manipulation dimensions from 0.0 (not present) to 1.0 (strongly present):

{chr(10).join(dim_spec_lines)}

Return this exact JSON structure:
{{
  "overall_risk": <0.0-1.0>,
  "manipulation_dimensions": {{
    "authority_pressure": <score>,
    "urgency_pressure": <score>,
    "scarcity": <score>,
    "fear_threat": <score>,
    "reward_lure": <score>,
    "curiosity_bait": <score>,
    "reciprocity_obligation": <score>,
    "social_proof": <score>,
    "secrecy_isolation": <score>,
    "impersonation": <score>,
    "compliance_escalation": <score>,
    "credential_or_payment_capture": <score>
  }},
  "summary": "<one sentence summary of the main risk>",
  "evidence": ["<specific evidence item 1>", "<specific evidence item 2>"],
  "recommended_action": "<allow|allow_with_banner|soft_hold|quarantine|reject>",
  "confidence": <0.0-1.0>
}}"""

    return prompt


def build_strict_retry_prompt(prompt: str) -> str:
    """Return a stricter version of the prompt for the retry attempt.

    Used when the first response was malformed JSON.  This version is more
    explicit about not including anything other than JSON.
    """
    return (
        "IMPORTANT: Your previous response was not valid JSON.\n"
        "You MUST respond with ONLY a raw JSON object — "
        "no markdown, no code fences, no text before or after the JSON.\n"
        "Start your response with '{' and end with '}'.\n\n"
        + prompt
    )


# ---------------------------------------------------------------------------
# Response parser / validator
# ---------------------------------------------------------------------------


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def parse_llm_response(raw_text: str) -> LLMAnalysisResponse | None:
    """Parse and validate the raw LLM response text.

    Returns None if the response cannot be parsed or fails validation.
    """
    if not raw_text:
        return None

    # Strip markdown code fences if the model ignored instructions
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Extract the first JSON object block
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start == -1 or brace_end == -1:
        return None

    json_text = text[brace_start : brace_end + 1]

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    try:
        overall_risk = _clamp(float(data.get("overall_risk", 0.0)))
        confidence = _clamp(float(data.get("confidence", 0.5)))
        summary = str(data.get("summary", ""))[:500]
        recommended_action = str(data.get("recommended_action", "allow"))[:30]
        evidence_raw = data.get("evidence", [])
        evidence = [str(e)[:200] for e in evidence_raw if isinstance(e, str)][:10]

        # Dimension scores — accept both "manipulation_dimensions" key and flat keys
        dim_data = data.get("manipulation_dimensions", data)
        dimension_scores: dict[str, float] = {}
        for d in ManipulationDimension:
            raw_score = dim_data.get(d.value)
            if raw_score is None:
                return None  # Missing required dimension
            dimension_scores[d.value] = _clamp(float(raw_score))

    except (TypeError, ValueError, AttributeError):
        return None

    result = LLMAnalysisResponse(
        overall_risk=overall_risk,
        dimension_scores=dimension_scores,
        summary=summary,
        evidence=evidence,
        recommended_action=recommended_action,
        confidence=confidence,
    )

    return result if result.is_valid() else None
