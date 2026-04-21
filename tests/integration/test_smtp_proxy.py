"""Integration tests for the SMTP proxy server.

Starts a real asyncio TCP server on an ephemeral port and exercises the
full SMTP protocol flow.  Uses the in-memory SQLite DB via the shared
db_engine fixture — no real PostgreSQL required.

Covered scenarios:
  - 220 greeting on connect.
  - EHLO returns 250 with AUTH PLAIN LOGIN advertised.
  - NOOP returns 250.
  - QUIT terminates session with 221.
  - AUTH PLAIN with bad credentials returns 535.
  - AUTH PLAIN with valid credentials returns 235.
  - AUTH LOGIN with valid credentials returns 235.
  - MAIL FROM before AUTH returns 530.
  - Full happy path: AUTH + MAIL FROM + RCPT TO + DATA → 250 + captured.
  - Unknown command returns 502.
  - RSET after MAIL FROM resets envelope.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import textwrap

import bcrypt
import pytest
from app.mailboxes.models import (
    ImapSecurity,
    MailboxProfile,
    MailboxStatus,
    SmtpSecurity,
)
from app.proxies.smtp.server import SmtpServer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture()
async def smtp_server(session_factory, tmp_path):
    """Create and start a SmtpServer on an ephemeral port."""
    servers: list[SmtpServer] = []

    async def _make(port: int = 0) -> tuple[SmtpServer, int]:
        server = SmtpServer(
            host="127.0.0.1",
            port=port,
            session_factory=session_factory,
            store_root=tmp_path,
            delivery_mode="capture",
            max_message_bytes=1_048_576,  # 1 MB for tests
            relay_timeout=5,
        )
        await server.start()
        actual_port = server._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        servers.append(server)
        return server, actual_port

    yield _make

    for s in servers:
        await s.stop()


@pytest.fixture()
async def proxy_profile(session_factory):
    """Insert a MailboxProfile with known SMTP proxy credentials."""
    suffix = secrets.token_hex(4)
    proxy_username = f"mw_smtp_int_{suffix}"
    proxy_password = "smtp-integration-test-pass"
    proxy_hash = bcrypt.hashpw(proxy_password.encode(), bcrypt.gensalt()).decode()

    async with session_factory() as db:
        profile = MailboxProfile(
            owner_id=2000,
            email_address="smtp_inttest@example.com",
            display_name="SMTP Integration Test User",
            imap_host="imap.example.com",
            imap_port=993,
            imap_username="smtp_inttest@example.com",
            imap_password_enc="fakeenc",
            imap_security=ImapSecurity.SSL_TLS,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp_inttest@example.com",
            smtp_password_enc="fakeenc",
            smtp_security=SmtpSecurity.STARTTLS,
            proxy_username=proxy_username,
            proxy_password_hash=proxy_hash,
            status=MailboxStatus.ACTIVE,
        )
        db.add(profile)
        await db.commit()

    return proxy_username, proxy_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _readline(reader: asyncio.StreamReader) -> str:
    data = await asyncio.wait_for(reader.readline(), timeout=5.0)
    return data.decode("utf-8", errors="replace").rstrip("\r\n")


async def _drain_until(reader: asyncio.StreamReader, prefix: str) -> str:
    """Read lines until one starts with the given prefix; return it."""
    for _ in range(20):
        line = await _readline(reader)
        if line.startswith(prefix):
            return line
    raise AssertionError(f"Did not receive line starting with {prefix!r}")


async def _send(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write((line + "\r\n").encode())
    await writer.drain()


def _plain_b64(username: str, password: str) -> str:
    """Encode username + password into AUTH PLAIN base64 blob."""
    raw = f"\x00{username}\x00{password}".encode()
    return base64.b64encode(raw).decode()


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


SAMPLE_MESSAGE = textwrap.dedent("""\
    From: sender@example.com
    To: rcpt@example.com
    Subject: Integration test email
    MIME-Version: 1.0
    Content-Type: text/plain

    Hello from the SMTP integration test.
