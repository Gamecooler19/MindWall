"""Unit tests for app.messages.urls."""

from app.messages.urls import extract_urls_from_html, extract_urls_from_text


class TestExtractUrlsFromText:
    def test_extracts_https_url(self):
        urls = extract_urls_from_text("Visit https://example.com for more info.")
        assert len(urls) == 1
        assert urls[0].raw_url == "https://example.com"
        assert urls[0].scheme == "https"
        assert urls[0].host == "example.com"
        assert urls[0].source == "text"

    def test_extracts_http_url(self):
        urls = extract_urls_from_text("See http://old.example.org/path?q=1")
        assert len(urls) == 1
        assert urls[0].scheme == "http"
        assert urls[0].host == "old.example.org"

    def test_extracts_url_with_path_and_query(self):
        urls = extract_urls_from_text("Go to https://example.com/path/to/page?token=abc123&ref=test")
        assert len(urls) == 1
        assert "path/to/page" in urls[0].path
        assert urls[0].host == "example.com"

    def test_extracts_multiple_urls(self):
        text = "First: https://a.com and second: https://b.org/page"
        urls = extract_urls_from_text(text)
        assert len(urls) == 2
        hosts = {u.host for u in urls}
        assert "a.com" in hosts
        assert "b.org" in hosts

    def test_deduplicates_identical_urls(self):
        text = "https://example.com and again https://example.com"
        urls = extract_urls_from_text(text)
        assert len(urls) == 1

    def test_strips_trailing_punctuation(self):
        urls = extract_urls_from_text("See https://example.com. That's the URL.")
        assert len(urls) == 1
        assert not urls[0].raw_url.endswith(".")

    def test_empty_text_returns_empty(self):
        assert extract_urls_from_text("") == []

    def test_no_urls_returns_empty(self):
        assert extract_urls_from_text("No links in this text at all.") == []

    def test_link_text_is_none_for_text_urls(self):
        urls = extract_urls_from_text("https://example.com")
        assert urls[0].link_text is None

    def test_source_is_text(self):
        urls = extract_urls_from_text("https://example.com")
        assert urls[0].source == "text"


class TestExtractUrlsFromHtml:
    def test_extracts_anchor_href(self):
        html = '<a href="https://example.com">Click here</a>'
        urls = extract_urls_from_html(html)
        assert len(urls) == 1
        assert urls[0].raw_url == "https://example.com"
        assert urls[0].link_text == "Click here"
        assert urls[0].source == "html"

    def test_extracts_multiple_anchors(self):
        html = '<a href="https://a.com">A</a><a href="https://b.com">B</a>'
        urls = extract_urls_from_html(html)
        assert len(urls) == 2

    def test_skips_javascript_href(self):
        html = '<a href="javascript:void(0)">Action</a>'
        urls = extract_urls_from_html(html)
        assert len(urls) == 0

    def test_skips_data_href(self):
        html = '<a href="data:text/html,<b>hi</b>">Data</a>'
        urls = extract_urls_from_html(html)
        assert len(urls) == 0

    def test_deduplicates_same_href(self):
        html = '<a href="https://example.com">First</a><a href="https://example.com">Second</a>'
        urls = extract_urls_from_html(html)
        assert len(urls) == 1

    def test_link_text_captured(self):
        html = '<a href="https://phish.example.com">Click to verify your account</a>'
        urls = extract_urls_from_html(html)
        assert urls[0].link_text == "Click to verify your account"

    def test_empty_link_text_becomes_none(self):
        html = '<a href="https://example.com"><img src="logo.png"/></a>'
        urls = extract_urls_from_html(html)
        # The anchor has no visible text — link_text should be None
        assert urls[0].link_text is None

    def test_empty_html_returns_empty(self):
        assert extract_urls_from_html("") == []

    def test_normalized_url_lowercases_scheme_and_host(self):
        html = '<a href="HTTPS://EXAMPLE.COM/Path">Link</a>'
        urls = extract_urls_from_html(html)
        assert len(urls) == 1
        assert urls[0].normalized_url.startswith("https://example.com")
