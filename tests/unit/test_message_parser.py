"""Unit tests for app.messages.parser."""

import hashlib
from pathlib import Path

from app.messages.parser import parse_message

FIXTURES = Path(__file__).parent.parent / "fixtures" / "emails"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParsePlainText:
    def test_parse_does_not_raise(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed is not None

    def test_from_address_extracted(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.from_address == "alice@example.com"

    def test_from_display_name_extracted(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.from_display_name == "Alice Smith"

    def test_subject_extracted(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.subject == "Plain text test message"

    def test_message_id_extracted(self):
        parsed = parse_message(load("plain_text.eml"))
        assert "plain-text-test@example.com" in (parsed.message_id or "")

    def test_to_addresses_extracted(self):
        parsed = parse_message(load("plain_text.eml"))
        assert "bob@example.com" in parsed.to_addresses

    def test_date_parsed_to_utc(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.date is not None
        # Aware datetime with UTC offset
        assert parsed.date.tzinfo is not None
        assert parsed.date.year == 2026

    def test_plain_text_body_extracted(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.has_text_plain is True
        assert "plain text test email" in (parsed.text_plain or "").lower()

    def test_html_absent(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.has_text_html is False
        assert parsed.text_html_safe is None

    def test_sha256_computed(self):
        raw = load("plain_text.eml")
        parsed = parse_message(raw)
        expected = hashlib.sha256(raw).hexdigest()
        assert parsed.raw_sha256 == expected

    def test_raw_size_bytes(self):
        raw = load("plain_text.eml")
        parsed = parse_message(raw)
        assert parsed.raw_size_bytes == len(raw)

    def test_urls_extracted_from_text(self):
        parsed = parse_message(load("plain_text.eml"))
        raw_urls = [u.raw_url for u in parsed.urls]
        assert any("example.com" in u for u in raw_urls)

    def test_no_attachments(self):
        parsed = parse_message(load("plain_text.eml"))
        assert parsed.attachments == []


class TestParseMultipart:
    def test_parse_does_not_raise(self):
        parsed = parse_message(load("multipart.eml"))
        assert parsed is not None

    def test_has_plain_and_html(self):
        parsed = parse_message(load("multipart.eml"))
        assert parsed.has_text_plain is True
        assert parsed.has_text_html is True

    def test_plain_text_body(self):
        parsed = parse_message(load("multipart.eml"))
        assert "Plain text version" in (parsed.text_plain or "")

    def test_html_text_extracted_and_script_stripped(self):
        parsed = parse_message(load("multipart.eml"))
        # HTML text should have been extracted to safe text
        assert parsed.text_html_safe is not None
        # Script content should be stripped
        assert "alert" not in (parsed.text_html_safe or "")
        # Visible text from HTML should be present
        assert "HTML version" in (parsed.text_html_safe or "")

    def test_cc_addresses_extracted(self):
        parsed = parse_message(load("multipart.eml"))
        assert "carol@example.com" in parsed.cc_addresses

    def test_reply_to_extracted(self):
        parsed = parse_message(load("multipart.eml"))
        assert parsed.reply_to_address == "support@example.com"

    def test_urls_extracted_from_html(self):
        parsed = parse_message(load("multipart.eml"))
        # Should include URLs from both text and HTML parts
        all_urls = [u.raw_url for u in parsed.urls]
        assert any("example.com" in u for u in all_urls)

    def test_url_deduplication(self):
        # https://example.com appears in both text and html parts
        parsed = parse_message(load("multipart.eml"))
        count = sum(1 for u in parsed.urls if "https://example.com" == u.raw_url)
        assert count == 1


class TestParseHtmlOnly:
    def test_parse_does_not_raise(self):
        parsed = parse_message(load("html_only.eml"))
        assert parsed is not None

    def test_html_text_extracted(self):
        parsed = parse_message(load("html_only.eml"))
        assert parsed.has_text_html is True
        assert parsed.text_html_safe is not None

    def test_script_stripped_from_html_text(self):
        parsed = parse_message(load("html_only.eml"))
        assert "document.cookie" not in (parsed.text_html_safe or "")
        assert "stolen" not in (parsed.text_html_safe or "")

    def test_no_plain_text_body(self):
        parsed = parse_message(load("html_only.eml"))
        assert parsed.has_text_plain is False

    def test_urls_extracted_from_html_anchors(self):
        parsed = parse_message(load("html_only.eml"))
        raw_urls = [u.raw_url for u in parsed.urls]
        assert any("phish.evil-domain.example" in u for u in raw_urls)


class TestParseWithAttachment:
    def test_parse_does_not_raise(self):
        parsed = parse_message(load("with_attachment.eml"))
        assert parsed is not None

    def test_attachment_found(self):
        parsed = parse_message(load("with_attachment.eml"))
        assert len(parsed.attachments) >= 1

    def test_pdf_attachment_filename(self):
        parsed = parse_message(load("with_attachment.eml"))
        filenames = [a.filename for a in parsed.attachments]
        assert "report.pdf" in filenames

    def test_pdf_content_type(self):
        parsed = parse_message(load("with_attachment.eml"))
        att = next(a for a in parsed.attachments if a.filename == "report.pdf")
        assert att.content_type == "application/pdf"

    def test_pdf_sha256_computed(self):
        parsed = parse_message(load("with_attachment.eml"))
        att = next(a for a in parsed.attachments if a.filename == "report.pdf")
        # sha256 of "fake pdf content"
        import base64
        raw_content = base64.b64decode("ZmFrZSBwZGYgY29udGVudA==")
        expected_sha256 = hashlib.sha256(raw_content).hexdigest()
        assert att.sha256 == expected_sha256

    def test_inline_attachment_detected(self):
        parsed = parse_message(load("with_attachment.eml"))
        inline_atts = [a for a in parsed.attachments if a.is_inline]
        assert len(inline_atts) >= 1

    def test_inline_content_id_captured(self):
        parsed = parse_message(load("with_attachment.eml"))
        inline_att = next((a for a in parsed.attachments if a.is_inline), None)
        assert inline_att is not None
        assert inline_att.content_id is not None


class TestParseMalformed:
    def test_parse_never_raises(self):
        # Malformed input should never cause an exception
        parsed = parse_message(load("malformed.eml"))
        assert parsed is not None

    def test_returns_parsedmessage(self):
        from app.messages.schemas import ParsedMessage
        parsed = parse_message(load("malformed.eml"))
        assert isinstance(parsed, ParsedMessage)

    def test_sha256_always_computed(self):
        raw = load("malformed.eml")
        parsed = parse_message(raw)
        expected = hashlib.sha256(raw).hexdigest()
        assert parsed.raw_sha256 == expected
        assert len(parsed.raw_sha256) == 64

    def test_raw_size_always_set(self):
        raw = load("malformed.eml")
        parsed = parse_message(raw)
        assert parsed.raw_size_bytes == len(raw)

    def test_empty_bytes_does_not_raise(self):
        parsed = parse_message(b"")
        assert parsed is not None
        assert parsed.raw_sha256 == hashlib.sha256(b"").hexdigest()

    def test_garbage_bytes_does_not_raise(self):
        parsed = parse_message(b"\x00\xff\xfe\xfd" * 100)
        assert parsed is not None
