"""RFC 5322 email parser and normalizer.

Accepts raw message bytes and returns a canonical ParsedMessage DTO.

Design constraints:
  - Never raises — malformed messages produce a best-effort ParsedMessage.
  - Passwords and other sensitive content are never logged.
  - The parser has no database dependency; it only produces in-memory DTOs.
  - Uses the Python stdlib email module exclusively (no third-party parsers).
"""

import email
import email.header
import email.message
import email.policy
import email.utils
import hashlib
from datetime import UTC, datetime

from app.messages.html_safe import extract_text_from_html
from app.messages.schemas import ExtractedAttachment, ParsedMessage
from app.messages.urls import extract_urls_from_html, extract_urls_from_text


def _hash_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of data."""
    return hashlib.sha256(data).hexdigest()


def _decode_header_value(raw: str | None) -> str | None:
    """Decode an RFC 2047-encoded header value to a plain Unicode string."""
    if not raw:
        return None
    try:
        parts = email.header.decode_header(raw)
        decoded: list[str] = []
        for part, charset in parts:
            if isinstance(part, bytes):
                try:
                    decoded.append(part.decode(charset or "utf-8", errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    decoded.append(part.decode("latin-1", errors="replace"))
            else:
                decoded.append(str(part))
        return "".join(decoded).strip() or None
    except Exception:
        return str(raw).strip() or None


def _parse_single_address(raw: str | None) -> tuple[str | None, str | None]:
    """Return (display_name, email_address) from a raw address header value."""
    if not raw:
        return None, None
    try:
        display_name, addr = email.utils.parseaddr(_decode_header_value(raw) or raw)
        return display_name.strip() or None, addr.strip() or None
    except Exception:
        return None, None


def _parse_address_list(raw: str | None) -> list[str]:
    """Return a list of email addresses from a raw address-list header value."""
    if not raw:
        return []
    try:
        return [
            addr.strip()
            for _, addr in email.utils.getaddresses([raw])
            if addr.strip()
        ]
    except Exception:
        return []


def _decode_part_payload(part: email.message.Message) -> str:
    """Decode a MIME part payload to a Unicode string."""
    payload_bytes = part.get_payload(decode=True)
    if not isinstance(payload_bytes, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload_bytes.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload_bytes.decode("latin-1", errors="replace")


def _is_attachment_part(part: email.message.Message) -> bool:
    """Return True if this MIME part should be treated as an attachment.

    Both explicit attachments (Content-Disposition: attachment) and inline
    non-text parts (e.g., embedded images) are collected as attachments for
    security analysis purposes.
    """
    disposition = (part.get_content_disposition() or "").strip().lower()
    if disposition == "attachment":
        return True
    content_type = part.get_content_type()
    # Non-text, non-multipart parts — treat as attachments regardless of whether
    # they are inline or have no Content-Disposition (e.g., embedded images).
    if not content_type.startswith(("text/", "multipart/")):
        return True
    return False


def parse_message(raw_bytes: bytes) -> ParsedMessage:
    """Parse raw RFC 5322 email bytes into a canonical ParsedMessage.

    This function never raises. Malformed inputs produce a best-effort
    ParsedMessage with None / empty-list for fields that cannot be extracted.
    """
    sha256 = _hash_bytes(raw_bytes)
    size = len(raw_bytes)

    try:
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)
    except Exception:
        return ParsedMessage(raw_size_bytes=size, raw_sha256=sha256)

    # ------------------------------------------------------------------ #
    # Headers
    # ------------------------------------------------------------------ #
    subject = _decode_header_value(msg.get("Subject"))

    from_display, from_addr = _parse_single_address(msg.get("From"))
    _, reply_to_addr = _parse_single_address(msg.get("Reply-To"))

    to_addresses = _parse_address_list(msg.get("To"))
    cc_addresses = _parse_address_list(msg.get("Cc"))
    bcc_addresses = _parse_address_list(msg.get("Bcc"))

    message_id = _decode_header_value(msg.get("Message-ID"))
    in_reply_to = _decode_header_value(msg.get("In-Reply-To"))
    references = _decode_header_value(msg.get("References"))

    # ------------------------------------------------------------------ #
    # Date — normalize to UTC
    # ------------------------------------------------------------------ #
    date: datetime | None = None
    date_raw = msg.get("Date")
    if date_raw:
        try:
            ts = email.utils.parsedate_to_datetime(date_raw)
            date = ts.astimezone(UTC)
        except Exception:
            date = None

    # ------------------------------------------------------------------ #
    # Authentication headers (raw values preserved for deterministic analysis)
    # ------------------------------------------------------------------ #
    auth_results = msg.get("Authentication-Results")
    received_spf = msg.get("Received-SPF")
    dkim_present = bool(msg.get("DKIM-Signature"))
    x_mailer = _decode_header_value(msg.get("X-Mailer"))

    # ------------------------------------------------------------------ #
    # Body extraction (depth-first walk of the MIME tree)
    # ------------------------------------------------------------------ #
    text_plain: str | None = None
    text_html: str | None = None
    has_text_plain = False
    has_text_html = False
    attachments: list[ExtractedAttachment] = []
    _attach_pos = 0

    def _walk(part: email.message.Message) -> None:
        nonlocal text_plain, text_html, has_text_plain, has_text_html, _attach_pos

        if part.is_multipart():
            for sub in part.get_payload():  # type: ignore[union-attr]
                _walk(sub)
            return

        ctype = part.get_content_type()

        if _is_attachment_part(part):
            payload_bytes = part.get_payload(decode=True) or b""
            sha = _hash_bytes(payload_bytes) if payload_bytes else None
            filename = _decode_header_value(part.get_filename())
            content_id = part.get("Content-ID")
            is_inline = (part.get_content_disposition() or "").strip().lower() == "inline"
            attachments.append(
                ExtractedAttachment(
                    filename=filename,
                    content_type=ctype,
                    size_bytes=len(payload_bytes),
                    sha256=sha,
                    is_inline=is_inline,
                    content_id=content_id,
                )
            )
            _attach_pos += 1
            return

        if ctype == "text/plain" and text_plain is None:
            text_plain = _decode_part_payload(part)
            has_text_plain = True
        elif ctype == "text/html" and text_html is None:
            text_html = _decode_part_payload(part)
            has_text_html = True

    _walk(msg)

    # ------------------------------------------------------------------ #
    # Safe HTML text extraction
    # ------------------------------------------------------------------ #
    text_html_safe: str | None = None
    if text_html:
        text_html_safe = extract_text_from_html(text_html)

    # ------------------------------------------------------------------ #
    # URL extraction (deduplicated across text and html)
    # ------------------------------------------------------------------ #
    from app.messages.schemas import ExtractedUrl

    urls: list[ExtractedUrl] = []
    seen_urls: set[str] = set()

    if text_plain:
        for url in extract_urls_from_text(text_plain):
            if url.raw_url not in seen_urls:
                seen_urls.add(url.raw_url)
                urls.append(url)

    if text_html:
        for url in extract_urls_from_html(text_html):
            if url.raw_url not in seen_urls:
                seen_urls.add(url.raw_url)
                urls.append(url)

    return ParsedMessage(
        raw_size_bytes=size,
        raw_sha256=sha256,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        subject=subject,
        from_address=from_addr,
        from_display_name=from_display,
        reply_to_address=reply_to_addr,
        to_addresses=to_addresses,
        cc_addresses=cc_addresses,
        bcc_addresses=bcc_addresses,
        date=date,
        has_text_plain=has_text_plain,
        has_text_html=has_text_html,
        text_plain=text_plain,
        text_html_safe=text_html_safe,
        header_authentication_results=auth_results,
        header_received_spf=received_spf,
        header_dkim_signature_present=dkim_present,
        header_x_mailer=x_mailer,
        urls=urls,
        attachments=attachments,
    )
