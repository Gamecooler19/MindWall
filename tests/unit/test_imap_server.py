"""Unit tests for the IMAP proxy server — protocol helpers.

Covers:
  - UID set parsing (single, range, wildcard, comma-separated)
  - Sequence set parsing
  - Login argument parsing (quoted and unquoted)
  - Search fallback behaviour
  - Folder normalisation
  - Envelope building
  - Fetch response field selection
"""

from __future__ import annotations

from app.proxies.imap.mailbox import ImapMailbox, ImapMessage
from app.proxies.imap.server import (
    _apply_search,
    _build_fetch_response,
    _parse_login_args,
    _parse_seq_set,
    _parse_uid_set,
    _unquote,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mailbox(uids: list[int]) -> ImapMailbox:
    """Build a minimal ImapMailbox with the given UIDs."""
    messages = [
        ImapMessage(
            uid=uid,
            seq=i + 1,
            size=100 + uid,
            flags=[r"\Seen"],
            raw_bytes=b"From: test@example.com\r\nSubject: Test\r\n\r\nBody",
            subject=f"Test message {uid}",
            from_address="sender@example.com",
            date_str="Mon, 01 Jan 2024 10:00:00 +0000",
        )
        for i, uid in enumerate(uids)
    ]
    return ImapMailbox(name="INBOX", uid_validity=12345, messages=messages)


# ---------------------------------------------------------------------------
# UID set parsing
# ---------------------------------------------------------------------------


class TestParseUidSet:
    def test_single_uid(self):
        mb = _make_mailbox([1, 2, 3])
        result = _parse_uid_set("2", mb)
        assert result == frozenset({2})

    def test_range(self):
        mb = _make_mailbox([1, 2, 3, 4, 5])
        result = _parse_uid_set("2:4", mb)
        assert result == frozenset({2, 3, 4})

    def test_wildcard_star(self):
        mb = _make_mailbox([1, 2, 5])
        result = _parse_uid_set("*", mb)
        assert result == frozenset({5})

    def test_range_to_star(self):
        mb = _make_mailbox([1, 2, 3])
        result = _parse_uid_set("2:*", mb)
        assert result == frozenset({2, 3})

    def test_comma_separated(self):
        mb = _make_mailbox([1, 2, 3, 4])
        result = _parse_uid_set("1,3", mb)
        assert result == frozenset({1, 3})

    def test_mixed(self):
        mb = _make_mailbox([10, 20, 30, 40])
        result = _parse_uid_set("10,20:30", mb)
        assert result == frozenset({10, 20, 30})

    def test_uid_not_in_mailbox_still_returned(self):
        """UIDs not in the mailbox are still included in the parsed set."""
        mb = _make_mailbox([1, 2, 3])
        result = _parse_uid_set("99", mb)
        assert result == frozenset({99})

    def test_empty_mailbox(self):
        mb = _make_mailbox([])
        result = _parse_uid_set("1:*", mb)
        assert result == frozenset()


# ---------------------------------------------------------------------------
# Sequence set parsing
# ---------------------------------------------------------------------------


class TestParseSeqSet:
    def test_single_seq(self):
        mb = _make_mailbox([10, 20, 30])
        result = _parse_seq_set("2", mb)
        assert result == frozenset({2})

    def test_range(self):
        mb = _make_mailbox([10, 20, 30])
        result = _parse_seq_set("1:2", mb)
        assert result == frozenset({1, 2})

    def test_star(self):
        mb = _make_mailbox([10, 20, 30])
        result = _parse_seq_set("*", mb)
        assert result == frozenset({3})

    def test_out_of_bounds_clipped(self):
        mb = _make_mailbox([10, 20])
        result = _parse_seq_set("5", mb)
        assert result == frozenset()

    def test_empty_mailbox(self):
        mb = _make_mailbox([])
        result = _parse_seq_set("1", mb)
        assert result == frozenset()


# ---------------------------------------------------------------------------
# Login argument parsing
# ---------------------------------------------------------------------------


class TestParseLoginArgs:
    def test_unquoted(self):
        username, password = _parse_login_args("alice secret123")
        assert username == "alice"
        assert password == "secret123"

    def test_quoted_args(self):
        username, password = _parse_login_args('"alice@example.com" "my password"')
        assert username == "alice@example.com"
        assert password == "my password"

    def test_quoted_with_spaces_in_password(self):
        username, password = _parse_login_args('"user" "pass word here"')
        assert username == "user"
        assert password == "pass word here"

    def test_missing_password_returns_none(self):
        username, password = _parse_login_args("justusername")
        assert username is None or password is None

    def test_empty_returns_none(self):
        username, password = _parse_login_args("")
        assert username is None or password is None


# ---------------------------------------------------------------------------
# Unquote
# ---------------------------------------------------------------------------


class TestUnquote:
    def test_strips_double_quotes(self):
        assert _unquote('"INBOX"') == "INBOX"

    def test_no_quotes_unchanged(self):
        assert _unquote("INBOX") == "INBOX"

    def test_strips_whitespace(self):
        assert _unquote('  "INBOX"  ') == "INBOX"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestApplySearch:
    def test_all_returns_all_uids(self):
        mb = _make_mailbox([1, 2, 3])
        result = _apply_search("ALL", mb)
        assert sorted(result) == [1, 2, 3]

    def test_empty_criteria_returns_all(self):
        mb = _make_mailbox([10, 20])
        result = _apply_search("", mb)
        assert sorted(result) == [10, 20]

    def test_uid_range(self):
        mb = _make_mailbox([1, 2, 3, 4, 5])
        result = _apply_search("UID 2:4", mb)
        assert sorted(result) == [2, 3, 4]

    def test_unsupported_criteria_returns_all(self):
        mb = _make_mailbox([1, 2, 3])
        result = _apply_search("UNSEEN", mb)
        # Fallback to ALL
        assert sorted(result) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Fetch response
# ---------------------------------------------------------------------------


class TestBuildFetchResponse:
    def test_uid_always_included(self):
        mb = _make_mailbox([42])
        msg = mb.messages[0]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _build_fetch_response(msg, "FLAGS")
        )
        assert "UID 42" in result

    def test_flags_included_when_requested(self):
        mb = _make_mailbox([1])
        msg = mb.messages[0]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _build_fetch_response(msg, "FLAGS")
        )
        assert "FLAGS" in result
        assert r"\Seen" in result

    def test_rfc822_size_included_for_body_fetch(self):
        mb = _make_mailbox([5])
        msg = mb.messages[0]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _build_fetch_response(msg, "BODY[]")
        )
        assert "RFC822.SIZE" in result

    def test_envelope_included_when_requested(self):
        mb = _make_mailbox([7])
        msg = mb.messages[0]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _build_fetch_response(msg, "ENVELOPE")
        )
        assert "ENVELOPE" in result

    def test_body_full_included(self):
        mb = _make_mailbox([3])
        msg = mb.messages[0]

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _build_fetch_response(msg, "BODY[]")
        )
        assert "BODY[]" in result
