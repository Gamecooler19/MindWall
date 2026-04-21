"""SMTP submission proxy server for Mindwall.

Implements an RFC 5321-compatible SMTP submission server subset suitable for
mail clients to authenticate and submit outbound mail through Mindwall.

Supported commands:
  Any state:  QUIT, NOOP
  Pre-auth:   EHLO, HELO
  Pre-auth:   AUTH PLAIN, AUTH LOGIN
  Post-auth:  MAIL FROM, RCPT TO, DATA, RSET
  (EHLO is also accepted post-auth for pipelining clients)

Unsupported (not announced, rejected with 502):
  VRFY, EXPN, TURN, ETRN, any extension not advertised

Architecture:
  - Each TCP connection runs in its own asyncio.Task via SmtpConnection.
  - SmtpServer manages the listener and spawns connections.
  - All database I/O uses a fresh AsyncSession per connection.
  - The delivery service (capture or relay) is called after DATA is accepted.
  - No TLS in this MVP; STARTTLS support is deferred to a later phase.

Security:
  - AUTH is required before MAIL FROM.
  - Message size limit enforced during DATA collection.
  - No credential echoing in logs or protocol responses.
  - AUTH PLAIN and AUTH LOGIN both handled securely.
  - Lines are limited to prevent resource exhaustion.
"""

from __future__ import annotations

import asyncio
import base64
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from app.proxies.smtp.delivery import deliver_outbound
from app.proxies.smtp.session import SmtpProxySession, authenticate_smtp_credentials

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    AsyncSessionFactory = async_sessionmaker[AsyncSession]

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EHLO_BANNER = "Mindwall SMTP submission proxy ready"

# Maximum size of a single line (bytes) — prevent memory exhaustion.
_MAX_LINE_BYTES = 8192

# Maximum seconds a connection may stay idle.
_IDLE_TIMEOUT_SECONDS = 300  # 5 minutes for SMTP

# Announce these EHLO extensions.
_EHLO_EXTENSIONS = [
    "8BITMIME",
    "PIPELINING",
    "AUTH PLAIN LOGIN",
]

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class SmtpState(Enum):
    GREETING = auto()   # Just connected; waiting for EHLO/HELO.
    EHLO = auto()       # EHLO received; ready for AUTH.
    AUTH = auto()       # Authenticated; ready for MAIL FROM.
    MAIL = auto()       # MAIL FROM accepted; ready for RCPT TO.
    RCPT = auto()       # At least one RCPT TO accepted; ready for DATA.
    DATA = auto()       # Inside DATA collection.


# ---------------------------------------------------------------------------
# SMTP connection handler
# ---------------------------------------------------------------------------


