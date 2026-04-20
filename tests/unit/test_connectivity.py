"""Unit tests for app.mailboxes.connectivity.

All network I/O is mocked — no real mail servers are contacted.
Tests target the synchronous helper functions directly so that:
  1. We avoid asyncio.to_thread overhead in unit tests.
  2. We can inspect imaplib / smtplib mock call sequences precisely.
"""

import imaplib
import smtplib
import ssl
from unittest.mock import MagicMock, patch

from app.mailboxes.connectivity import (
    _safe_imap_error,
    _safe_smtp_error,
    _test_imap_sync,
    _test_smtp_sync,
)
from app.mailboxes.models import ImapSecurity, SmtpSecurity

# ---------------------------------------------------------------------------
# Safe error mappers
# ---------------------------------------------------------------------------


class TestSafeImapError:
    def test_auth_error(self):
        exc = imaplib.IMAP4.error(b"AUTHENTICATIONFAILED")
        msg = _safe_imap_error(exc)
        assert "authentication" in msg.lower()
        assert "password" in msg.lower()

    def test_timeout_error(self):
        msg = _safe_imap_error(TimeoutError())
        assert "timed out" in msg.lower()

    def test_connection_refused(self):
        msg = _safe_imap_error(ConnectionRefusedError())
        assert "refused" in msg.lower()

    def test_ssl_error(self):
        # ssl.SSLError can only be raised after ssl is imported
        try:
            raise ssl.SSLError("ssl: certificate error")
        except ssl.SSLError as exc:
            msg = _safe_imap_error(exc)
        assert "tls" in msg.lower() or "ssl" in msg.lower()

    def test_os_error(self):
        msg = _safe_imap_error(OSError("network unreachable"))
        assert "network" in msg.lower()

    def test_unknown_error_gives_generic_message(self):
        msg = _safe_imap_error(RuntimeError("something weird"))
        assert "IMAP" in msg
        assert "failed" in msg.lower()


class TestSafeSmtpError:
    def test_auth_error(self):
        exc = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
        msg = _safe_smtp_error(exc)
        assert "authentication" in msg.lower()

    def test_connect_error(self):
        exc = smtplib.SMTPConnectError(421, b"Service unavailable")
        msg = _safe_smtp_error(exc)
        assert "connection" in msg.lower()

    def test_timeout_error(self):
        msg = _safe_smtp_error(TimeoutError())
        assert "timed out" in msg.lower()

    def test_connection_refused(self):
        msg = _safe_smtp_error(ConnectionRefusedError())
        assert "refused" in msg.lower()

    def test_general_smtp_exception(self):
        exc = smtplib.SMTPException("some smtp error")
        msg = _safe_smtp_error(exc)
        assert "SMTP" in msg

    def test_unknown_error_gives_generic_message(self):
        msg = _safe_smtp_error(RuntimeError("something weird"))
        assert "SMTP" in msg and "failed" in msg.lower()


# ---------------------------------------------------------------------------
# IMAP sync tester
# ---------------------------------------------------------------------------


class TestImapSyncTester:
    def _make_mock_imap(self):
        m = MagicMock()
        m.login.return_value = ("OK", [b"Logged in"])
        m.logout.return_value = ("BYE", [b"Logging out"])
        return m

    def test_ssl_tls_success(self):
        mock_conn = self._make_mock_imap()
        with patch("app.mailboxes.connectivity.imaplib.IMAP4_SSL", return_value=mock_conn):
            result = _test_imap_sync(
                "imap.example.com", 993, ImapSecurity.SSL_TLS, "user", "pass", 5
            )
        assert result.success is True
        assert result.error_message is None
        assert result.latency_ms is not None
        mock_conn.login.assert_called_once_with("user", "pass")
        mock_conn.logout.assert_called_once()

    def test_starttls_success(self):
        mock_conn = self._make_mock_imap()
        with patch("app.mailboxes.connectivity.imaplib.IMAP4", return_value=mock_conn):
            result = _test_imap_sync(
                "imap.example.com", 143, ImapSecurity.STARTTLS, "user", "pass", 5
            )
        assert result.success is True
        mock_conn.starttls.assert_called_once()
        mock_conn.login.assert_called_once_with("user", "pass")

    def test_none_security_success(self):
        mock_conn = self._make_mock_imap()
        with patch("app.mailboxes.connectivity.imaplib.IMAP4", return_value=mock_conn):
            result = _test_imap_sync(
                "imap.example.com", 143, ImapSecurity.NONE, "user", "pass", 5
            )
        assert result.success is True
        mock_conn.starttls.assert_not_called()

    def test_auth_failure_returns_error_result(self):
        mock_conn = self._make_mock_imap()
        mock_conn.login.side_effect = imaplib.IMAP4.error(b"AUTHENTICATIONFAILED")
        with patch("app.mailboxes.connectivity.imaplib.IMAP4_SSL", return_value=mock_conn):
            result = _test_imap_sync(
                "imap.example.com", 993, ImapSecurity.SSL_TLS, "user", "badpass", 5
            )
        assert result.success is False
        assert result.error_message is not None
        assert "authentication" in result.error_message.lower()

    def test_connection_refused_returns_error_result(self):
        with patch(
            "app.mailboxes.connectivity.imaplib.IMAP4_SSL",
            side_effect=ConnectionRefusedError(),
        ):
            result = _test_imap_sync(
                "imap.example.com", 993, ImapSecurity.SSL_TLS, "user", "pass", 5
            )
        assert result.success is False
        assert "refused" in result.error_message.lower()

    def test_timeout_returns_error_result(self):
        with patch(
            "app.mailboxes.connectivity.imaplib.IMAP4_SSL",
            side_effect=TimeoutError(),
        ):
            result = _test_imap_sync(
                "imap.example.com", 993, ImapSecurity.SSL_TLS, "user", "pass", 5
            )
        assert result.success is False
        assert "timed out" in result.error_message.lower()

    def test_never_exposes_password_in_error(self):
        secret = "my-super-secret-password"
        mock_conn = self._make_mock_imap()
        mock_conn.login.side_effect = imaplib.IMAP4.error(f"AUTHFAILED {secret}".encode())
        with patch("app.mailboxes.connectivity.imaplib.IMAP4_SSL", return_value=mock_conn):
            result = _test_imap_sync(
                "imap.example.com", 993, ImapSecurity.SSL_TLS, "user", secret, 5
            )
        # The password must not appear in the user-facing error message
        assert secret not in (result.error_message or "")


