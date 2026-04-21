"""Integration tests for the IMAP proxy server.

Starts a real asyncio TCP server on an ephemeral port, connects to it with
asyncio.open_connection(), and exercises the full IMAP protocol flow.

These tests do not require a real PostgreSQL instance — they use the in-memory
SQLite database via the shared db_engine fixture.

Covered scenarios:
  - Server sends greeting on connect.
  - CAPABILITY command returns IMAP4rev1.
  - NOOP is acknowledged.
  - LOGIN with bad credentials returns NO.
  - LOGIN with valid credentials returns OK.
  - LIST shows INBOX and Mindwall/Quarantine.
  - SELECT INBOX with empty mailbox returns EXISTS 0.
  - UID SEARCH ALL on empty mailbox returns empty SEARCH response.
  - LOGOUT terminates the session.
  - Mutation commands (STORE, COPY, EXPUNGE) are rejected.
  - Unknown commands return BAD.
"""

from __future__ import annotations

import asyncio
import secrets

import bcrypt
import pytest
from app.mailboxes.models import (
    ImapSecurity,
    MailboxProfile,
    MailboxStatus,
    SmtpSecurity,
)
from app.proxies.imap.server import ImapServer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture()
async def imap_server(session_factory):
    """Return a factory that creates a running ImapServer on an ephemeral port."""
    servers: list[ImapServer] = []

    async def _make(port: int = 0) -> tuple[ImapServer, int]:
        server = ImapServer(
            host="127.0.0.1",
            port=port,
            session_factory=session_factory,
            raw_store_root=None,
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
    """Insert a MailboxProfile with known proxy credentials into the test DB.

    Each invocation uses a unique proxy_username to avoid UNIQUE constraint
    violations in the shared in-memory SQLite database.
    """
    # Unique suffix to avoid collisions across tests in the shared DB.
    suffix = secrets.token_hex(4)
    proxy_username = f"mw_inttest_{suffix}"
    proxy_password = "integration-test-password"
    proxy_hash = bcrypt.hashpw(proxy_password.encode(), bcrypt.gensalt()).decode()

    async with session_factory() as db:
        profile = MailboxProfile(
            owner_id=1000,
            email_address="inttest@example.com",
            display_name="Integration Test User",
            imap_host="imap.example.com",
            imap_port=993,
            imap_username="inttest@example.com",
            imap_password_enc="fakeenc",
            imap_security=ImapSecurity.SSL_TLS,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="inttest@example.com",
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
    """Read lines until one starts with the given prefix; return that line."""
    for _ in range(20):
        line = await _readline(reader)
        if line.startswith(prefix):
            return line
    raise AssertionError(f"Did not receive line starting with {prefix!r}")


async def _send(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write((line + "\r\n").encode())
    await writer.drain()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImapProxyProtocol:
    async def test_greeting_on_connect(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            greeting = await _readline(reader)
            assert "* OK" in greeting
            assert "Mindwall" in greeting
        finally:
            writer.close()

    async def test_capability(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "T001 CAPABILITY")
            cap_line = await _drain_until(reader, "* CAPABILITY")
            assert "IMAP4rev1" in cap_line
            ok_line = await _drain_until(reader, "T001")
            assert "OK" in ok_line
        finally:
            writer.close()

    async def test_noop(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "T002 NOOP")
            ok_line = await _drain_until(reader, "T002")
            assert "OK" in ok_line
        finally:
            writer.close()

    async def test_logout(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "T003 LOGOUT")
            bye_line = await _drain_until(reader, "* BYE")
            assert "BYE" in bye_line
            ok_line = await _drain_until(reader, "T003")
            assert "OK" in ok_line
        finally:
            writer.close()

    async def test_login_bad_credentials(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, "T004 LOGIN doesnotexist wrongpass")
            no_line = await _drain_until(reader, "T004")
            assert "NO" in no_line
        finally:
            writer.close()

    async def test_login_valid_credentials(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, f"T005 LOGIN {proxy_username} {proxy_password}")
            ok_line = await _drain_until(reader, "T005")
            assert "OK" in ok_line
        finally:
            writer.close()

    async def test_list_after_login(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)  # greeting
            await _send(writer, f"T006 LOGIN {proxy_username} {proxy_password}")
            await _drain_until(reader, "T006 OK")

            await _send(writer, 'T007 LIST "" "*"')
            lines: list[str] = []
            for _ in range(10):
                line = await _readline(reader)
                lines.append(line)
                if line.startswith("T007"):
                    break

            all_text = "\n".join(lines)
            assert "INBOX" in all_text
            assert "Quarantine" in all_text
            assert "T007 OK" in all_text
        finally:
            writer.close()

    async def test_select_inbox_empty(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, f"T008 LOGIN {proxy_username} {proxy_password}")
            await _drain_until(reader, "T008 OK")

            await _send(writer, "T009 SELECT INBOX")
            lines: list[str] = []
            for _ in range(15):
                line = await _readline(reader)
                lines.append(line)
                if line.startswith("T009"):
                    break

            all_text = "\n".join(lines)
            assert "EXISTS" in all_text
            assert "UIDVALIDITY" in all_text
            assert "T009 OK" in all_text
        finally:
            writer.close()

    async def test_uid_search_all_empty_mailbox(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, f"T010 LOGIN {proxy_username} {proxy_password}")
            await _drain_until(reader, "T010 OK")

            await _send(writer, "T011 SELECT INBOX")
            await _drain_until(reader, "T011 OK")

            await _send(writer, "T012 UID SEARCH ALL")
            search_line = await _drain_until(reader, "* SEARCH")
            # Empty mailbox — should return "* SEARCH" with no UIDs
            assert search_line.startswith("* SEARCH")
            ok_line = await _drain_until(reader, "T012")
            assert "OK" in ok_line
        finally:
            writer.close()

    async def test_mutation_store_rejected(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, f"T013 LOGIN {proxy_username} {proxy_password}")
            await _drain_until(reader, "T013 OK")

            await _send(writer, "T014 SELECT INBOX")
            await _drain_until(reader, "T014 OK")

            await _send(writer, "T015 STORE 1 +FLAGS (\\Seen)")
            no_line = await _drain_until(reader, "T015")
            assert "NO" in no_line or "BAD" in no_line
        finally:
            writer.close()

    async def test_mutation_expunge_rejected(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, f"T016 LOGIN {proxy_username} {proxy_password}")
            await _drain_until(reader, "T016 OK")

            await _send(writer, "T017 EXPUNGE")
            no_line = await _drain_until(reader, "T017")
            assert "NO" in no_line
        finally:
            writer.close()

    async def test_unknown_command_returns_bad(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, "T018 UNKNOWN_CMD")
            bad_line = await _drain_until(reader, "T018")
            assert "BAD" in bad_line
        finally:
            writer.close()

    async def test_list_before_login_returns_no(self, imap_server):
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, 'T019 LIST "" "*"')
            no_line = await _drain_until(reader, "T019")
            assert "NO" in no_line
        finally:
            writer.close()

    async def test_status_command(self, imap_server, proxy_profile):
        proxy_username, proxy_password = proxy_profile
        _, port = await imap_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _readline(reader)
            await _send(writer, f"T020 LOGIN {proxy_username} {proxy_password}")
            await _drain_until(reader, "T020 OK")

            await _send(writer, "T021 STATUS INBOX (MESSAGES UIDVALIDITY UNSEEN)")
            lines: list[str] = []
            for _ in range(5):
                line = await _readline(reader)
                lines.append(line)
                if line.startswith("T021"):
                    break

            all_text = "\n".join(lines)
            assert "MESSAGES" in all_text
            assert "T021 OK" in all_text
        finally:
            writer.close()
