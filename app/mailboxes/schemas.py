"""Pydantic schemas for the mailboxes domain.

These are used for form input validation, response serialisation,
and internal service boundaries. Route handlers extract raw Form()
fields and pass them to these schemas for validation before calling
the service layer.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.mailboxes.models import ImapSecurity, SmtpSecurity


class MailboxFormData(BaseModel):
    """Validated form data for creating or updating a MailboxProfile.

    Both the create and edit forms use this schema.
    For updates, leaving imap_password or smtp_password blank means
    "keep the existing encrypted password".
    """

    display_name: str = Field(..., min_length=1, max_length=255)
    email_address: str = Field(..., min_length=5, max_length=255)

    # IMAP upstream
    imap_host: str = Field(..., min_length=1, max_length=255)
    imap_port: int = Field(..., ge=1, le=65535)
    imap_username: str = Field(..., min_length=1, max_length=255)
    # Plaintext — encrypted before any persistence. Empty string on edit = "keep existing".
    imap_password: str = Field(default="", max_length=1000)
    imap_security: ImapSecurity

    # SMTP upstream
    smtp_host: str = Field(..., min_length=1, max_length=255)
    smtp_port: int = Field(..., ge=1, le=65535)
    smtp_username: str = Field(..., min_length=1, max_length=255)
    # Plaintext — encrypted before any persistence. Empty string on edit = "keep existing".
    smtp_password: str = Field(default="", max_length=1000)
    smtp_security: SmtpSecurity

    @field_validator("email_address")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Enter a valid email address.")
        return v.lower().strip()

    @field_validator("imap_host", "smtp_host")
    @classmethod
    def strip_host(cls, v: str) -> str:
        return v.strip().lower()


class ConnectivityStatus(BaseModel):
    """Structured result of an upstream IMAP or SMTP connectivity test."""

    protocol: str  # "imap" or "smtp"
    success: bool
    error_message: str | None = None
    latency_ms: float | None = None


class MailboxListItem(BaseModel):
    """Minimal mailbox info for list views."""

    id: int
    display_name: str
    email_address: str
    status: str
    proxy_username: str | None
    imap_host: str
    smtp_host: str

    model_config = {"from_attributes": True}


class MailboxDetail(BaseModel):
    """Full mailbox info for the detail page.

    Sensitive fields (encrypted passwords) are excluded.
    The proxy_password_hash is also excluded — the plaintext is only shown
    once via a session flash immediately after creation or password reset.
    """

    id: int
    owner_id: int
    display_name: str
    email_address: str
    imap_host: str
    imap_port: int
    imap_username: str
    imap_security: ImapSecurity
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_security: SmtpSecurity
    status: str
    proxy_username: str | None
    last_connection_error: str | None

    model_config = {"from_attributes": True}