# ---------------------------------------------------------------------------
# SMTP sync tester
# ---------------------------------------------------------------------------


class TestSmtpSyncTester:
    def _make_mock_smtp(self):
        m = MagicMock()
        m.ehlo.return_value = (250, b"OK")
        m.login.return_value = (235, b"Authentication successful")
        m.quit.return_value = (221, b"Bye")
        return m

    def test_starttls_success(self):
        mock_conn = self._make_mock_smtp()
        with patch("app.mailboxes.connectivity.smtplib.SMTP", return_value=mock_conn):
            result = _test_smtp_sync(
                "smtp.example.com", 587, SmtpSecurity.STARTTLS, "user", "pass", 5
            )
        assert result.success is True
        mock_conn.ehlo.assert_called()
        mock_conn.starttls.assert_called_once()
        mock_conn.login.assert_called_once_with("user", "pass")
        mock_conn.quit.assert_called_once()

    def test_ssl_tls_success(self):
        mock_conn = self._make_mock_smtp()
        with patch("app.mailboxes.connectivity.smtplib.SMTP_SSL", return_value=mock_conn):
            result = _test_smtp_sync(
                "smtp.example.com", 465, SmtpSecurity.SSL_TLS, "user", "pass", 5
            )
        assert result.success is True
        mock_conn.starttls.assert_not_called()

    def test_none_security_success(self):
        mock_conn = self._make_mock_smtp()
        with patch("app.mailboxes.connectivity.smtplib.SMTP", return_value=mock_conn):
            result = _test_smtp_sync(
                "smtp.example.com", 25, SmtpSecurity.NONE, "user", "pass", 5
            )
        assert result.success is True
        mock_conn.starttls.assert_not_called()

    def test_auth_failure_returns_error_result(self):
        mock_conn = self._make_mock_smtp()
        mock_conn.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        with patch("app.mailboxes.connectivity.smtplib.SMTP", return_value=mock_conn):
            result = _test_smtp_sync(
                "smtp.example.com", 587, SmtpSecurity.STARTTLS, "user", "badpass", 5
            )
        assert result.success is False
        assert "authentication" in result.error_message.lower()

    def test_connection_refused_returns_error_result(self):
        with patch(
            "app.mailboxes.connectivity.smtplib.SMTP",
            side_effect=ConnectionRefusedError(),
        ):
            result = _test_smtp_sync(
                "smtp.example.com", 587, SmtpSecurity.STARTTLS, "user", "pass", 5
            )
        assert result.success is False
        assert "refused" in result.error_message.lower()

    def test_timeout_returns_error_result(self):
        with patch(
            "app.mailboxes.connectivity.smtplib.SMTP",
            side_effect=TimeoutError(),
        ):
            result = _test_smtp_sync(
                "smtp.example.com", 587, SmtpSecurity.STARTTLS, "user", "pass", 5
            )
        assert result.success is False
        assert "timed out" in result.error_message.lower()

    def test_never_exposes_password_in_error(self):
        secret = "my-super-secret-password"
        mock_conn = self._make_mock_smtp()
        mock_conn.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        with patch("app.mailboxes.connectivity.smtplib.SMTP", return_value=mock_conn):
            result = _test_smtp_sync(
                "smtp.example.com", 587, SmtpSecurity.STARTTLS, "user", secret, 5
            )
        assert secret not in (result.error_message or "")
