"""URL extraction from plain-text and HTML email bodies.

Design constraints:
  - No external network requests are made.
  - javascript:, data:, and vbscript: schemes are always rejected.
  - Duplicate URLs (same raw string) are deduplicated within each source.
  - Results preserve insertion order.
"""

import re
from urllib.parse import urlparse, urlunparse

from app.messages.html_safe import extract_anchors_from_html
from app.messages.schemas import ExtractedUrl

# Matches http/https URLs in plain text.
# Stops at whitespace and common punctuation that terminates URLs in prose.
_URL_PATTERN = re.compile(
    r"https?://"
    r"[^\s<>\"'()\[\]{}|\\^`]+"
    r"(?<![.,;:!?'\")\]])",
    re.IGNORECASE,
)

# Schemes that must never be treated as navigable URLs
_BLOCKED_SCHEMES = frozenset({"javascript", "data", "vbscript", "file"})


def _normalize_url(raw: str) -> str:
    """Return a normalized form of the URL (lowercased scheme+host, cleaned path)."""
    try:
        parsed = urlparse(raw)
        normalized = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
        )
        return urlunparse(normalized)
    except Exception:
        return raw


def _build_extracted_url(
    raw: str, source: str, link_text: str | None = None
) -> ExtractedUrl | None:
    """Parse a raw URL string into an ExtractedUrl. Returns None if invalid."""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return None
        if parsed.scheme.lower() in _BLOCKED_SCHEMES:
            return None
        return ExtractedUrl(
            raw_url=raw,
            normalized_url=_normalize_url(raw),
            scheme=parsed.scheme.lower(),
            host=parsed.netloc.lower(),
            path=parsed.path or "/",
            source=source,
            link_text=link_text or None,
        )
    except Exception:
        return None


def extract_urls_from_text(text: str) -> list[ExtractedUrl]:
    """Extract HTTP/HTTPS URLs from plain text using regex."""
    if not text:
        return []
    urls: list[ExtractedUrl] = []
    seen: set[str] = set()
    for match in _URL_PATTERN.finditer(text):
        raw = match.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        url = _build_extracted_url(raw, source="text")
        if url:
            urls.append(url)
    return urls


def extract_urls_from_html(html: str) -> list[ExtractedUrl]:
    """Extract URLs from HTML anchor href attributes.

    Also collects the anchor's visible text so that link-text-vs-destination
    mismatches can be detected in later analysis phases.
    """
    if not html:
        return []
    urls: list[ExtractedUrl] = []
    seen: set[str] = set()
    for href, link_text in extract_anchors_from_html(html):
        if not href or href in seen:
            continue
        seen.add(href)
        url = _build_extracted_url(href, source="html", link_text=link_text or None)
        if url:
            urls.append(url)
    return urls
