"""Safe plain-text extraction from HTML using the stdlib html.parser.

Design constraints:
  - No active content is executed (scripts and styles are stripped).
  - No external resources are fetched.
  - Anchor href URLs and their link text are collected for later analysis.
  - Malformed HTML is handled gracefully; exceptions are suppressed so that
    ingestion never fails due to bad HTML.
"""

import re
from html.parser import HTMLParser

# Tags whose content should be completely discarded
_SKIP_TAGS: frozenset[str] = frozenset(
    {"script", "style", "head", "meta", "link", "noscript", "template"}
)

# Tags that introduce a logical line break in extracted text
_BLOCK_TAGS: frozenset[str] = frozenset(
    {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote"}
)


class _TextExtractor(HTMLParser):
    """HTMLParser that collects visible text and anchor href/text pairs.

    Uses convert_charrefs=True (the default since Python 3.5) so named
    and numeric character references are converted to Unicode before
    handle_data is called.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth: int = 0
        self._parts: list[str] = []
        self._anchors: list[tuple[str, str]] = []
        self._current_anchor_href: str | None = None
        self._current_anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()

        if tag_lower in _SKIP_TAGS:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        if tag_lower in _BLOCK_TAGS:
            self._parts.append("\n")

        if tag_lower == "br":
            self._parts.append("\n")

        if tag_lower == "a":
            _blocked = ("javascript:", "data:", "vbscript:")
            href = next(
                (v for k, v in attrs if k == "href" and v and not v.startswith(_blocked)),
                None,
            )
            self._current_anchor_href = href
            self._current_anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return

        if tag_lower == "a" and self._current_anchor_href is not None:
            link_text = "".join(self._current_anchor_parts).strip()
            self._anchors.append((self._current_anchor_href, link_text))
            self._current_anchor_href = None
            self._current_anchor_parts = []

        if tag_lower in _BLOCK_TAGS:
            if self._skip_depth == 0:
                self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._current_anchor_href is not None:
            self._current_anchor_parts.append(data)
        self._parts.append(data)

    def get_text(self) -> str:
        """Return the accumulated visible text, collapsed of excessive whitespace."""
        raw = "".join(self._parts)
        # Collapse 3+ consecutive newlines to 2
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        # Collapse horizontal whitespace (not newlines)
        raw = re.sub(r"[ \t]+", " ", raw)
        return raw.strip()

    def get_anchors(self) -> list[tuple[str, str]]:
        """Return (href, link_text) pairs collected from anchor tags."""
        return list(self._anchors)


def extract_text_from_html(html: str) -> str:
    """Extract safe plain text from an HTML string.

    Never executes scripts, fetches remote resources, or renders images.
    Silently degrades for malformed HTML — ingestion must not fail on bad input.
    """
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: S110 — malformed HTML must not crash ingestion
        pass
    return parser.get_text()


def extract_anchors_from_html(html: str) -> list[tuple[str, str]]:
    """Return (href, link_text) pairs from all anchor tags in the HTML.

    Skips javascript:, data:, and vbscript: hrefs.
    """
    if not html:
        return []
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: S110 — malformed HTML must not crash ingestion
        pass
    return parser.get_anchors()
