"""Upstream IMAP and SMTP connectivity testers.

Each tester runs blocking stdlib I/O (imaplib / smtplib) inside
asyncio.to_thread() so as not to block the event loop.

Design principles:
  - Always apply timeouts — never hang indefinitely.
  - Return structured ConnectivityResult; never raise to callers.
  - Map all exceptions to safe, user-facing messages — never expose
    server names, stack traces, or credential hints in error text.
  - Never log plaintext passwords.
"""

import asyncio
import imaplib
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass

import structlog

from app.mailboxes.models import ImapSecurity, SmtpSecurity

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class ConnectivityResult:
    """Outcome of a single upstream connectivity test."""

    success: bool
    error_message: str | None = None   # Safe, user-facing text — no secrets
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Safe error mappers — keep internal exception details off user-facing output
# ---------------------------------------------------------------------------


def _safe_imap_error(exc: Exception) -> str:
    """Map an IMAP-related exception to a safe user-facing message."""
    if isinstance(exc, imaplib.IMAP4.error):
        msg = str(exc).lower()
        if "authenticate" in msg or "login" in msg or "authenticationfailed" in msg:
            return "IMAP authentication failed. Check your username and password."
        return "IMAP server returned an error. Check your host and port settings."
    if isinstance(exc, TimeoutError | socket.timeout):
        return "IMAP connection timed out. Check the host, port, and firewall settings."
    if isinstance(exc, ConnectionRefusedError):
        return "IMAP connection refused. Verify the host and port are correct."
    if isinstance(exc, ssl.SSLError):
        return "IMAP TLS/SSL error. Check the security mode setting."
    if isinstance(exc, OSError):
        return "IMAP network error. Verify the host is reachable."
    return "IMAP connection failed. Check your server settings."


def _safe_smtp_error(exc: Exception) -> str:
    """Map an SMTP-related exception to a safe user-facing message."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "SMTP authentication failed. Check your username and password."
    if isinstance(exc, smtplib.SMTPConnectError):
        return "SMTP connection failed. Verify the host and port are correct."
    if isinstance(exc, smtplib.SMTPException):
        return "SMTP server returned an error. Check your host and security settings."
    if isinstance(exc, TimeoutError | socket.timeout):
        return "SMTP connection timed out. Check the host, port, and firewall settings."
    if isinstance(exc, ConnectionRefusedError):
        return "SMTP connection refused. Verify the host and port are correct."
    if isinstance(exc, ssl.SSLError):
        return "SMTP TLS/SSL error. Check the security mode setting."
    if isinstance(exc, OSError):
        return "SMTP network error. Verify the host is reachable."
    return "SMTP connection failed. Check your server settings."


# ---------------------------------------------------------------------------
# Synchronous helpers — run in thread pool via asyncio.to_thread()
# ---------------------------------------------------------------------------


def _test_imap_sync(
    host: str,
    port: int,
    security: ImapSecurity,
    username: str,
    password: str,
    timeout: int,
) -> ConnectivityResult:
    """Perform a blocking IMAP connectivity test.

    Sequence: connect → (STARTTLS if applicable) → login → logout.
    Password is used only for authentication and is never logged.
    """
    start = time.monotonic()
    conn: imaplib.IMAP4 | None = None
    try:
        if security == ImapSecurity.SSL_TLS:
            conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host, port, timeout=timeout)
            if security == ImapSecurity.STARTTLS:
                conn.starttls()

        conn.login(username, password)
        conn.logout()

        latency = (time.monotonic() - start) * 1000
        log.info(
            "connectivity.imap_ok",
            host=host,
            port=port,
            security=security,
            latency_ms=round(latency, 1),
        )
        return ConnectivityResult(success=True, latency_ms=round(latency, 1))

    except Exception as exc:
        error_msg = _safe_imap_error(exc)
        log.warning(
            "connectivity.imap_failed",
            host=host,
            port=port,
            security=security,
            exc_type=type(exc).__name__,
        )
        return ConnectivityResult(success=False, error_message=error_msg)

    finally:
        # Ensure the connection is closed even if logout failed.
        if conn is not None:
            try:
                conn.shutdown()
            except Exception:  # noqa: S110 — cleanup failure is expected and harmless
                pass


def _test_smtp_sync(
    host: str,
    port: int,
    security: SmtpSecurity,
    username: str,
    password: str,
    timeout: int,
) -> ConnectivityResult:
    """Perform a blocking SMTP connectivity test.

    Sequence: connect → EHLO → (STARTTLS/re-EHLO if applicable) → login → quit.
    Password is used only for authentication and is never logged.
    """
    start = time.monotonic()
    conn: smtplib.SMTP | None = None
    try:
        if security == SmtpSecurity.SSL_TLS:
            conn = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            conn = smtplib.SMTP(host, port, timeout=timeout)

        conn.ehlo()

        if security == SmtpSecurity.STARTTLS:
            conn.starttls()
            conn.ehlo()  # re-greet after TLS upgrade

        conn.login(username, password)
        conn.quit()

        latency = (time.monotonic() - start) * 1000
        log.info(
            "connectivity.smtp_ok",
            host=host,
            port=port,
            security=security,
            latency_ms=round(latency, 1),
        )
        return ConnectivityResult(success=True, latency_ms=round(latency, 1))

    except Exception as exc:
        error_msg = _safe_smtp_error(exc)
        log.warning(
            "connectivity.smtp_failed",
            host=host,
            port=port,
            security=security,
            exc_type=type(exc).__name__,
        )
        return ConnectivityResult(success=False, error_message=error_msg)

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — cleanup failure is expected and harmless
                pass


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def test_imap_connection(
    host: str,
    port: int,
    security: ImapSecurity,
    username: str,
    password: str,
    timeout: int = 10,
) -> ConnectivityResult:
    """Async wrapper: test upstream IMAP connectivity in a thread pool.

    Returns a ConnectivityResult — never raises. Safe to call from a
    FastAPI route handler without blocking the event loop.
    """
    return await asyncio.to_thread(
        _test_imap_sync, host, port, security, username, password, timeout
    )


async def test_smtp_connection(
    host: str,
    port: int,
    security: SmtpSecurity,
    username: str,
    password: str,
    timeout: int = 10,
) -> ConnectivityResult:
    """Async wrapper: test upstream SMTP connectivity in a thread pool.

    Returns a ConnectivityResult — never raises. Safe to call from a
    FastAPI route handler without blocking the event loop.
    """
    return await asyncio.to_thread(
        _test_smtp_sync, host, port, security, username, password, timeout
    )