class SmtpConnection:
    """Handles one SMTP client connection.

    Manages the SMTP state machine, parses commands, and dispatches to handler
    methods.  All I/O is async (asyncio streams).
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session_factory: AsyncSessionFactory,
        store_root: Path,
        delivery_mode: str,
        max_message_bytes: int,
        relay_timeout: int,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._session_factory = session_factory
        self._store_root = store_root
        self._delivery_mode = delivery_mode
        self._max_message_bytes = max_message_bytes
        self._relay_timeout = relay_timeout

        self._state = SmtpState.GREETING
        self._proxy_session: SmtpProxySession | None = None
        self._ehlo_domain: str | None = None
        self._envelope_from: str | None = None
        self._envelope_to: list[str] = []

        peer = writer.get_extra_info("peername", ("?", 0))
        self._peer = f"{peer[0]}:{peer[1]}"

    # -----------------------------------------------------------------------
    # I/O helpers
    # -----------------------------------------------------------------------

    async def _send(self, line: str) -> None:
        """Send a SMTP response line (CRLF terminated)."""
        self._writer.write((line + "\r\n").encode())
        await self._writer.drain()

    async def _readline(self) -> str | None:
        """Read one CRLF-terminated line, enforcing the line length limit."""
        try:
            raw = await asyncio.wait_for(
                self._reader.readline(),
                timeout=_IDLE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            log.info("smtp_proxy.idle_timeout", peer=self._peer)
            return None
        if not raw:
            return None
        if len(raw) > _MAX_LINE_BYTES:
            return None
        return raw.rstrip(b"\r\n").decode("utf-8", errors="replace")

    def _close(self) -> None:
        try:
            self._writer.close()
        except Exception:  # noqa: S110 — best-effort cleanup on close
            pass

    # -----------------------------------------------------------------------
    # Envelope helpers
    # -----------------------------------------------------------------------

    def _reset_envelope(self) -> None:
        """Clear mail transaction state but keep auth."""
        self._envelope_from = None
        self._envelope_to = []
        if self._proxy_session is not None:
            self._state = SmtpState.AUTH
        else:
            self._state = SmtpState.EHLO

    # -----------------------------------------------------------------------
    # Command parser
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_command(line: str) -> tuple[str, str]:
        """Split 'COMMAND args' → ('COMMAND', 'args').  Command is uppercased."""
        parts = line.split(" ", 1)
        cmd = parts[0].upper().strip()
        args = parts[1].strip() if len(parts) > 1 else ""
        return cmd, args

    # -----------------------------------------------------------------------
    # AUTH helpers
    # -----------------------------------------------------------------------

    async def _handle_auth_plain(self, db: AsyncSession, initial_blob: str) -> bool:
        """Handle AUTH PLAIN [initial-response].

        AUTH PLAIN sends a single base64-encoded string: \0user\0password.
        Returns True on success.
        """
        if initial_blob:
            b64 = initial_blob
        else:
            await self._send("334 ")
            line = await self._readline()
            if line is None:
                return False
            b64 = line.strip()

        try:
            decoded = base64.b64decode(b64).split(b"\x00")
            # Format: [authzid, authcid, passwd] (authzid may be empty)
            if len(decoded) < 3:
                return False
            username = decoded[-2].decode("utf-8", errors="replace")
            password = decoded[-1].decode("utf-8", errors="replace")
        except Exception:
            return False

        session = await authenticate_smtp_credentials(db, username, password)
        if session is None:
            return False
        self._proxy_session = session
        return True

    async def _handle_auth_login(self, db: AsyncSession) -> bool:
        """Handle AUTH LOGIN (two-step base64 username + password challenge).

        Returns True on success.
        """
        await self._send("334 VXNlcm5hbWU6")  # "Username:" in base64
        line = await self._readline()
        if line is None:
            return False
        try:
            username = base64.b64decode(line.strip()).decode("utf-8", errors="replace")
        except Exception:
            return False

        await self._send("334 UGFzc3dvcmQ6")  # "Password:" in base64
        line = await self._readline()
        if line is None:
            return False
        try:
            password = base64.b64decode(line.strip()).decode("utf-8", errors="replace")
        except Exception:
            return False

        session = await authenticate_smtp_credentials(db, username, password)
        if session is None:
            return False
        self._proxy_session = session
        return True

    # -----------------------------------------------------------------------
    # DATA collection
    # -----------------------------------------------------------------------

    async def _collect_data(self) -> bytes | None:
        """Collect DATA lines until the <CRLF>.<CRLF> terminator.

        Returns raw bytes (headers + body), or None if max_message_bytes exceeded.
        Performs SMTP dot-unstuffing (RFC 5321 §4.5.2).
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                raw = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=_IDLE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                return None
            if not raw:
                return None
            if raw == b".\r\n":
                break
            # Dot-unstuffing: leading ".." → "."
            if raw.startswith(b".."):
                raw = raw[1:]
            total += len(raw)
            if total > self._max_message_bytes:
                log.warning(
                    "smtp_proxy.message_too_large",
                    peer=self._peer,
                    limit=self._max_message_bytes,
                )
                return None
            chunks.append(raw)
        return b"".join(chunks)

    # -----------------------------------------------------------------------
    # Address extraction helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_address(angle_arg: str) -> str:
        """Extract address from 'FROM:<addr>' or 'TO:<addr>' argument."""
        # Find first '<' ... '>' pair.
        start = angle_arg.find("<")
        end = angle_arg.find(">")
        if start != -1 and end != -1 and end > start:
            return angle_arg[start + 1 : end].strip()
        # No angle brackets — return stripped arg (MAIL FROM: addr syntax).
        colon_idx = angle_arg.find(":")
        if colon_idx != -1:
            return angle_arg[colon_idx + 1 :].strip()
        return angle_arg.strip()

    # -----------------------------------------------------------------------
    # Main connection loop
    # -----------------------------------------------------------------------

    async def handle(self, db: AsyncSession) -> None:
        """Run the SMTP command loop for this connection."""
        await self._send(f"220 {_EHLO_BANNER}")
        log.info("smtp_proxy.connection_open", peer=self._peer)

        try:
            while True:
                line = await self._readline()
                if line is None:
                    break

                cmd, args = self._parse_command(line)

                if cmd == "QUIT":
                    await self._send("221 2.0.0 Bye")
                    break

                if cmd == "NOOP":
                    await self._send("250 2.0.0 OK")
                    continue

                if cmd in ("EHLO", "HELO"):
                    self._ehlo_domain = args or "unknown"
                    if cmd == "EHLO":
                        await self._send("250-Mindwall")
                        for ext in _EHLO_EXTENSIONS:
                            await self._send(f"250-{ext}")
                        await self._send("250 SMTPUTF8")
                    else:
                        await self._send("250 Mindwall SMTP proxy")
                    if self._proxy_session is not None:
                        self._state = SmtpState.AUTH
                    else:
                        self._state = SmtpState.EHLO
                    continue

                if cmd == "AUTH":
                    if self._state == SmtpState.GREETING:
                        await self._send("503 5.5.1 Send EHLO first")
                        continue
                    if self._proxy_session is not None:
                        await self._send("503 5.5.1 Already authenticated")
                        continue

                    method, _, initial = args.partition(" ")
                    method = method.upper()

                    if method == "PLAIN":
                        ok = await self._handle_auth_plain(db, initial)
                    elif method == "LOGIN":
                        ok = await self._handle_auth_login(db)
                    else:
                        await self._send("504 5.5.4 Unrecognised AUTH mechanism")
                        continue

                    if ok:
                        self._state = SmtpState.AUTH
                        await self._send(
                            f"235 2.7.0 Authentication successful, "
                            f"logged in as {self._proxy_session.proxy_username}"
                        )
                        log.info(
                            "smtp_proxy.auth_success",
                            peer=self._peer,
                            username=self._proxy_session.proxy_username,
                        )
                    else:
                        await self._send("535 5.7.8 Authentication credentials invalid")
                        log.warning("smtp_proxy.auth_failed", peer=self._peer)
                    continue

                # All commands below require authentication.
                if self._proxy_session is None:
                    await self._send("530 5.7.0 Authentication required")
                    continue

                if cmd == "RSET":
                    self._reset_envelope()
                    await self._send("250 2.0.0 Reset OK")
                    continue

                if cmd == "MAIL":
                    if self._state not in (SmtpState.AUTH, SmtpState.EHLO):
                        await self._send("503 5.5.1 Bad sequence of commands")
                        continue
                    addr = self._extract_address(args)
                    self._envelope_from = addr
                    self._envelope_to = []
                    self._state = SmtpState.MAIL
                    await self._send(f"250 2.1.0 OK sender {addr!r} accepted")
                    continue

                if cmd == "RCPT":
                    if self._state not in (SmtpState.MAIL, SmtpState.RCPT):
                        await self._send("503 5.5.1 MAIL FROM required first")
                        continue
                    addr = self._extract_address(args)
                    self._envelope_to.append(addr)
                    self._state = SmtpState.RCPT
                    await self._send(f"250 2.1.5 OK recipient {addr!r} accepted")
                    continue

                if cmd == "DATA":
                    if self._state != SmtpState.RCPT:
                        await self._send("503 5.5.1 RCPT TO required first")
                        continue
                    await self._send("354 Start mail input; end with <CRLF>.<CRLF>")
                    raw = await self._collect_data()
                    if raw is None:
                        await self._send("552 5.3.4 Message too large or connection lost")
                        self._reset_envelope()
                        continue

                    # Deliver (capture or relay).
                    assert self._envelope_from is not None  # guaranteed by state machine
                    try:
                        outbound = await deliver_outbound(
                            db=db,
                            session=self._proxy_session,
                            envelope_from=self._envelope_from,
                            envelope_to=list(self._envelope_to),
                            raw_message=raw,
                            store_root=self._store_root,
                            delivery_mode=self._delivery_mode,
                            relay_timeout=self._relay_timeout,
                        )
                        if outbound.delivery_status.value == "failed":
                            err = outbound.relay_error or "Delivery failed"
                            await self._send(f"550 5.0.0 {err}")
                        else:
                            await self._send(
                                f"250 2.0.0 Message accepted "
                                f"(id={outbound.id} mode={outbound.delivery_mode})"
                            )
                    except Exception as exc:
                        log.error(
                            "smtp_proxy.delivery_error",
                            peer=self._peer,
                            error=type(exc).__name__,
                        )
                        await self._send("451 4.3.0 Internal error during delivery")
                    finally:
                        self._reset_envelope()
                    continue

                # Unknown command.
                await self._send(f"502 5.5.1 Command {cmd!r} not implemented")

        except ConnectionResetError:
            log.info("smtp_proxy.connection_reset", peer=self._peer)
        finally:
            log.info("smtp_proxy.connection_closed", peer=self._peer)
            self._close()


