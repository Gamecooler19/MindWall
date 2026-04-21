"""Read-only IMAP proxy server for Mindwall.

Implements a minimal, RFC 3501-compatible IMAP server subset sufficient for:
  - Mail clients to authenticate with Mindwall proxy credentials.
  - Browsing INBOX and Mindwall/Quarantine virtual folders.
  - Fetching message data from the local Mindwall message store.

Supported commands:
  CAPABILITY, NOOP, LOGOUT (any state)
  LOGIN (not-authenticated state)
  LIST, SELECT, STATUS, UID SEARCH, UID FETCH, CLOSE (authenticated/selected)
  EXAMINE (treated identically to SELECT — always read-only)

Unsupported/mutation commands return NO [CANNOT] with a clear message:
  STORE, COPY, APPEND, EXPUNGE, CREATE, DELETE, RENAME, MOVE, SUBSCRIBE,
  UNSUBSCRIBE, LSUB, SETFLAGS, etc.

Architecture:
  - Each TCP connection runs in its own asyncio.Task via ImapConnection.
  - The ImapServer manages the listener and spawns connections.
  - All database I/O uses a fresh AsyncSession per connection (not per command)
    to keep the session lifecycle bounded to the connection lifetime.
  - The mailbox.py data adapter is the only contact with the DB/store layer.

Design constraints:
  - No write operations — this is an MVP read-only proxy.
  - No TLS in this phase; add STARTTLS wrapper in Phase 10.
  - No pipelining — commands are processed one at a time.
  - Lines are limited to 8 KB to prevent resource exhaustion.
  - Maximum idle time enforced to prevent connection leaks.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from app.proxies.imap.mailbox import (
    ImapMailbox,
    ImapMessage,
    list_folders,
    select_mailbox,
)
from app.proxies.imap.session import ProxySession, authenticate_proxy_credentials

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    AsyncSessionFactory = async_sessionmaker[AsyncSession]

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMAP_VERSION = "IMAP4rev1"

# Commands supported in the CAPABILITY response.
_CAPABILITY_STR = (
    f"CAPABILITY {IMAP_VERSION} LITERAL+ AUTH=PLAIN IMAP4rev1"
)

# Maximum line length accepted from a client (bytes).
_MAX_LINE_BYTES = 8192

# Maximum seconds a connection may be idle (no commands received).
_IDLE_TIMEOUT_SECONDS = 1800  # 30 minutes

# Read-only mutation rejection message.
_READONLY_MSG = "NO [CANNOT] This is a read-only IMAP proxy. Operation not supported in MVP."

# ---------------------------------------------------------------------------
# Protocol state
# ---------------------------------------------------------------------------


class ConnState(Enum):
    NOT_AUTH = auto()
    AUTH = auto()
    SELECTED = auto()
    LOGOUT = auto()


# ---------------------------------------------------------------------------
# IMAP connection handler
# ---------------------------------------------------------------------------


class ImapConnection:
    """Handles one IMAP client connection.

    Manages the IMAP state machine, parses commands, and dispatches to handler
    methods.  All I/O is async (asyncio streams).
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session_factory: AsyncSessionFactory,
        raw_store_root: Path | None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._session_factory = session_factory
        self._raw_store_root = raw_store_root

        self._state = ConnState.NOT_AUTH
        self._proxy_session: ProxySession | None = None
        self._selected: ImapMailbox | None = None

        peer = writer.get_extra_info("peername", ("?", 0))
        self._peer = f"{peer[0]}:{peer[1]}"

        log.info("imap_proxy.connection_opened", peer=self._peer)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def handle(self) -> None:
        """Main connection loop — read commands until LOGOUT or disconnect."""
        await self._send(f"* OK [CAPABILITY {IMAP_VERSION}] Mindwall IMAP proxy ready")

        try:
            async with asyncio.timeout(_IDLE_TIMEOUT_SECONDS):
                while self._state != ConnState.LOGOUT:
                    try:
                        line = await asyncio.wait_for(
                            self._reader.readline(), timeout=_IDLE_TIMEOUT_SECONDS
                        )
                    except TimeoutError:
                        await self._send("* BYE Idle timeout")
                        break

                    if not line:
                        break  # Client disconnected

                    if len(line) > _MAX_LINE_BYTES:
                        await self._send("* BAD Line too long")
                        break

                    decoded = line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                    await self._dispatch(decoded)

        except Exception:
            log.exception("imap_proxy.connection_error", peer=self._peer)
        finally:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                log.debug("imap_proxy.close_error", peer=self._peer)
            log.info("imap_proxy.connection_closed", peer=self._peer)

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------

    async def _send(self, line: str) -> None:
        """Send a line to the client, terminated with CRLF."""
        self._writer.write((line + "\r\n").encode("utf-8", errors="replace"))
        await self._writer.drain()

    async def _send_literal(self, tag: str, prefix: str, data: bytes) -> None:
        """Send a literal string response: {N} followed by the raw bytes."""
        header = f"{prefix} {{{len(data)}}}"
        self._writer.write((header + "\r\n").encode())
        self._writer.write(data)
        self._writer.write(b"\r\n")
        await self._writer.drain()

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, line: str) -> None:
        """Parse one IMAP command line and dispatch to the right handler."""
        if not line.strip():
            return

        # IMAP command format: tag SP command [SP args...]
        parts = line.split(None, 2)
        if len(parts) < 2:
            await self._send("* BAD Could not parse command")
            return

        tag = parts[0]
        command = parts[1].upper()
        args = parts[2] if len(parts) > 2 else ""

        log.debug("imap_proxy.command", peer=self._peer, tag=tag, command=command)

        # Commands available in any state.
        if command == "CAPABILITY":
            await self._cmd_capability(tag)
        elif command == "NOOP":
            await self._send(f"{tag} OK NOOP completed")
        elif command == "LOGOUT":
            await self._cmd_logout(tag)
        # Not-authenticated state.
        elif command == "LOGIN":
            await self._cmd_login(tag, args)
        elif command == "AUTHENTICATE":
            await self._send(f"{tag} NO AUTHENTICATE not supported. Use LOGIN.")
        # Authenticated / selected state.
        elif command == "LIST":
            await self._require_auth(tag) and await self._cmd_list(tag, args)
        elif command in ("SELECT", "EXAMINE"):
            await self._require_auth(tag) and await self._cmd_select(tag, args)
        elif command == "STATUS":
            await self._require_auth(tag) and await self._cmd_status(tag, args)
        elif command == "CLOSE":
            await self._require_selected(tag) and await self._cmd_close(tag)
        elif command == "UID":
            await self._require_auth(tag) and await self._cmd_uid(tag, args)
        elif command == "SEARCH":
            await self._require_selected(tag) and await self._cmd_search(tag, args)
        elif command == "FETCH":
            await self._require_selected(tag) and await self._cmd_fetch(tag, args)
        # Mutation commands — always rejected.
        elif command in _MUTATION_COMMANDS:
            await self._send(f"{tag} {_READONLY_MSG}")
        else:
            await self._send(f"{tag} BAD Unknown command: {command}")

    # ------------------------------------------------------------------
    # CAPABILITY
    # ------------------------------------------------------------------

    async def _cmd_capability(self, tag: str) -> None:
        await self._send(f"* {_CAPABILITY_STR}")
        await self._send(f"{tag} OK CAPABILITY completed")

    # ------------------------------------------------------------------
    # LOGOUT
    # ------------------------------------------------------------------

    async def _cmd_logout(self, tag: str) -> None:
        self._state = ConnState.LOGOUT
        await self._send("* BYE Mindwall IMAP proxy signing out")
        await self._send(f"{tag} OK LOGOUT completed")

    # ------------------------------------------------------------------
    # LOGIN
    # ------------------------------------------------------------------

    async def _cmd_login(self, tag: str, args: str) -> None:
        if self._state != ConnState.NOT_AUTH:
            await self._send(f"{tag} BAD Already authenticated")
            return

        # Parse: LOGIN username password  (quoted or unquoted)
        username, password = _parse_login_args(args)
        if username is None or password is None:
            await self._send(f"{tag} BAD LOGIN requires username and password")
            return

        async with self._session_factory() as db:
            session = await authenticate_proxy_credentials(db, username, password)

        if session is None:
            await self._send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials")
            return

        self._proxy_session = session
        self._state = ConnState.AUTH
        await self._send(
            f"{tag} OK [CAPABILITY {IMAP_VERSION}] "
            f"LOGIN completed, authenticated as {username}"
        )

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------

    async def _cmd_list(self, tag: str, args: str) -> None:
        folders = list_folders()
        for flags, delimiter, name in folders:
            await self._send(f'* LIST {flags} "{delimiter}" "{name}"')
        await self._send(f"{tag} OK LIST completed")

    # ------------------------------------------------------------------
    # SELECT / EXAMINE
    # ------------------------------------------------------------------

    async def _cmd_select(self, tag: str, args: str) -> None:
        folder_name = _unquote(args.strip())
        if not folder_name:
            await self._send(f"{tag} BAD SELECT requires a mailbox name")
            return

        assert self._proxy_session is not None
        async with self._session_factory() as db:
            mailbox = await select_mailbox(
                db=db,
                mailbox_profile_id=self._proxy_session.mailbox_profile_id,
                folder_name=folder_name,
                raw_store_root=self._raw_store_root,
            )

        if mailbox is None:
            await self._send(f"{tag} NO [NONEXISTENT] Mailbox does not exist: {folder_name}")
            return

        self._selected = mailbox
        self._state = ConnState.SELECTED

        await self._send(f"* {mailbox.exists} EXISTS")
        await self._send(f"* {mailbox.recent} RECENT")
        await self._send("* OK [UNSEEN 0] No unseen messages")
        await self._send(f"* OK [UIDVALIDITY {mailbox.uid_validity}] UIDs valid")
        await self._send(f"* OK [UIDNEXT {mailbox.uid_next}] Predicted next UID")
        await self._send("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
        await self._send("* OK [PERMANENTFLAGS ()] No permanent flags in read-only proxy")
        await self._send(f"{tag} OK [READ-ONLY] SELECT completed, mailbox is read-only")

    # ------------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------------

    async def _cmd_status(self, tag: str, args: str) -> None:
        # STATUS "mailbox" (MESSAGES UIDVALIDITY UIDNEXT UNSEEN RECENT)
        m = re.match(r'"?([^"(]+)"?\s*\(([^)]+)\)', args.strip())
        if not m:
            await self._send(f"{tag} BAD STATUS syntax error")
            return

        folder_name = m.group(1).strip()
        items_requested = m.group(2).upper().split()

        assert self._proxy_session is not None
        async with self._session_factory() as db:
            mailbox = await select_mailbox(
                db=db,
                mailbox_profile_id=self._proxy_session.mailbox_profile_id,
                folder_name=folder_name,
                raw_store_root=self._raw_store_root,
            )

        if mailbox is None:
            await self._send(f"{tag} NO [NONEXISTENT] Mailbox does not exist")
            return

        parts: list[str] = []
        for item in items_requested:
            if item == "MESSAGES":
                parts.append(f"MESSAGES {mailbox.exists}")
            elif item == "UIDVALIDITY":
                parts.append(f"UIDVALIDITY {mailbox.uid_validity}")
            elif item == "UIDNEXT":
                parts.append(f"UIDNEXT {mailbox.uid_next}")
            elif item == "UNSEEN":
                parts.append("UNSEEN 0")
            elif item == "RECENT":
                parts.append("RECENT 0")

        await self._send(f'* STATUS "{folder_name}" ({" ".join(parts)})')
        await self._send(f"{tag} OK STATUS completed")

    # ------------------------------------------------------------------
    # CLOSE
    # ------------------------------------------------------------------

    async def _cmd_close(self, tag: str) -> None:
        self._selected = None
        self._state = ConnState.AUTH
        await self._send(f"{tag} OK CLOSE completed")

    # ------------------------------------------------------------------
    # UID (dispatcher for UID SEARCH, UID FETCH)
    # ------------------------------------------------------------------

    async def _cmd_uid(self, tag: str, args: str) -> None:
        if self._state != ConnState.SELECTED:
            await self._send(f"{tag} BAD No mailbox selected")
            return

        uid_args = args.strip().split(None, 1)
        if not uid_args:
            await self._send(f"{tag} BAD UID requires a subcommand")
            return

        subcmd = uid_args[0].upper()
        sub_args = uid_args[1] if len(uid_args) > 1 else ""

        if subcmd == "SEARCH":
            await self._cmd_uid_search(tag, sub_args)
        elif subcmd == "FETCH":
            await self._cmd_uid_fetch(tag, sub_args)
        elif subcmd in ("STORE", "COPY", "MOVE", "EXPUNGE"):
            await self._send(f"{tag} {_READONLY_MSG}")
        else:
            await self._send(f"{tag} BAD Unknown UID subcommand: {subcmd}")

    # ------------------------------------------------------------------
    # SEARCH (seq-num based — redirects to UID list for simplicity)
    # ------------------------------------------------------------------

    async def _cmd_search(self, tag: str, args: str) -> None:
        """Return sequence numbers for matching messages.

        MVP implementation: supports ALL, returning all sequence numbers.
        """
        assert self._selected is not None
        uids = _apply_search(args, self._selected)
        # For non-UID SEARCH, return sequence numbers
        seqs = []
        for uid in uids:
            msg = self._selected.by_uid(uid)
            if msg:
                seqs.append(str(msg.seq))
        await self._send(f"* SEARCH {' '.join(seqs)}")
        await self._send(f"{tag} OK SEARCH completed")

    # ------------------------------------------------------------------
    # UID SEARCH
    # ------------------------------------------------------------------

    async def _cmd_uid_search(self, tag: str, args: str) -> None:
        """Return UIDs of matching messages.

        MVP supports: ALL, UID <range>, ALL (no criteria = ALL).
        """
        assert self._selected is not None
        uids = _apply_search(args, self._selected)
        uid_str = " ".join(str(u) for u in sorted(uids))
        await self._send(f"* SEARCH {uid_str}")
        await self._send(f"{tag} OK UID SEARCH completed")

    # ------------------------------------------------------------------
    # UID FETCH
    # ------------------------------------------------------------------

    async def _cmd_uid_fetch(self, tag: str, args: str) -> None:
        """Fetch message data for specified UIDs.

        Supports fetch items: UID, FLAGS, BODY[], BODY[HEADER], BODY[TEXT],
        RFC822, RFC822.SIZE, ENVELOPE, BODYSTRUCTURE, BODY.PEEK[].
        """
        assert self._selected is not None

        # Parse: uid_set fetch_items
        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            await self._send(f"{tag} BAD UID FETCH requires UID set and items")
            return

        uid_set_str, items_str = parts[0], parts[1]
        target_uids = _parse_uid_set(uid_set_str, self._selected)

        for msg in self._selected.messages:
            if msg.uid not in target_uids:
                continue
            response = await _build_fetch_response(msg, items_str)
            await self._send(f"* {msg.seq} FETCH ({response})")

        await self._send(f"{tag} OK UID FETCH completed")

    # ------------------------------------------------------------------
    # FETCH (sequence-number based)
    # ------------------------------------------------------------------

    async def _cmd_fetch(self, tag: str, args: str) -> None:
        """Sequence-number based FETCH — identical logic to UID FETCH."""
        assert self._selected is not None

        parts = args.strip().split(None, 1)
        if len(parts) < 2:
            await self._send(f"{tag} BAD FETCH requires sequence set and items")
            return

        seq_set_str, items_str = parts[0], parts[1]
        target_seqs = _parse_seq_set(seq_set_str, self._selected)

        for msg in self._selected.messages:
            if msg.seq not in target_seqs:
                continue
            response = await _build_fetch_response(msg, items_str)
            await self._send(f"* {msg.seq} FETCH ({response})")

        await self._send(f"{tag} OK FETCH completed")

    # ------------------------------------------------------------------
    # Guard helpers
    # ------------------------------------------------------------------

    async def _require_auth(self, tag: str) -> bool:
        if self._state in (ConnState.NOT_AUTH, ConnState.LOGOUT):
            await self._send(f"{tag} NO Not authenticated")
            return False
        return True

    async def _require_selected(self, tag: str) -> bool:
        if not await self._require_auth(tag):
            return False
        if self._state != ConnState.SELECTED:
            await self._send(f"{tag} BAD No mailbox selected")
            return False
        return True


# ---------------------------------------------------------------------------
# Mutation command set
# ---------------------------------------------------------------------------

_MUTATION_COMMANDS = frozenset(
    {
        "STORE",
        "COPY",
        "MOVE",
        "APPEND",
        "EXPUNGE",
        "CREATE",
        "DELETE",
        "RENAME",
        "SUBSCRIBE",
        "UNSUBSCRIBE",
        "LSUB",
        "SETACL",
        "DELETEACL",
        "SETMETADATA",
        "SETQUOTA",
        "SORT",
    }
)


# ---------------------------------------------------------------------------
# Search implementation
# ---------------------------------------------------------------------------


def _apply_search(criteria: str, mailbox: ImapMailbox) -> list[int]:
    """Return UIDs matching the search criteria.

    MVP supports: ALL (or empty), UID <range>.
    Everything else falls back to ALL.
    """
    upper = criteria.strip().upper()
    all_uids = [m.uid for m in mailbox.messages]

    if not upper or upper == "ALL":
        return all_uids

    # UID range: e.g. "UID 1:*" or "UID 1,2,3"
    if upper.startswith("UID "):
        uid_range_str = criteria.strip()[4:]
        target = _parse_uid_set(uid_range_str, mailbox)
        return [u for u in all_uids if u in target]

    # Fallback — return all UIDs for unsupported criteria.
    log.debug("imap_proxy.search_fallback", criteria=criteria)
    return all_uids


# ---------------------------------------------------------------------------
# UID/sequence set parsers
# ---------------------------------------------------------------------------


def _parse_uid_set(uid_set_str: str, mailbox: ImapMailbox) -> frozenset[int]:
    """Parse an IMAP UID set string and return a set of matching UIDs.

    Supports: single UIDs (1), ranges (1:5), wildcards (1:*), comma-separated.
    """
    all_uids = [m.uid for m in mailbox.messages]
    if not all_uids:
        return frozenset()

    max_uid = max(all_uids)
    result: set[int] = set()

    for part in uid_set_str.split(","):
        part = part.strip()
        if ":" in part:
            lo_str, hi_str = part.split(":", 1)
            lo = int(lo_str) if lo_str != "*" else max_uid
            hi = max_uid if hi_str == "*" else int(hi_str)
            for uid in all_uids:
                if lo <= uid <= hi:
                    result.add(uid)
        elif part == "*":
            if all_uids:
                result.add(max_uid)
        else:
            try:
                result.add(int(part))
            except ValueError:
                pass

    return frozenset(result)


def _parse_seq_set(seq_set_str: str, mailbox: ImapMailbox) -> frozenset[int]:
    """Parse an IMAP sequence set and return matching 1-based sequence numbers."""
    n = mailbox.exists
    if n == 0:
        return frozenset()

    result: set[int] = set()

    for part in seq_set_str.split(","):
        part = part.strip()
        if ":" in part:
            lo_str, hi_str = part.split(":", 1)
            lo = int(lo_str) if lo_str != "*" else n
            hi = n if hi_str == "*" else int(hi_str)
            for seq in range(max(1, lo), min(n, hi) + 1):
                result.add(seq)
        elif part == "*":
            result.add(n)
        else:
            try:
                seq = int(part)
                if 1 <= seq <= n:
                    result.add(seq)
            except ValueError:
                pass

    return frozenset(result)


# ---------------------------------------------------------------------------
# FETCH response builder
# ---------------------------------------------------------------------------


async def _build_fetch_response(msg: ImapMessage, items_str: str) -> str:
    """Build the parenthesised FETCH response for one message.

    items_str is the raw FETCH items, e.g. "(UID FLAGS BODY[])".
    We parse it and build the corresponding response fields.
    """
    # Normalise: strip outer parens if present.
    normalized = items_str.strip()
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1]

    upper = normalized.upper()

    parts: list[str] = []

    # Always include UID in response.
    if "UID" in upper or True:
        parts.append(f"UID {msg.uid}")

    if "FLAGS" in upper:
        flags_str = " ".join(msg.flags)
        parts.append(f"FLAGS ({flags_str})")

    if "RFC822.SIZE" in upper or "BODY" in upper or "RFC822" in upper:
        parts.append(f"RFC822.SIZE {msg.size}")

    # BODY[] or BODY.PEEK[] — full raw message.
    if re.search(r"BODY(?:\.PEEK)?\[\]", upper) or "RFC822" in upper:
        raw_encoded = _imap_literal(msg.raw_bytes)
        parts.append(f"BODY[] {raw_encoded}")

    # BODY[HEADER] — just the headers.
    elif re.search(r"BODY(?:\.PEEK)?\[HEADER\]", upper):
        header_bytes = _extract_headers(msg.raw_bytes)
        raw_encoded = _imap_literal(header_bytes)
        parts.append(f"BODY[HEADER] {raw_encoded}")

    # BODY[TEXT] — just the body.
    elif re.search(r"BODY(?:\.PEEK)?\[TEXT\]", upper):
        body_bytes = _extract_body(msg.raw_bytes)
        raw_encoded = _imap_literal(body_bytes)
        parts.append(f"BODY[TEXT] {raw_encoded}")

    if "ENVELOPE" in upper:
        env = _build_envelope(msg)
        parts.append(f"ENVELOPE {env}")

    if "BODYSTRUCTURE" in upper or ("BODY" in upper and "[" not in upper):
        parts.append('BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" '
                     f'{msg.size} 1 NIL NIL NIL)')

    # De-duplicate while preserving order, UID first.
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        key = p.split()[0]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return " ".join(unique)


