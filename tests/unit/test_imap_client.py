"""Unit tests for the upstream IMAP client abstraction.

Tests use a mock imaplib.IMAP4 connection — no real IMAP server required.
"""

from __future__ import annotations

import imaplib
from unittest.mock import MagicMock, patch

import pytest
from app.mailboxes.models import ImapSecurity
from app.proxies.imap.client import UpstreamImapClient, UpstreamImapError, _safe_error

# ---------------------------------------------------------------------------
# _safe_error mapping
# ---------------------------------------------------------------------------


class TestSafeErrorMapping:
    def test_imap_auth_error(self):
        exc = imaplib.IMAP4.error("Authentication failed")
        msg = _safe_error(exc)
        assert "authentication" in msg.lower()

    def test_imap_generic_error(self):
        exc = imaplib.IMAP4.error("Some other IMAP error")
        msg = _safe_error(exc)
        assert "IMAP" in msg

    def test_timeout_error(self):
        msg = _safe_error(TimeoutError("timed out"))
        assert "timed out" in msg.lower()

    def test_connection_refused(self):
        msg = _safe_error(ConnectionRefusedError("refused"))
        assert "refused" in msg.lower()

    def test_os_error(self):
        msg = _safe_error(OSError("network error"))
        assert "network" in msg.lower() or "unreachable" in msg.lower()

    def test_ssl_error(self):
        import ssl

        msg = _safe_error(ssl.SSLError("cert verify failed"))
        assert "TLS" in msg or "SSL" in msg

    def test_unknown_error(self):
        msg = _safe_error(RuntimeError("something weird"))
        assert "IMAP" in msg or "RuntimeError" in msg


# ---------------------------------------------------------------------------
# UpstreamImapClient with mocked imaplib
# ---------------------------------------------------------------------------


def _make_mock_imap4(
    uid_validity: int = 12345,
    uids: list[int] | None = None,
    raw_message: bytes = b"From: test@example.com\r\n\r\nBody",
) -> MagicMock:
    """Build a mock imaplib.IMAP4 that returns controlled responses."""
    if uids is None:
        uids = [1, 2, 3]

    conn = MagicMock(spec=imaplib.IMAP4)

    # SELECT response
    conn.select.return_value = ("OK", [b"3"])
    conn.untagged_responses = {"UIDVALIDITY": [str(uid_validity).encode()]}

    # UID SEARCH ALL
    uid_bytes = b" ".join(str(u).encode() for u in uids)

    def _uid_command(command, *args):
        if command == "SEARCH":
            return ("OK", [uid_bytes])
        if command == "FETCH":
            return ("OK", [(b"header", raw_message), b")"])
        return ("OK", [None])

    conn.uid.side_effect = _uid_command
    conn.list.return_value = ("OK", [b'(\\HasNoChildren) "." "INBOX"'])
    conn.logout.return_value = ("BYE", [b"Logging out"])
    conn.login.return_value = ("OK", [b"Logged in"])
    return conn


class TestUpstreamImapClientSelectFolder:
    def test_select_returns_uid_validity_and_uids(self):
        mock_conn = _make_mock_imap4(uid_validity=9999, uids=[10, 20, 30])

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="user@example.com",
                password="secret",
                timeout=5,
            )
            client._conn = mock_conn
            uv, uids = await client.select_folder("INBOX")
            return uv, uids

        uid_validity, uids = asyncio.get_event_loop().run_until_complete(_run())
        assert uid_validity == 9999
        assert uids == [10, 20, 30]

    def test_select_returns_empty_when_no_messages(self):
        mock_conn = _make_mock_imap4(uids=[])

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            return await client.select_folder("INBOX")

        _, uids = asyncio.get_event_loop().run_until_complete(_run())
        assert uids == []

    def test_select_raises_on_imap_error(self):
        mock_conn = _make_mock_imap4()
        mock_conn.select.return_value = ("NO", [b"Mailbox does not exist"])

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            await client.select_folder("NONEXISTENT")

        with pytest.raises(UpstreamImapError):
            asyncio.get_event_loop().run_until_complete(_run())