# ---------------------------------------------------------------------------
# SMTP server (listener)
# ---------------------------------------------------------------------------


class SmtpServer:
    """Manages the TCP listener and spawns SmtpConnection tasks."""

    def __init__(
        self,
        host: str,
        port: int,
        session_factory: AsyncSessionFactory,
        store_root: Path,
        delivery_mode: str = "capture",
        max_message_bytes: int = 26_214_400,
        relay_timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._session_factory = session_factory
        self._store_root = store_root
        self._delivery_mode = delivery_mode
        self._max_message_bytes = max_message_bytes
        self._relay_timeout = relay_timeout
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        """Start listening for SMTP connections."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self._host,
            port=self._port,
        )
        log.info(
            "smtp_proxy.listening",
            host=self._host,
            port=self._port,
            delivery_mode=self._delivery_mode,
        )

    async def serve_forever(self) -> None:
        """Serve until stop() is called."""
        if self._server is None:
            await self.start()
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Gracefully stop the server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            log.info("smtp_proxy.stopped")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Callback invoked by asyncio for each new TCP connection."""
        conn = SmtpConnection(
            reader=reader,
            writer=writer,
            session_factory=self._session_factory,
            store_root=self._store_root,
            delivery_mode=self._delivery_mode,
            max_message_bytes=self._max_message_bytes,
            relay_timeout=self._relay_timeout,
        )
        async with self._session_factory() as db:
            await conn.handle(db)
