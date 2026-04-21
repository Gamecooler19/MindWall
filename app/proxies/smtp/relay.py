"""Upstream SMTP relay client for Mindwall SMTP proxy.

Connects to the registered upstream SMTP server using the stored (encrypted)
credentials and relays a submitted message.

Design principles:
  - Upstream credentials are decrypted only at relay time, not at AUTH time.
  - All three upstream security modes are supported: SSL_TLS, STARTTLS, NONE.
  - Relay errors are caught and returned as safe strings; no credentials or
    raw tracebacks are propagated to the client or logs.
  - Timeouts are enforced to prevent relay from blocking the proxy indefinitely.
  - This module is mockable in tests: relay_message() is a standalone async
    function with clear inputs and outputs.
"""

from __future__ import annotations

import smtplib

import structlog

from app.mailboxes.models import MailboxProfile, SmtpSecurity
from app.security.crypto import CredentialEncryptor

log = structlog.get_logger(__name__)


class SmtpRelayError(Exception):
    """Raised when upstream relay fails.  Message is always safe to surface."""


def _safe_smtp_error(exc: Exception) -> str:
    """Convert an SMTP or network exception to a safe, non-secret error string."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Upstream SMTP authentication failed — check stored credentials."
    if isinstance(exc, smtplib.SMTPConnectError):
        return "Could not connect to upstream SMTP server."
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "Upstream SMTP server disconnected unexpectedly."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "Upstream SMTP server refused one or more recipients."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "Upstream SMTP server refused the sender address."
    if isinstance(exc, TimeoutError):
        return "Connection to upstream SMTP server timed out."
    if isinstance(exc, OSError):
        return f"Network error reaching upstream SMTP server: {type(exc).__name__}"
    return f"Upstream SMTP relay error: {type(exc).__name__}"


async def relay_message(
    profile: MailboxProfile,
    encryptor: CredentialEncryptor,
    envelope_from: str,
    envelope_to: list[str],
    raw_message: bytes,
    timeout_seconds: int = 30,
) -> None:
    """Relay a submitted message via the upstream SMTP server.

    Decrypts stored upstream credentials, connects to the upstream server
    using the configured security mode, authenticates, and sends the message.

    Raises:
        SmtpRelayError: With a safe message if relay fails for any reason.
    """
    # Decrypt upstream password only at relay time.
    try:
        upstream_password = encryptor.decrypt(profile.smtp_password_enc)
    except Exception as exc:
        log.error(
            "smtp_relay.credential_decrypt_failed",
            mailbox_id=profile.id,
            error=type(exc).__name__,
        )
        raise SmtpRelayError("Failed to decrypt upstream SMTP credentials.") from exc

    host = profile.smtp_host
    port = profile.smtp_port
    username = profile.smtp_username

    log.info(
        "smtp_relay.attempting",
        mailbox_id=profile.id,
        host=host,
        port=port,
        security=profile.smtp_security,
        recipients=len(envelope_to),
    )

    try:
        if profile.smtp_security == SmtpSecurity.SSL_TLS:
            smtp_cls = smtplib.SMTP_SSL
        else:
            smtp_cls = smtplib.SMTP

        with smtp_cls(host, port, timeout=timeout_seconds) as smtp:
            if profile.smtp_security == SmtpSecurity.STARTTLS:
                smtp.starttls()
            smtp.login(username, upstream_password)
            refused = smtp.sendmail(envelope_from, envelope_to, raw_message)
            if refused:
                log.warning(
                    "smtp_relay.some_recipients_refused",
                    mailbox_id=profile.id,
                    refused_count=len(refused),
                )
    except smtplib.SMTPException as exc:
        safe_msg = _safe_smtp_error(exc)
        log.warning(
            "smtp_relay.failed",
            mailbox_id=profile.id,
            error=type(exc).__name__,
            safe_message=safe_msg,
        )
        raise SmtpRelayError(safe_msg) from exc
    except OSError as exc:
        safe_msg = _safe_smtp_error(exc)
        log.warning(
            "smtp_relay.network_error",
            mailbox_id=profile.id,
            error=type(exc).__name__,
            safe_message=safe_msg,
        )
        raise SmtpRelayError(safe_msg) from exc

    log.info(
        "smtp_relay.success",
        mailbox_id=profile.id,
        recipients=len(envelope_to),
    )
