"""Upstream IMAP client abstraction for Mindwall.

Wraps imaplib (stdlib) with clean async support via asyncio.to_thread(),
matching the same pattern as connectivity.py.

Design principles:
  - All blocking network I/O runs inside asyncio.to_thread().
  - Credentials are accepted as arguments; never stored on the instance.
  - All exceptions are mapped to UpstreamImapError with safe, non-leaking messages.
  - No credentials, server banners, or raw IMAP responses are logged.
  - The client is a context manager: use ``async with UpstreamImapClient(...) as c:``.
  - Supports SSL/TLS, STARTTLS, and plain (not recommended) connections.

Usage::

    async with UpstreamImapClient(
        host=profile.imap_host,
        port=profile.imap_port,
        security=profile.imap_security,
        username=profile.imap_username,
        password=plaintext_password,
        timeout=30,
    ) as client:
        uid_validity, uids = await client.select_folder("INBOX")
        new_uids = [u for u in uids if u > last_seen_uid]
        raw_bytes = await client.fetch_raw_message(uid)

This module has no FastAPI, SQLAlchemy, or Mindwall-domain imports so it can
be used cleanly from both the sync service and the future IMAP proxy.
"""

from __future__ import annotations

import asyncio
import imaplib
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.mailboxes.models import ImapSecurity

log = structlog.get_logger(__name__)


class UpstreamImapError(Exception):
    """Raised when upstream IMAP communication fails.

    The message is always safe to surface to users — no secrets, stack traces,
    or raw server responses are included.
    """


# ---------------------------------------------------------------------------
# Safe error mapper
# ---------------------------------------------------------------------------


def _safe_error(exc: Exception) -> str:
    """Convert an IMAP exception to a safe, user-facing error string."""
    if isinstance(exc, imaplib.IMAP4.error):
        msg = str(exc).lower()
        auth_keywords = ("authentication", "authenticat", "login", "invalid credentials")
        if any(k in msg for k in auth_keywords):
            return "IMAP authentication failed - check username and password."
        return "IMAP server error - check host and port configuration."
    if isinstance(exc, TimeoutError | socket.timeout):
        return "IMAP connection timed out."
    if isinstance(exc, ConnectionRefusedError):
        return "IMAP connection refused — check host and port."
    if isinstance(exc, ssl.SSLError):
        return "IMAP TLS/SSL handshake failed — check security mode."
    if isinstance(exc, OSError):
        return "IMAP network error — host may be unreachable."
    return f"IMAP error: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Sync-side IMAP connection helper
# ---------------------------------------------------------------------------


def _build_imap_connection(
    host: str,
    port: int,
    security: ImapSecurity,
    timeout: int,
) -> imaplib.IMAP4:
    """Create an imaplib connection with the correct security mode.

    Runs synchronously — must be called inside asyncio.to_thread().
    """
    if security == ImapSecurity.SSL_TLS:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host=host, port=port, ssl_context=ctx, timeout=timeout)
    elif security == ImapSecurity.STARTTLS:
        conn = imaplib.IMAP4(host=host, port=port, timeout=timeout)
        ctx = ssl.create_default_context()
        conn.starttls(ssl_context=ctx)
    else:
        conn = imaplib.IMAP4(host=host, port=port, timeout=timeout)
    return conn