def _imap_literal(data: bytes) -> str:
    """Format bytes as an IMAP literal: {N}\r\nDATA."""
    # For single-line embedding we use the string form; the caller sends it.
    # Because we're inside a single _send() call we embed inline.
    # RFC 3516 BINARY / RFC 4466 LITERAL+ would allow streaming;
    # for simplicity we embed the full data as a Python string representation.
    # NOTE: actual binary literals require the server to send {N}\r\n then raw bytes.
    # We handle this by embedding the decoded text and trusting UTF-8.
    decoded = data.decode("utf-8", errors="replace")
    return f"{{{len(data)}}}\r\n{decoded}"


def _extract_headers(raw: bytes) -> bytes:
    """Return just the header section from a raw RFC 5322 message."""
    sep = b"\r\n\r\n"
    idx = raw.find(sep)
    if idx == -1:
        sep = b"\n\n"
        idx = raw.find(sep)
    if idx == -1:
        return raw
    return raw[: idx + len(sep)]


def _extract_body(raw: bytes) -> bytes:
    """Return just the body section from a raw RFC 5322 message."""
    sep = b"\r\n\r\n"
    idx = raw.find(sep)
    if idx == -1:
        sep = b"\n\n"
        idx = raw.find(sep)
    if idx == -1:
        return b""
    return raw[idx + len(sep):]


