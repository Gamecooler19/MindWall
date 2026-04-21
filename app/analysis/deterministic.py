"""Deterministic security checks for Mindwall.

These checks run against a parsed message before (and independently of) the
LLM analysis.  They produce structured Finding objects that:
  - feed into the policy verdict calculation,
  - are injected into the LLM prompt as evidence, and
  - are displayed in the admin UI.

Design principles:
  - Every check is explicit and independently testable.
  - No network calls — all logic is pure-Python against already-parsed data.
  - Each check maps to one or more ManipulationDimension identifiers.
  - Findings include a severity (0.0-1.0) so they can contribute to the
    deterministic risk score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.messages.schemas import ParsedMessage
from app.policies.constants import ManipulationDimension

# ---------------------------------------------------------------------------
# Risky attachment extensions — high risk of malware delivery
# ---------------------------------------------------------------------------
_HIGH_RISK_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe", ".bat", ".cmd", ".com", ".scr", ".pif",
        ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
        ".ps1", ".psm1", ".psd1",
        ".jar", ".jnlp",
        ".lnk", ".iso", ".img",
        ".docm", ".xlsm", ".pptm",
        ".hta", ".msi", ".dll",
    }
)

_MEDIUM_RISK_EXTENSIONS: frozenset[str] = frozenset(
    {".zip", ".rar", ".7z", ".gz", ".tar", ".cab", ".ace"}
)

# ---------------------------------------------------------------------------
# Credential / payment language patterns
# ---------------------------------------------------------------------------
_CREDENTIAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(verify|confirm)\s+(your\s+)?(account|identity|email|password)\b",
        r"\b(reset|update|change)\s+(your\s+)?(password|credentials?|login)\b",
        r"\b(enter|provide|submit)\s+(your\s+)?(username|password|pin|otp|code)\b",
        r"\bclick\s+(here|the\s+link|below)\s+to\s+(login|log\s*in|verify|confirm|access)\b",
        r"\bsign\s*in\s+to\s+(your\s+)?(account|portal)\b",
        r"\byour\s+account\s+(has been|will be|is about to be)"
        r"\s+(suspended|locked|disabled|closed)\b",
        r"\bunauthori[sz]ed\s+access\b",
        r"\bsecurity\s+(alert|warning|breach|incident)\b",
    ]
]

_PAYMENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(invoice|payment|wire\s+transfer|bank\s+transfer)\b",
        r"\b(overdue|past\s+due|outstanding\s+balance|amount\s+due)\b",
        r"\b(update|change|verify)\s+(your\s+)?(billing|payment|credit\s+card|bank)\s+(info|details|account)\b",
        r"\breceive\s+(your\s+)?(refund|payment|compensation|reward)\b",
        r"\b(bitcoin|crypto|wire)\s+(transfer|payment)\b",
    ]
]

_URGENCY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(urgent|immediate|critical|emergency|action\s+required)\b",
        r"\b(within\s+\d+\s+(hour|minute|day)s?|by\s+(today|tomorrow|midnight|end\s+of\s+day))\b",
        r"\b(last\s+(chance|opportunity)|limited\s+time|expires?\s+(soon|today))\b",
        r"\bdo\s+not\s+(ignore|delay|wait)\b",
        r"\bfailure\s+to\s+(act|respond|comply)\b",
    ]
]

_FEAR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(threaten|threat|arrest|legal\s+action|lawsuit|prosecution)\b",
        r"\b(terminate|suspend|close|delete)\s+(your\s+)?(account|access|service)\b",
        r"\b(penalty|fine|consequence|forfeiture)\b",
        r"\byou\s+(will|may|could|must)\s+(be\s+)?(charged|arrested|prosecuted|fined)\b",
    ]
]


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """A single deterministic security finding."""

    rule: str
    description: str
    severity: float                             # 0.0-1.0
    dimensions: list[str] = field(default_factory=list)   # ManipulationDimension values
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "description": self.description,
            "severity": round(self.severity, 3),
            "dimensions": self.dimensions,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Individual checks — each returns 0 or more Finding objects
# ---------------------------------------------------------------------------


def _check_display_name_reply_to_mismatch(msg: ParsedMessage) -> list[Finding]:
    """Flag when the From display-name implies one identity but Reply-To points elsewhere."""
    findings: list[Finding] = []

    if not msg.from_address:
        return findings

    from_domain = msg.from_address.rsplit("@", 1)[-1].lower() if "@" in msg.from_address else ""

    # Check Reply-To domain mismatch (only when reply_to is present)
    if msg.reply_to_address:
        reply_domain = (
            msg.reply_to_address.rsplit("@", 1)[-1].lower()
            if "@" in msg.reply_to_address
            else ""
        )
        if from_domain and reply_domain and from_domain != reply_domain:
            findings.append(
                Finding(
                    rule="display_name_reply_to_mismatch",
                    description="From domain differs from Reply-To domain.",
                    severity=0.75,
                    dimensions=[
                        ManipulationDimension.IMPERSONATION,
                        ManipulationDimension.SECRECY_ISOLATION,
                    ],
                    evidence=(
                        f"From: {msg.from_address}  Reply-To: {msg.reply_to_address}"
                    ),
                )
            )

    # Flag when the display name contains a known brand but the sender domain
    # does not match (basic lookalike heuristic — runs regardless of reply-to).
    if msg.from_display_name and from_domain:
        dn_lower = msg.from_display_name.lower()
        # Simple brand word check - expand per policy later
        brand_words = {"paypal", "microsoft", "apple", "google", "amazon", "netflix",
                       "bank", "irs", "fedex", "ups", "dhl", "support", "security"}
        for brand in brand_words:
            if brand in dn_lower and brand not in from_domain:
                findings.append(
                    Finding(
                        rule="brand_impersonation_display_name",
                        description=(
                            f"Display name contains '{brand}' but sender domain "
                            f"is '{from_domain}'."
                        ),
                        severity=0.85,
                        dimensions=[ManipulationDimension.IMPERSONATION],
                        evidence=(
                            f"Display name: '{msg.from_display_name}'  "
                            f"From address: '{msg.from_address}'"
                        ),
                    )
                )
                break  # one finding per message is enough for this check

    return findings


def _check_url_link_text_mismatch(msg: ParsedMessage) -> list[Finding]:
    """Flag when visible anchor text implies a different destination than the href."""
    findings: list[Finding] = []

    for url in msg.urls:
        if url.source != "html" or not url.link_text:
            continue
        link_lower = url.link_text.lower()
        host_lower = (url.host or "").lower()

        # Extract any domain-like word from the link text
        # e.g. "Go to paypal.com" but href → evil.example.com
        domain_in_text = re.search(r"[\w-]+\.[a-z]{2,6}", link_lower)
        if domain_in_text:
            text_domain = domain_in_text.group(0).lower()
            if text_domain and text_domain not in host_lower and host_lower not in text_domain:
                findings.append(
                    Finding(
                        rule="link_text_href_mismatch",
                        description="Anchor text suggests a different domain than the actual href.",
                        severity=0.80,
                        dimensions=[
                            ManipulationDimension.IMPERSONATION,
                            ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE,
                        ],
                        evidence=(
                            f"Link text: '{url.link_text[:80]}'  "
                            f"Href host: '{url.host}'"
                        ),
                    )
                )
                break  # avoid flooding with many URL mismatches

    return findings


def _check_suspicious_url_structure(msg: ParsedMessage) -> list[Finding]:
    """Flag unusual URL structures: IP addresses, excessive subdomains, lookalike paths."""
    findings: list[Finding] = []
    seen_issues: set[str] = set()

    for url in msg.urls:
        host = url.host or ""
        parsed = urlparse(url.raw_url)

        # IP address as hostname
        ip_pattern = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
        if ip_pattern.match(host) and "ip_host" not in seen_issues:
            findings.append(
                Finding(
                    rule="url_ip_address_host",
                    description="URL uses an IP address as host (unusual for legitimate services).",
                    severity=0.65,
                    dimensions=[ManipulationDimension.IMPERSONATION],
                    evidence=f"URL: {url.raw_url[:120]}",
                )
            )
            seen_issues.add("ip_host")

        # Excessive subdomain depth (≥4 labels)
        subdomain_count = len(host.split("."))
        if subdomain_count >= 4 and "deep_subdomain" not in seen_issues:
            findings.append(
                Finding(
                    rule="url_excessive_subdomains",
                    description="URL has unusually deep subdomain nesting.",
                    severity=0.45,
                    dimensions=[ManipulationDimension.IMPERSONATION],
                    evidence=f"Host: {host}",
                )
            )
            seen_issues.add("deep_subdomain")

        # Credential-keyword in path
        path = (parsed.path or "").lower()
        if any(kw in path for kw in ("login", "signin", "verify", "account", "secure", "auth")):
            if "cred_path" not in seen_issues:
                findings.append(
                    Finding(
                        rule="url_credential_path",
                        description="URL path contains credential or authentication keywords.",
                        severity=0.55,
                        dimensions=[ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE],
                        evidence=f"URL: {url.raw_url[:120]}",
                    )
                )
                seen_issues.add("cred_path")

    return findings


def _check_risky_attachments(msg: ParsedMessage) -> list[Finding]:
    """Flag attachments with high-risk or medium-risk file extensions."""
    findings: list[Finding] = []

    for att in msg.attachments:
        filename = (att.filename or "").lower()
        ext = ""
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[-1]

        if ext in _HIGH_RISK_EXTENSIONS:
            findings.append(
                Finding(
                    rule="risky_attachment_high",
                    description=f"Attachment has a high-risk file extension: {ext}",
                    severity=0.90,
                    dimensions=[ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE],
                    evidence=f"Filename: {att.filename}  Type: {att.content_type}",
                )
            )
        elif ext in _MEDIUM_RISK_EXTENSIONS:
            findings.append(
                Finding(
                    rule="risky_attachment_medium",
                    description=f"Attachment is a compressed archive: {ext}",
                    severity=0.50,
                    dimensions=[ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE],
                    evidence=f"Filename: {att.filename}  Type: {att.content_type}",
                )
            )

    return findings


def _check_credential_language(msg: ParsedMessage) -> list[Finding]:
    """Scan body text for credential capture language patterns."""
    body = " ".join(
        part for part in [msg.text_plain, msg.text_html_safe] if part
    )
    if not body:
        return []

    findings: list[Finding] = []
    matched_patterns: list[str] = []

    for pattern in _CREDENTIAL_PATTERNS:
        m = pattern.search(body)
        if m:
            matched_patterns.append(m.group(0)[:60])

    if matched_patterns:
        findings.append(
            Finding(
                rule="credential_capture_language",
                description="Message body contains credential capture language patterns.",
                severity=min(0.4 + len(matched_patterns) * 0.1, 0.95),
                dimensions=[
                    ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE,
                    ManipulationDimension.IMPERSONATION,
                ],
                evidence="; ".join(matched_patterns[:3]),
            )
        )

    return findings


def _check_payment_language(msg: ParsedMessage) -> list[Finding]:
    """Scan body text for payment redirection or invoice manipulation patterns."""
    body = " ".join(
        part for part in [msg.text_plain, msg.text_html_safe] if part
    )
    if not body:
        return []

    findings: list[Finding] = []
    matched: list[str] = []

    for pattern in _PAYMENT_PATTERNS:
        m = pattern.search(body)
        if m:
            matched.append(m.group(0)[:60])

    if matched:
        findings.append(
            Finding(
                rule="payment_language",
                description="Message body contains payment or invoice manipulation language.",
                severity=min(0.35 + len(matched) * 0.10, 0.85),
                dimensions=[ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE],
                evidence="; ".join(matched[:3]),
            )
        )

    return findings


def _check_urgency_language(msg: ParsedMessage) -> list[Finding]:
    """Scan body text for urgency and pressure language patterns."""
    body = " ".join(
        part for part in [msg.text_plain, msg.text_html_safe] if part
    )
    if not body:
        return []

    subject = msg.subject or ""
    combined = subject + " " + body

    findings: list[Finding] = []
    matched: list[str] = []

    for pattern in _URGENCY_PATTERNS:
        m = pattern.search(combined)
        if m:
            matched.append(m.group(0)[:60])

    if matched:
        findings.append(
            Finding(
                rule="urgency_language",
                description="Message uses urgency or time-pressure language.",
                severity=min(0.30 + len(matched) * 0.10, 0.80),
                dimensions=[
                    ManipulationDimension.URGENCY_PRESSURE,
                    ManipulationDimension.AUTHORITY_PRESSURE,
                ],
                evidence="; ".join(matched[:3]),
            )
        )

    return findings


def _check_fear_language(msg: ParsedMessage) -> list[Finding]:
    """Scan body text for fear, threat, and coercive language patterns."""
    body = " ".join(
        part for part in [msg.text_plain, msg.text_html_safe] if part
    )
    if not body:
        return []

    subject = msg.subject or ""
    combined = subject + " " + body

    findings: list[Finding] = []
    matched: list[str] = []

    for pattern in _FEAR_PATTERNS:
        m = pattern.search(combined)
        if m:
            matched.append(m.group(0)[:60])

    if matched:
        findings.append(
            Finding(
                rule="fear_threat_language",
                description="Message uses fear or threat language.",
                severity=min(0.40 + len(matched) * 0.10, 0.90),
                dimensions=[
                    ManipulationDimension.FEAR_THREAT,
                    ManipulationDimension.COMPLIANCE_ESCALATION,
                ],
                evidence="; ".join(matched[:3]),
            )
        )

    return findings


def _check_no_plain_text_body(msg: ParsedMessage) -> list[Finding]:
    """Flag HTML-only messages with no plain-text alternative (common phishing pattern)."""
    if msg.has_text_html and not msg.has_text_plain:
        return [
            Finding(
                rule="html_only_no_plain_text",
                description="Message is HTML-only with no plain-text alternative.",
                severity=0.25,
                dimensions=[ManipulationDimension.SECRECY_ISOLATION],
                evidence="Content-Type: text/html only",
            )
        ]
    return []


def _check_missing_auth_headers(msg: ParsedMessage) -> list[Finding]:
    """Note when standard authentication headers are absent."""
    findings: list[Finding] = []

    if not msg.header_dkim_signature_present:
        findings.append(
            Finding(
                rule="missing_dkim_signature",
                description="Message has no DKIM-Signature header.",
                severity=0.20,
                dimensions=[ManipulationDimension.IMPERSONATION],
                evidence="DKIM-Signature: absent",
            )
        )

    if not msg.header_received_spf and not msg.header_authentication_results:
        findings.append(
            Finding(
                rule="missing_spf_results",
                description="No SPF or Authentication-Results header present.",
                severity=0.15,
                dimensions=[ManipulationDimension.IMPERSONATION],
                evidence="Received-SPF: absent; Authentication-Results: absent",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class DeterministicResult:
    """Aggregated output of all deterministic checks."""

    findings: list[Finding]
    risk_score: float
    dimension_scores: dict[str, float]

    def to_evidence_list(self) -> list[str]:
        """Return a flat list of evidence strings for injection into the LLM prompt."""
        return [f"[{f.rule}] {f.description} — {f.evidence}" for f in self.findings if f.evidence]


def run_deterministic_checks(msg: ParsedMessage) -> DeterministicResult:
    """Run all deterministic security checks and return aggregated results.

    The risk_score is computed as a weighted average of finding severities,
    capped at 1.0.  Per-dimension scores are derived by averaging the
    severities of findings that target each dimension.
    """
    findings: list[Finding] = []
    findings.extend(_check_display_name_reply_to_mismatch(msg))
    findings.extend(_check_url_link_text_mismatch(msg))
    findings.extend(_check_suspicious_url_structure(msg))
    findings.extend(_check_risky_attachments(msg))
    findings.extend(_check_credential_language(msg))
    findings.extend(_check_payment_language(msg))
    findings.extend(_check_urgency_language(msg))
    findings.extend(_check_fear_language(msg))
    findings.extend(_check_no_plain_text_body(msg))
    findings.extend(_check_missing_auth_headers(msg))

    # Aggregate dimension scores from findings
    dim_buckets: dict[str, list[float]] = {}
    for f in findings:
        for dim in f.dimensions:
            dim_buckets.setdefault(dim, []).append(f.severity)

    dimension_scores: dict[str, float] = {
        dim: min(max(scores), 1.0) for dim, scores in dim_buckets.items()
    }

    # Overall deterministic risk score: max severity, pulled toward mean
    if findings:
        severities = [f.severity for f in findings]
        max_sev = max(severities)
        mean_sev = sum(severities) / len(severities)
        risk_score = round(min((max_sev * 0.6 + mean_sev * 0.4), 1.0), 4)
    else:
        risk_score = 0.0

    return DeterministicResult(
        findings=findings,
        risk_score=risk_score,
        dimension_scores=dimension_scores,
    )