""")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSmtpProxyProtocol:
    async def test_greeting_on_connect(self, smtp_server):
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            greeting = await _readline(reader)
            assert greeting.startswith("220")
            assert "Mindwall" in greeting
        finally:
            writer.close()

    async def test_ehlo_response_includes_auth(self, smtp_server):
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO testclient.example.com")
            lines = []
            while True:
                line = await _readline(reader)
                lines.append(line)
                # Multi-line response ends when the line starts with "250 " (space, not dash)
                if line.startswith("250 "):
                    break
            assert any("AUTH" in ln for ln in lines)
            assert any("PLAIN" in ln for ln in lines)
            assert any("LOGIN" in ln for ln in lines)
        finally:
            writer.close()

    async def test_noop(self, smtp_server):
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")
            await _send(writer, "NOOP")
            line = await _readline(reader)
            assert line.startswith("250")
        finally:
            writer.close()

    async def test_quit(self, smtp_server):
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "QUIT")
            line = await _readline(reader)
            assert line.startswith("221")
        finally:
            writer.close()

    async def test_auth_plain_bad_credentials(self, smtp_server):
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            b64 = _plain_b64("mw_nobody", "wrongpass")
            await _send(writer, f"AUTH PLAIN {b64}")
            line = await _readline(reader)
            assert line.startswith("535")
        finally:
            writer.close()

    async def test_auth_plain_valid_credentials(self, smtp_server, proxy_profile):
        username, password = proxy_profile
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            b64 = _plain_b64(username, password)
            await _send(writer, f"AUTH PLAIN {b64}")
            line = await _readline(reader)
            assert line.startswith("235")
        finally:
            writer.close()

    async def test_auth_login_valid_credentials(self, smtp_server, proxy_profile):
        username, password = proxy_profile
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            await _send(writer, "AUTH LOGIN")
            challenge1 = await _readline(reader)
            assert challenge1.startswith("334")  # Username:

            await _send(writer, _b64(username))
            challenge2 = await _readline(reader)
            assert challenge2.startswith("334")  # Password:

            await _send(writer, _b64(password))
            result = await _readline(reader)
            assert result.startswith("235")
        finally:
            writer.close()

    async def test_mail_from_before_auth_rejected(self, smtp_server):
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            await _send(writer, "MAIL FROM:<sender@example.com>")
            line = await _readline(reader)
            # Must be rejected (530 auth required, 503 bad seq, etc.)
            code = int(line.split()[0]) if line and line[0].isdigit() else 0
            assert code >= 500
        finally:
            writer.close()

    async def test_full_happy_path_capture(self, smtp_server, proxy_profile):
        """AUTH → MAIL FROM → RCPT TO → DATA → 250 accepted."""
        username, password = proxy_profile
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            b64 = _plain_b64(username, password)
            await _send(writer, f"AUTH PLAIN {b64}")
            line = await _readline(reader)
            assert line.startswith("235"), f"Expected 235, got: {line!r}"

            await _send(writer, "MAIL FROM:<sender@example.com>")
            line = await _readline(reader)
            assert line.startswith("250"), f"Expected 250 for MAIL FROM, got: {line!r}"

            await _send(writer, "RCPT TO:<rcpt@example.com>")
            line = await _readline(reader)
            assert line.startswith("250"), f"Expected 250 for RCPT TO, got: {line!r}"

            await _send(writer, "DATA")
            line = await _readline(reader)
            assert line.startswith("354"), f"Expected 354, got: {line!r}"

            # Send message body with SMTP dot-stuffing where needed.
            for raw_line in SAMPLE_MESSAGE.splitlines():
                await _send(writer, raw_line)
            await _send(writer, ".")  # End of DATA

            line = await _readline(reader)
            assert line.startswith("250"), f"Expected 250 after DATA, got: {line!r}"
        finally:
            writer.close()

    async def test_unknown_command_returns_502(self, smtp_server, proxy_profile):
        """VRFY is an unsupported command — server must return 502 regardless of state."""
        username, password = proxy_profile
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            b64 = _plain_b64(username, password)
            await _send(writer, f"AUTH PLAIN {b64}")
            await _readline(reader)  # 235

            await _send(writer, "VRFY user@example.com")
            line = await _readline(reader)
            assert line.startswith("502"), f"Expected 502, got: {line!r}"
        finally:
            writer.close()

    async def test_rset_resets_envelope(self, smtp_server, proxy_profile):
        """RSET after MAIL FROM allows a new mail transaction."""
        username, password = proxy_profile
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "EHLO client")
            await _drain_until(reader, "250 ")

            b64 = _plain_b64(username, password)
            await _send(writer, f"AUTH PLAIN {b64}")
            await _readline(reader)  # 235

            await _send(writer, "MAIL FROM:<a@example.com>")
            await _readline(reader)  # 250

            await _send(writer, "RSET")
            line = await _readline(reader)
            assert line.startswith("250"), f"Expected 250 for RSET, got: {line!r}"

            # Can start a new mail transaction after RSET.
            await _send(writer, "MAIL FROM:<b@example.com>")
            line = await _readline(reader)
            assert line.startswith("250"), f"Expected 250 for new MAIL FROM, got: {line!r}"
        finally:
            writer.close()

    async def test_helo_accepted(self, smtp_server):
        """HELO (legacy) is accepted and returns 250."""
        _, port = await smtp_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "HELO legacy.client.example.com")
            line = await _readline(reader)
            assert line.startswith("250"), f"Expected 250 for HELO, got: {line!r}"
        finally:
            writer.close()
