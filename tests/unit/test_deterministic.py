"""Unit tests for app.analysis.deterministic.

Each check function is exercised with minimal ParsedMessage fixtures.
No database or network access — fully deterministic.
"""

from __future__ import annotations

from app.analysis.deterministic import (
    DeterministicResult,
    Finding,
    run_deterministic_checks,
)
from app.messages.schemas import ExtractedAttachment, ExtractedUrl, ParsedMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_msg(**overrides) -> ParsedMessage:
    """Return a minimal safe ParsedMessage with all optional fields empty."""
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
        "text_plain": "Hello, this is a normal message.",
        "text_html_safe": None,
        # Include auth headers so the base message looks legitimate
        "header_authentication_results": "dmarc=pass; dkim=pass; spf=pass",
        "header_received_spf": "pass",
        "header_dkim_signature_present": True,
        "header_x_mailer": None,
        "urls": [],
        "attachments": [],
    }
    defaults.update(overrides)
    return ParsedMessage(**defaults)


def _url(
    raw_url: str, host: str, link_text: str | None = None, source: str = "html"
) -> ExtractedUrl:
    return ExtractedUrl(
        raw_url=raw_url,
        normalized_url=raw_url,
        scheme="https",
        host=host,
        path="/",
        source=source,
        link_text=link_text,
    )


def _att(filename: str, content_type: str = "application/octet-stream") -> ExtractedAttachment:
    return ExtractedAttachment(
        filename=filename,
        content_type=content_type,
        size_bytes=1024,
        sha256="deadbeef",
        is_inline=False,
        content_id=None,
    )


# ---------------------------------------------------------------------------
# Clean message returns no findings
# ---------------------------------------------------------------------------


def test_clean_message_no_findings():
    result = run_deterministic_checks(_base_msg())
    assert isinstance(result, DeterministicResult)
    assert result.findings == []
    assert result.risk_score == 0.0


# ---------------------------------------------------------------------------
# Display-name / reply-to mismatch
# ---------------------------------------------------------------------------


def test_display_name_reply_to_mismatch():
    msg = _base_msg(
        from_address="user@paypal.com",
        from_display_name="PayPal Support",
        reply_to_address="attacker@evil.com",
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "display_name_reply_to_mismatch" in rules
    assert result.risk_score > 0.0


def test_display_name_brand_not_in_domain():
    msg = _base_msg(
        from_address="user@totally-legit-site.com",
        from_display_name="PayPal Security",
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "brand_impersonation_display_name" in rules


def test_no_mismatch_when_same_domain():
    msg = _base_msg(
        from_address="noreply@paypal.com",
        from_display_name="PayPal",
        reply_to_address="help@paypal.com",
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "display_name_reply_to_mismatch" not in rules


# ---------------------------------------------------------------------------
# URL link-text mismatch
# ---------------------------------------------------------------------------


def test_url_link_text_mismatch():
    msg = _base_msg(
        urls=[_url("https://evil.com/login", "evil.com", link_text="www.paypal.com")]
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "link_text_href_mismatch" in rules


def test_url_no_mismatch_when_same_domain():
    msg = _base_msg(
        urls=[_url("https://paypal.com/login", "paypal.com", link_text="paypal.com")]
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "link_text_href_mismatch" not in rules


# ---------------------------------------------------------------------------
# Suspicious URL structure
# ---------------------------------------------------------------------------


def test_ip_host_flagged():
    msg = _base_msg(
        urls=[_url("http://192.168.1.1/click", "192.168.1.1")]
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "url_ip_address_host" in rules


def test_deep_subdomain_flagged():
    msg = _base_msg(
        urls=[_url("https://a.b.c.d.evil.com/", "a.b.c.d.evil.com")]
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "url_excessive_subdomains" in rules


def test_credential_keyword_in_path():
    msg = _base_msg(
        urls=[_url("https://example.com/login/verify/password", "example.com")]
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "url_credential_path" in rules


# ---------------------------------------------------------------------------
# Risky attachments
# ---------------------------------------------------------------------------


def test_high_risk_attachment_exe():
    msg = _base_msg(attachments=[_att("malware.exe")])
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "risky_attachment_high" in rules


def test_medium_risk_attachment_zip():
    msg = _base_msg(attachments=[_att("archive.zip")])
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "risky_attachment_medium" in rules


def test_safe_attachment_pdf():
    msg = _base_msg(attachments=[_att("document.pdf")])
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "high_risk_attachment" not in rules
    assert "medium_risk_attachment" not in rules


# ---------------------------------------------------------------------------
# Credential and payment language
# ---------------------------------------------------------------------------


def test_credential_language_detected():
    msg = _base_msg(
        text_plain="Please verify your account login credentials to secure your password."
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "credential_capture_language" in rules


def test_payment_language_detected():
    msg = _base_msg(
        text_plain="Your invoice is overdue. Complete the wire transfer immediately."
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "payment_language" in rules


def test_urgency_language_detected():
    msg = _base_msg(
        text_plain="URGENT: You must respond within 24 hours or your account will be suspended."
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "urgency_language" in rules


def test_fear_language_detected():
    msg = _base_msg(
        text_plain=(
            "Warning: your account has been compromised."
            " Failure to act will result in legal action."
        )
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "fear_threat_language" in rules


# ---------------------------------------------------------------------------
# Auth header checks
# ---------------------------------------------------------------------------


def test_missing_dkim_flagged():
    msg = _base_msg(header_dkim_signature_present=False)
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "missing_dkim_signature" in rules


def test_missing_auth_headers():
    msg = _base_msg(
        header_dkim_signature_present=False,
        header_received_spf=None,
        header_authentication_results=None,
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "missing_dkim_signature" in rules
    assert "missing_spf_results" in rules


# ---------------------------------------------------------------------------
# HTML-only body
# ---------------------------------------------------------------------------


def test_html_only_body_flagged():
    msg = _base_msg(
        has_text_plain=False,
        has_text_html=True,
        text_plain=None,
        text_html_safe="<p>Hello</p>",
    )
    result = run_deterministic_checks(msg)
    rules = {f.rule for f in result.findings}
    assert "html_only_no_plain_text" in rules


# ---------------------------------------------------------------------------
# Risk score aggregation
# ---------------------------------------------------------------------------


def test_multiple_signals_increase_risk():
    msg = _base_msg(
        from_address="noreply@evil.com",
        from_display_name="PayPal Security",
        reply_to_address="attacker@hacker.net",
        text_plain="URGENT: verify your account password and credit card now or face suspension.",
        attachments=[_att("payload.exe")],
    )
    result = run_deterministic_checks(msg)
    # Multiple high-severity signals: risk should be significantly elevated
    assert result.risk_score >= 0.5


def test_finding_to_dict():
    f = Finding(
        rule="test_rule",
        description="Test description",
        severity=0.75,
        dimensions=["urgency_pressure"],
        evidence="some evidence",
    )
    d = f.to_dict()
    assert d["rule"] == "test_rule"
    assert d["severity"] == 0.75
    assert "urgency_pressure" in d["dimensions"]


def test_deterministic_result_to_evidence_list():
    f = Finding(
        rule="r1",
        description="desc",
        severity=0.5,
        dimensions=["fear_threat"],
        evidence="ev",
    )
    result = DeterministicResult(findings=[f], risk_score=0.5, dimension_scores={})
    evidence = result.to_evidence_list()
    assert len(evidence) == 1
    assert "ev" in evidence[0]
