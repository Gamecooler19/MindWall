"""SMTP proxy authentication and session management.

Authenticates incoming SMTP submission connections using Mindwall proxy
credentials.  The logic is identical to the IMAP proxy authentication;
both call the shared authenticate_proxy_credentials() function from the
IMAP session module, so there is a single bcrypt verification path.

Security principles:
  - Same constant-time dummy verify used for username-not-found to prevent
    username enumeration.
  - Upstream SMTP credentials are never touched at authentication time.
  - The SmtpProxySession carries only the profile ID and owner context;
    decrypted upstream credentials are fetched separately, only when relay
    mode is used.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

# Re-use the shared credential verification logic from the IMAP session layer.
from app.proxies.imap.session import (
    ProxySession,
    authenticate_proxy_credentials,
)


@dataclass
class SmtpProxySession:
    """Authenticated SMTP proxy session context.

    A thin wrapper around ProxySession that makes the SMTP layer explicit
    and allows SMTP-specific fields to be added later without touching the
    shared ProxySession dataclass.
    """

    mailbox_profile_id: int
    owner_user_id: int
    proxy_username: str
    email_address: str


async def authenticate_smtp_credentials(
    db: AsyncSession,
    proxy_username: str,
    proxy_password: str,
) -> SmtpProxySession | None:
    """Verify Mindwall proxy credentials for SMTP submission.

    Delegates to the shared authenticate_proxy_credentials() and converts
    the result to an SmtpProxySession.  Returns None on any failure.

    Args:
        db:              Active async session (read-only in this call).
        proxy_username:  Username from the SMTP AUTH command.
        proxy_password:  Plaintext password from the SMTP AUTH command.

    Returns:
        SmtpProxySession on success, None on any failure.
    """
    proxy_session: ProxySession | None = await authenticate_proxy_credentials(
        db, proxy_username, proxy_password
    )
    if proxy_session is None:
        return None
    return SmtpProxySession(
        mailbox_profile_id=proxy_session.mailbox_profile_id,
        owner_user_id=proxy_session.owner_user_id,
        proxy_username=proxy_session.proxy_username,
        email_address=proxy_session.email_address,
    )