# ---------------------------------------------------------------------------
# Folder metadata
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FolderInfo:
    """Metadata returned by list_folders()."""

    name: str
    flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class UpstreamImapClient:
    """Async-friendly wrapper around imaplib for upstream mailbox access.

    Use as an async context manager::

        async with UpstreamImapClient(...) as client:
            ...

    All public methods are async and safe to call from an async context.
    """

    def __init__(
        self,
        host: str,
        port: int,
        security: ImapSecurity,
        username: str,
        password: str,
        timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._security = security
        self._username = username
        self._password = password
        self._timeout = timeout
        self._conn: imaplib.IMAP4 | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> UpstreamImapClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.logout()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a connection and authenticate."""

        def _do_connect() -> imaplib.IMAP4:
            conn = _build_imap_connection(
                host=self._host,
                port=self._port,
                security=self._security,
                timeout=self._timeout,
            )
            conn.login(self._username, self._password)
            return conn

        try:
            self._conn = await asyncio.to_thread(_do_connect)
        except Exception as exc:
            raise UpstreamImapError(_safe_error(exc)) from exc

        log.debug(
            "imap.connected",
            host=self._host,
            port=self._port,
            security=self._security.value,
        )

    async def logout(self) -> None:
        """Close the IMAP connection cleanly."""
        if self._conn is None:
            return

        def _do_logout() -> None:
            try:
                self._conn.logout()  # type: ignore[union-attr]
            except Exception:  # noqa: S110  # Best-effort close; ignore errors on teardown
                pass

        await asyncio.to_thread(_do_logout)
        self._conn = None

    # ------------------------------------------------------------------
    # Folder operations
    # ------------------------------------------------------------------

    async def list_folders(self) -> list[FolderInfo]:
        """Return the list of mailbox folders available on the upstream server."""
        conn = self._require_connection()

        def _do_list() -> list[FolderInfo]:
            status, data = conn.list()
            if status != "OK":
                raise UpstreamImapError("IMAP LIST command failed.")
            folders = []
            for item in data:
                if not isinstance(item, bytes):
                    continue
                # IMAP LIST response format: (<flags>) "<sep>" "<name>"
                decoded = item.decode("utf-8", errors="replace")
                # Extract folder name: last quoted token in the line.
                # rsplit('"', 2) gives [prefix, name, ""] when name is quoted.
                parts = decoded.rsplit('"', 2)
                if len(parts) >= 3:
                    name = parts[-2].strip()
                elif len(parts) == 2:
                    name = parts[-1].strip()
                else:
                    name = decoded.strip()
                flags_part = decoded.split(")")[0].lstrip("(")
                flags = [f.strip() for f in flags_part.split() if f.strip()]
                if name:
                    folders.append(FolderInfo(name=name, flags=flags))
            return folders

        try:
            return await asyncio.to_thread(_do_list)
        except UpstreamImapError:
            raise
        except Exception as exc:
            raise UpstreamImapError(_safe_error(exc)) from exc

    async def select_folder(self, folder_name: str) -> tuple[int, list[int]]:
        """SELECT a folder and return (uid_validity, sorted_uid_list).

        Uses IMAP UID SEARCH to get the full UID set rather than the
        message sequence numbers, because UIDs are stable across sessions.

        Returns:
            uid_validity: The UIDVALIDITY value for this folder.
            uids:         Sorted list of all UIDs in the folder.
        """
        conn = self._require_connection()

        def _do_select() -> tuple[int, list[int]]:
            # Select the folder and parse UIDVALIDITY from the response.
            typ, _data = conn.select(f'"{folder_name}"')
            if typ != "OK":
                raise UpstreamImapError(f"Could not select folder {folder_name!r}.")

            uid_validity = 0
            if conn.untagged_responses and b"UIDVALIDITY" in str(conn.untagged_responses).encode():
                # imaplib stores untagged responses; UIDVALIDITY is in the
                # SELECT response extras.  Fall back to 0 if not found.
                pass
            # Better: parse from untagged_responses dict
            uidvalidity_raw = conn.untagged_responses.get("UIDVALIDITY", [b"0"])
            if uidvalidity_raw:
                try:
                    uid_validity = int(uidvalidity_raw[0])
                except (ValueError, IndexError):
                    uid_validity = 0

            # UID SEARCH ALL returns all UIDs.
            status, search_data = conn.uid("SEARCH", "ALL")
            if status != "OK":
                return uid_validity, []

            uids_raw = search_data[0] if search_data else b""
            if not uids_raw:
                return uid_validity, []
            uids = sorted(int(u) for u in uids_raw.split() if u)
            return uid_validity, uids

        try:
            return await asyncio.to_thread(_do_select)
        except UpstreamImapError:
            raise
        except Exception as exc:
            raise UpstreamImapError(_safe_error(exc)) from exc

    async def fetch_raw_message(self, uid: int) -> bytes:
        """Fetch the complete RFC 5322 raw message bytes for a single UID.

        Args:
            uid: The IMAP UID of the message to fetch.

        Returns:
            Raw .eml bytes.

        Raises:
            UpstreamImapError: If the fetch fails or returns no data.
        """
        conn = self._require_connection()

        def _do_fetch() -> bytes:
            status, data = conn.uid("FETCH", str(uid), "(RFC822)")
            if status != "OK" or not data:
                raise UpstreamImapError(f"FETCH failed for UID {uid}.")
            # data is a list of (header_bytes, body_bytes) tuples mixed with
            # literal bytes.  The raw message is the bytes item after the
            # header tuple.
            for part in data:
                if isinstance(part, tuple) and len(part) == 2:
                    raw = part[1]
                    if isinstance(raw, bytes) and raw:
                        return raw
            raise UpstreamImapError(f"No message body returned for UID {uid}.")

        try:
            return await asyncio.to_thread(_do_fetch)
        except UpstreamImapError:
            raise
        except Exception as exc:
            raise UpstreamImapError(_safe_error(exc)) from exc

    async def fetch_uids_in_range(self, min_uid: int) -> list[int]:
        """Return all UIDs >= min_uid in the currently selected folder.

        This is the incremental sync query: pass last_seen_uid + 1 to get
        only new messages.

        Args:
            min_uid: Lowest UID to include (inclusive).

        Returns:
            Sorted list of matching UIDs.
        """
        conn = self._require_connection()

        def _do_search() -> list[int]:
            status, data = conn.uid("SEARCH", f"{min_uid}:*")
            if status != "OK" or not data:
                return []
            raw = data[0]
            if not raw:
                return []
            uids = sorted(int(u) for u in raw.split() if u)
            # IMAP may return UIDs < min_uid when the range endpoint is
            # the wildcard (*) and the mailbox has exactly one message.
            return [u for u in uids if u >= min_uid]

        try:
            return await asyncio.to_thread(_do_search)
        except UpstreamImapError:
            raise
        except Exception as exc:
            raise UpstreamImapError(_safe_error(exc)) from exc

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_connection(self) -> imaplib.IMAP4:
        if self._conn is None:
            raise UpstreamImapError("IMAP client is not connected. Call connect() first.")
        return self._conn
