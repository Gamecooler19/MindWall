"""Unit tests for app.messages.html_safe."""

from app.messages.html_safe import extract_anchors_from_html, extract_text_from_html


class TestExtractTextFromHtml:
    def test_strips_tags(self):
        result = extract_text_from_html("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result
        assert "<p>" not in result
        assert "<b>" not in result

    def test_strips_scripts(self):
        html = "<p>Keep this</p><script>alert('xss')</script><p>And this</p>"
        result = extract_text_from_html(html)
        assert "Keep this" in result
        assert "And this" in result
        assert "alert" not in result
        assert "xss" not in result

    def test_strips_styles(self):
        html = "<style>body { color: red; }</style><p>Visible text</p>"
        result = extract_text_from_html(html)
        assert "Visible text" in result
        assert "color" not in result
        assert "body" not in result

    def test_strips_noscript(self):
        html = "<noscript>Fallback content</noscript><p>Main content</p>"
        result = extract_text_from_html(html)
        assert "Main content" in result
        assert "Fallback content" not in result

    def test_handles_entities(self):
        result = extract_text_from_html("<p>Hello &amp; World &lt;test&gt;</p>")
        assert "Hello" in result
        assert "World" in result
        # Entities should be converted to their character equivalents
        assert "&amp;" not in result

    def test_preserves_newlines_at_block_elements(self):
        html = "<p>First paragraph</p><p>Second paragraph</p>"
        result = extract_text_from_html(html)
        assert "First paragraph" in result
        assert "Second paragraph" in result
        assert "\n" in result

    def test_empty_string_returns_empty(self):
        assert extract_text_from_html("") == ""

    def test_malformed_html_does_not_raise(self):
        # Should degrade gracefully, not raise
        result = extract_text_from_html("<p>Unclosed tag <b>bold")
        assert isinstance(result, str)

    def test_plain_text_passthrough(self):
        # Text without any HTML tags should pass through
        result = extract_text_from_html("No HTML here")
        assert "No HTML here" in result

    def test_nested_skipped_tags(self):
        html = "<script><script>nested</script></script><p>visible</p>"
        result = extract_text_from_html(html)
        assert "visible" in result
        assert "nested" not in result

    def test_href_with_dangerous_scheme_excluded_from_text(self):
        html = '<a href="javascript:alert(1)">Click</a>'
        result = extract_text_from_html(html)
        # The link text "Click" is visible text, but javascript: should not appear
        assert "javascript" not in result


class TestExtractAnchorsFromHtml:
    def test_extracts_href_and_link_text(self):
        html = '<a href="https://example.com">Example</a>'
        anchors = extract_anchors_from_html(html)
        assert len(anchors) == 1
        href, text = anchors[0]
        assert href == "https://example.com"
        assert text == "Example"

    def test_multiple_anchors(self):
        html = '<a href="https://a.com">A</a><a href="https://b.com">B</a>'
        anchors = extract_anchors_from_html(html)
        assert len(anchors) == 2
        hrefs = [h for h, _ in anchors]
        assert "https://a.com" in hrefs
        assert "https://b.com" in hrefs

    def test_skips_javascript_href(self):
        html = '<a href="javascript:void(0)">Click</a>'
        anchors = extract_anchors_from_html(html)
        assert len(anchors) == 0

    def test_skips_data_href(self):
        html = '<a href="data:text/html,<b>hi</b>">Data link</a>'
        anchors = extract_anchors_from_html(html)
        assert len(anchors) == 0

    def test_empty_string_returns_empty_list(self):
        assert extract_anchors_from_html("") == []

    def test_anchor_without_href_not_included(self):
        html = '<a name="anchor">Jump target</a>'
        anchors = extract_anchors_from_html(html)
        # href is absent — should not be included
        assert len(anchors) == 0

    def test_anchor_in_skipped_tag(self):
        # Anchors inside <script> blocks should not be collected
        html = '<script>var x = "<a href=\"https://evil.com\">bad</a>";</script>'
        anchors = extract_anchors_from_html(html)
        assert len(anchors) == 0