def _build_envelope(msg: ImapMessage) -> str:
    """Build an IMAP ENVELOPE response (simplified)."""
    def _nstr(s: str | None) -> str:
        if s is None:
            return "NIL"
        return f'"{s}"'

    date = _nstr(msg.date_str)
    subject = _nstr(msg.subject)
    from_addr = f'(("{msg.from_address or ""}" NIL NIL NIL))'
    return (
        f"({date} {subject} {from_addr} {from_addr} "
        f"{from_addr} NIL NIL NIL NIL NIL)"
    )


# ---------------------------------------------------------------------------
# Argument parsers
# ---------------------------------------------------------------------------


def _parse_login_args(args: str) -> tuple[str | None, str | None]:
    """Parse LOGIN username password (supports quoted strings)."""
    args = args.strip()
    try:
        parts = _split_imap_args(args)
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:
        log.debug("imap_proxy.login_parse_error", args=args)
    return None, None


def _split_imap_args(args: str) -> list[str]:
    """Split an IMAP argument string respecting quoted strings."""
    result: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == '"':
            j = i + 1
            buf: list[str] = []
            while j < len(args):
                if args[j] == '\\' and j + 1 < len(args):
                    buf.append(args[j + 1])
                    j += 2
                elif args[j] == '"':
                    j += 1
                    break
                else:
                    buf.append(args[j])
                    j += 1
            result.append("".join(buf))
            i = j
        elif args[i] == ' ':
            i += 1
        else:
            j = i
            while j < len(args) and args[j] not in (' ', '"'):
                j += 1
            result.append(args[i:j])
            i = j
    return result


def _unquote(s: str) -> str:
    """Strip surrounding double-quotes from a string if present."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------
# IMAP Server
# ---------------------------------------------------------------------------


class ImapServer:
    """Asyncio TCP server that accepts IMAP proxy connections.

    Each connection runs in its own task via ImapConnection.

    Args:
        host:            Bind address.
        port:            TCP port to listen on.
        session_factory: Async session factory from the app DB layer.
        raw_store_root:  Path to the RawMessageStore root for fetching .eml files.
    """

    def __init__(
        self,
        host: str,
        port: int,
        session_factory: AsyncSessionFactory,
        raw_store_root: Path | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._session_factory = session_factory
        self._raw_store_root = raw_store_root
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start the TCP listener."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
        )
        log.info(
            "imap_proxy.listening",
            host=self._host,
            port=self._port,
        )

    async def serve_forever(self) -> None:
        """Serve until cancelled."""
        if self._server is None:
            await self.start()
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop accepting new connections."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            log.info("imap_proxy.stopped")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        conn = ImapConnection(
            reader=reader,
            writer=writer,
            session_factory=self._session_factory,
            raw_store_root=self._raw_store_root,
        )
        await conn.handle()