class TestUpstreamImapClientFetchMessage:
    def test_fetch_returns_raw_bytes(self):
        raw = b"From: sender@example.com\r\n\r\nHello world"
        mock_conn = _make_mock_imap4(raw_message=raw)

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            return await client.fetch_raw_message(1)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result == raw

    def test_fetch_raises_when_no_body(self):
        mock_conn = _make_mock_imap4()

        def _bad_fetch(command, *args):
            if command == "FETCH":
                return ("OK", [None])
            return ("OK", [b""])

        mock_conn.uid.side_effect = _bad_fetch

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            await client.fetch_raw_message(999)

        with pytest.raises(UpstreamImapError):
            asyncio.get_event_loop().run_until_complete(_run())


class TestUpstreamImapClientFetchUidsInRange:
    def test_returns_uids_above_threshold(self):
        mock_conn = _make_mock_imap4()

        def _search(command, *args):
            if command == "SEARCH":
                return ("OK", [b"5 10 15 20"])
            return ("OK", [None])

        mock_conn.uid.side_effect = _search

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            return await client.fetch_uids_in_range(10)

        uids = asyncio.get_event_loop().run_until_complete(_run())
        assert uids == [10, 15, 20]

    def test_returns_empty_on_empty_response(self):
        mock_conn = _make_mock_imap4()

        def _empty(command, *args):
            return ("OK", [b""])

        mock_conn.uid.side_effect = _empty

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            return await client.fetch_uids_in_range(1)

        uids = asyncio.get_event_loop().run_until_complete(_run())
        assert uids == []


class TestUpstreamImapClientConnectFailure:
    def test_connect_auth_failure_raises_upstream_imap_error(self):
        import asyncio

        async def _run():
            with patch("app.proxies.imap.client._build_imap_connection") as mock_build:
                mock_conn = MagicMock()
                mock_conn.login.side_effect = imaplib.IMAP4.error("Authentication failed")
                mock_build.return_value = mock_conn

                client = UpstreamImapClient(
                    host="imap.example.com",
                    port=993,
                    security=ImapSecurity.SSL_TLS,
                    username="u",
                    password="wrong",
                )
                await client.connect()

        with pytest.raises(UpstreamImapError) as exc_info:
            asyncio.get_event_loop().run_until_complete(_run())
        assert "authentication" in str(exc_info.value).lower()

    def test_connect_timeout_raises_upstream_imap_error(self):
        import asyncio

        async def _run():
            with patch("app.proxies.imap.client._build_imap_connection") as mock_build:
                mock_build.side_effect = TimeoutError("timed out")

                client = UpstreamImapClient(
                    host="imap.example.com",
                    port=993,
                    security=ImapSecurity.SSL_TLS,
                    username="u",
                    password="p",
                )
                await client.connect()

        with pytest.raises(UpstreamImapError):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_requires_connection_raises_when_not_connected(self):
        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            # Never connected — _conn is None
            await client.fetch_raw_message(1)

        with pytest.raises(UpstreamImapError):
            asyncio.get_event_loop().run_until_complete(_run())


class TestListFolders:
    def test_list_returns_folder_names(self):
        mock_conn = _make_mock_imap4()
        mock_conn.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "." "INBOX"',
                b'(\\HasNoChildren) "." "Sent"',
                b'(\\HasNoChildren) "." "Drafts"',
            ],
        )

        import asyncio

        async def _run():
            client = UpstreamImapClient(
                host="imap.example.com",
                port=993,
                security=ImapSecurity.SSL_TLS,
                username="u",
                password="p",
            )
            client._conn = mock_conn
            return await client.list_folders()

        folders = asyncio.get_event_loop().run_until_complete(_run())
        names = [f.name for f in folders]
        assert "INBOX" in names
        assert "Sent" in names
        assert "Drafts" in names
