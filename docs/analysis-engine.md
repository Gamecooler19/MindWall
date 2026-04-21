# Analysis engine

The Mindwall analysis engine combines deterministic rule-based checks with local LLM inference to produce a risk score, a per-dimension breakdown, and an explainable verdict for every message.

---

## Pipeline overview

```
Parsed message (ParsedMessage)
        │
        ▼
1. Deterministic checks ─────────────────────────────────────────┐
   (app/analysis/deterministic.py)                               │
   • Pure Python, no network calls                                │
   • Returns: List[Finding], deterministic_risk: float           │
   • Findings injected into LLM prompt as evidence               │
        │                                                         │
        ▼                                                         │
2. LLM analysis (if LLM_ENABLED=true)                           │
   (app/analysis/ollama_client.py + app/analysis/prompt.py)      │
   • Builds structured prompt                                     │
   • Calls local Ollama /api/generate                             │
   • Parses strict JSON response                                  │
   • Retries once with stricter schema on malformed output        │
   • Returns: LLMAnalysisResponse or None                        │
        │                                                         │
        ▼                                                         │
3. Score combination                                             │
   overall_risk = 0.4 × deterministic_risk + 0.6 × llm_risk    │
   (falls back to deterministic_risk if LLM unavailable)         │
        │                                                         │
        ▼                                                         │
4. Verdict computation                                           │
   (app/policies/verdict.py)                                     │
   • Applies configurable thresholds                             │
   • Adjusts for degraded mode (low confidence → +0.10 risk)    │
   • Gateway mode enables reject verdict                         │
        │                                                         │
        ▼                                                         │
5. Persistence                                                   │
   • AnalysisRun row (one per run)                               │
   • DimensionScore rows (12 per run)                            │
   • QuarantineItem + AuditEvent + Alert if verdict requires it  │
```

---

## Deterministic checks

Implemented in `app/analysis/deterministic.py`. All checks are pure Python with no network calls.

| Check | Dimensions targeted | Notes |
|-------|-------------------|-------|
| Display-name / From-address mismatch | `impersonation` | e.g. "PayPal Support" in display name but from `random@gmail.com` |
| Reply-To mismatch | `impersonation`, `credential_or_payment_capture` | Reply-To differs from From domain |
| Brand impersonation patterns | `impersonation`, `authority_pressure` | Regex patterns for known brand names in envelope fields |
| Link-text / href mismatch | `credential_or_payment_capture`, `impersonation` | Anchor text says one domain, href goes to another |
| Suspicious URL structure | `credential_or_payment_capture` | IP-address hosts, deep subdomain chains, credential keywords in path |
| Risky attachment types | `credential_or_payment_capture` | `.exe`, `.ps1`, `.docm`, `.jar`, `.iso`, etc. |
| Medium-risk attachment types | — | Archives (`.zip`, `.rar`, etc.) |
| Credential/payment language | `credential_or_payment_capture`, `urgency_pressure` | Regex patterns for password reset, account suspension, payment capture |
| Urgency/fear language | `urgency_pressure`, `fear_threat` | Time pressure and threat language patterns |
| Missing DKIM | `impersonation` | No DKIM-Signature header |
| Missing SPF pass | `impersonation` | SPF-related keywords absent from auth headers |
| HTML-only body | `secrecy_isolation` | No plain-text alternative — common in phishing |

Each check produces zero or more `Finding` objects:

```python
@dataclass
class Finding:
    check_id: str          # Stable identifier, e.g. "display_name_mismatch"
    dimension: str         # ManipulationDimension value
    severity: float        # 0.0–1.0
    description: str       # Human-readable explanation
    evidence: list[str]    # Specific evidence strings
```

The deterministic risk score is the maximum severity across all findings, capped at `1.0`.

---

## LLM analysis

### Prompt design

`app/analysis/prompt.py` builds a compact prompt that includes:

- Message envelope (From, To, Reply-To, Subject)
- Authentication signals (DKIM-Signature, Received-SPF, Authentication-Results headers)
- Message body (truncated to fit context window)
- Extracted URLs (up to 20)
- Deterministic findings as structured evidence

The prompt requests **strict JSON output** with no free-form prose as the primary response format.

### Expected LLM response schema

```json
{
  "overall_risk": 0.85,
  "confidence": 0.9,
  "dimensions": {
    "authority_pressure": 0.7,
    "urgency_pressure": 0.9,
    "scarcity": 0.2,
    "fear_threat": 0.8,
    "reward_lure": 0.1,
    "curiosity_bait": 0.3,
    "reciprocity_obligation": 0.1,
    "social_proof": 0.4,
    "secrecy_isolation": 0.5,
    "impersonation": 0.9,
    "compliance_escalation": 0.3,
    "credential_or_payment_capture": 0.95
  },
  "rationale": "...",
  "evidence": ["...", "..."],
  "recommended_action": "quarantine"
}
```

All scores are clamped to `[0.0, 1.0]` after parsing.

### Retry and fallback behavior

1. If the model returns invalid JSON, the system retries **once** with a stricter prompt.
2. If the retry also fails, the run is marked `is_degraded=True`.
3. In degraded mode:
   - `overall_risk = deterministic_risk_score`
   - `confidence = 0.35` (conservative)
   - Verdict is computed with a `+0.10` risk adjustment when `confidence < 0.5`
   - UI displays a yellow degraded-mode banner

---

## The 12 manipulation dimensions

These are first-class product concepts with stable string identifiers. Defined in `app/policies/constants.py`.

| Dimension | Identifier | Description |
|-----------|-----------|-------------|
| Authority pressure | `authority_pressure` | Sender impersonates authority figures, officials, or executives |
| Urgency pressure | `urgency_pressure` | Artificial time limits — "act within 24 hours" |
| Scarcity | `scarcity` | False scarcity — "limited offer", "only 2 remaining" |
| Fear / threat | `fear_threat` | Threats of account suspension, legal action, or harm |
| Reward lure | `reward_lure` | Promises of prizes, refunds, or unexpected money |
| Curiosity bait | `curiosity_bait` | Clickbait hooks, withheld information, titillating subject lines |
| Reciprocity obligation | `reciprocity_obligation` | Manufactured sense of obligation to respond or act |
| Social proof | `social_proof` | False consensus — "everyone is doing this", "your colleagues have already" |
| Secrecy / isolation | `secrecy_isolation` | Requests to keep communication secret or bypass normal channels |
| Impersonation | `impersonation` | Impersonation of people, brands, or organizations |
| Compliance escalation | `compliance_escalation` | Foot-in-the-door patterns — small asks leading to larger ones |
| Credential / payment capture | `credential_or_payment_capture` | Requests for passwords, financial data, or PII |

---

## Verdict thresholds

See [Policy engine](policy-engine.md) for the full verdict threshold documentation.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANALYSIS_ENABLED` | `true` | Disable all analysis (ingestion still works) |
| `LLM_ENABLED` | `true` | Disable LLM calls (deterministic-only mode) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | Model name |
| `OLLAMA_TIMEOUT_SECONDS` | `120.0` | Response timeout |
| `ANALYSIS_PROMPT_VERSION` | `1.0` | Prompt version identifier — bump when schema changes |

---

## Inspecting analysis results

### Message Lab

Upload any `.eml` file at `/admin/messages/` and click **Analyse** to run the full pipeline and see the results immediately.

### Quarantine detail

Every quarantined message shows the full analysis breakdown:
- Verdict badge and risk score
- Confidence indicator
- Degraded mode banner (if applicable)
- Per-dimension score grid
- Deterministic findings
- LLM rationale and evidence list
