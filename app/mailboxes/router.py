"""Mailbox domain routes.

All routes require authentication. Ownership is enforced in service calls
— a user can only read/modify their own mailbox profiles.

Route map:
  GET  /mailboxes/                    — list user's mailboxes
  GET  /mailboxes/new                 — registration form
  POST /mailboxes/                    — create mailbox profile
  GET  /mailboxes/{id}                — detail + proxy setup instructions
  GET  /mailboxes/{id}/edit           — edit form
  POST /mailboxes/{id}/edit           — update mailbox profile
  POST /mailboxes/{id}/test           — test upstream connectivity
  POST /mailboxes/{id}/reset-password — reset proxy password
  POST /mailboxes/{id}/delete         — permanently delete profile
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserContext
from app.config import get_settings
from app.dependencies import get_current_user, get_db, get_session_user, get_templates
from app.mailboxes import service
from app.mailboxes.models import ImapSecurity, SmtpSecurity
from app.mailboxes.schemas import MailboxFormData
from app.security.crypto import get_encryptor

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])

# Proxy password session key — {mailbox_id} will be substituted.
_PROXY_PW_SESSION_KEY = "proxy_pw_reveal_{}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proxy_pw_key(mailbox_id: int) -> str:
    return _PROXY_PW_SESSION_KEY.format(mailbox_id)


def _form_to_schema(
    display_name: str,
    email_address: str,
    imap_host: str,
    imap_port: str,
    imap_username: str,
    imap_password: str,
    imap_security: str,
    smtp_host: str,
    smtp_port: str,
    smtp_username: str,
    smtp_password: str,
    smtp_security: str,
) -> tuple[MailboxFormData | None, list[str]]:
    """Parse and validate raw form strings into a MailboxFormData.

    Returns (schema, []) on success or (None, [error_messages]) on failure.
    """
    errors: list[str] = []

    try:
        imap_port_int = int(imap_port)
    except (ValueError, TypeError):
        errors.append("IMAP port must be a number.")
        imap_port_int = 0

    try:
        smtp_port_int = int(smtp_port)
    except (ValueError, TypeError):
        errors.append("SMTP port must be a number.")
        smtp_port_int = 0

    if errors:
        return None, errors

    try:
        form = MailboxFormData(
            display_name=display_name,
            email_address=email_address,
            imap_host=imap_host,
            imap_port=imap_port_int,
            imap_username=imap_username,
            imap_password=imap_password,
            imap_security=ImapSecurity(imap_security),
            smtp_host=smtp_host,
            smtp_port=smtp_port_int,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_security=SmtpSecurity(smtp_security),
        )
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            errors = [
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            ]
        else:
            errors = [str(exc)]
        return None, errors

    return form, []


# ---------------------------------------------------------------------------
# Mailbox list
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def list_mailboxes(
    request: Request,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Show all mailbox profiles for the authenticated user."""
    profiles = await service.list_mailboxes_for_user(db, current_user.user_id)
    return templates.TemplateResponse(
        request,
        "mailboxes/list.html",
        {
            "user": get_session_user(request),
            "profiles": profiles,
        },
    )


# ---------------------------------------------------------------------------
# Create mailbox
# ---------------------------------------------------------------------------


@router.get("/new", response_class=HTMLResponse)
async def new_mailbox_form(
    request: Request,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the mailbox registration form."""
    return templates.TemplateResponse(
        request,
        "mailboxes/form.html",
        {
            "user": get_session_user(request),
            "form_action": "/mailboxes/",
            "title": "Register Mailbox",
            "is_edit": False,
            "prefill": {},
            "errors": [],
        },
    )


@router.post("/", response_class=HTMLResponse)
async def create_mailbox(
    request: Request,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    display_name: Annotated[str, Form()],
    email_address: Annotated[str, Form()],
    imap_host: Annotated[str, Form()],
    imap_port: Annotated[str, Form()],
    imap_username: Annotated[str, Form()],
    smtp_host: Annotated[str, Form()],
    smtp_port: Annotated[str, Form()],
    smtp_username: Annotated[str, Form()],
    imap_password: Annotated[str, Form()] = "",
    imap_security: Annotated[str, Form()] = "ssl_tls",
    smtp_password: Annotated[str, Form()] = "",
    smtp_security: Annotated[str, Form()] = "starttls",
) -> HTMLResponse:
    """Process the mailbox registration form."""
    form_data, errors = _form_to_schema(
        display_name=display_name,
        email_address=email_address,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_username=imap_username,
        imap_password=imap_password,
        imap_security=imap_security,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_security=smtp_security,
    )

    if errors or form_data is None:
        return templates.TemplateResponse(
            request,
            "mailboxes/form.html",
            {
                "user": get_session_user(request),
                "form_action": "/mailboxes/",
                "title": "Register Mailbox",
                "is_edit": False,
                "prefill": {
                    "display_name": display_name,
                    "email_address": email_address,
                    "imap_host": imap_host,
                    "imap_port": imap_port,
                    "imap_username": imap_username,
                    "imap_security": imap_security,
                    "smtp_host": smtp_host,
                    "smtp_port": smtp_port,
                    "smtp_username": smtp_username,
                    "smtp_security": smtp_security,
                },
                "errors": errors,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        profile, proxy_password_plain = await service.create_mailbox(
            db=db,
            owner_id=current_user.user_id,
            form=form_data,
            encryptor=get_encryptor(),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "mailboxes/form.html",
            {
                "user": get_session_user(request),
                "form_action": "/mailboxes/",
                "title": "Register Mailbox",
                "is_edit": False,
                "prefill": {},
                "errors": [str(exc)],
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # Store the proxy password in the session for one-time display on the detail page.
    request.session[_proxy_pw_key(profile.id)] = proxy_password_plain

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/mailboxes/{profile.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Mailbox detail
# ---------------------------------------------------------------------------


@router.get("/{mailbox_id}", response_class=HTMLResponse)
async def mailbox_detail(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Show mailbox details and proxy setup instructions.

    If a one-time proxy password is in the session (just created or reset),
    it is displayed once and then removed from the session.
    """
    profile = await service.get_mailbox_by_id(db, mailbox_id, current_user.user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")

    # Consume the one-time proxy password from the session if present.
    pw_key = _proxy_pw_key(mailbox_id)
    proxy_password_reveal = request.session.pop(pw_key, None)

    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "mailboxes/detail.html",
        {
            "user": get_session_user(request),
            "profile": profile,
            "proxy_password_reveal": proxy_password_reveal,
            "proxy_imap_host": settings.imap_proxy_display_host,
            "proxy_imap_port": settings.imap_proxy_port,
            "proxy_smtp_host": settings.smtp_proxy_display_host,
            "proxy_smtp_port": settings.smtp_proxy_port,
        },
    )


# ---------------------------------------------------------------------------
# Edit mailbox
# ---------------------------------------------------------------------------


@router.get("/{mailbox_id}/edit", response_class=HTMLResponse)
async def edit_mailbox_form(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the mailbox edit form pre-filled with existing settings."""
    profile = await service.get_mailbox_by_id(db, mailbox_id, current_user.user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")

    return templates.TemplateResponse(
        request,
        "mailboxes/form.html",
        {
            "user": get_session_user(request),
            "form_action": f"/mailboxes/{mailbox_id}/edit",
            "title": f"Edit — {profile.display_name}",
            "is_edit": True,
            "profile": profile,
            "prefill": {
                "display_name": profile.display_name,
                "email_address": profile.email_address,
                "imap_host": profile.imap_host,
                "imap_port": profile.imap_port,
                "imap_username": profile.imap_username,
                "imap_security": profile.imap_security.value,
                "smtp_host": profile.smtp_host,
                "smtp_port": profile.smtp_port,
                "smtp_username": profile.smtp_username,
                "smtp_security": profile.smtp_security.value,
            },
            "errors": [],
        },
    )


@router.post("/{mailbox_id}/edit", response_class=HTMLResponse)
async def update_mailbox(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    display_name: Annotated[str, Form()],
    email_address: Annotated[str, Form()],
    imap_host: Annotated[str, Form()],
    imap_port: Annotated[str, Form()],
    imap_username: Annotated[str, Form()],
    smtp_host: Annotated[str, Form()],
    smtp_port: Annotated[str, Form()],
    smtp_username: Annotated[str, Form()],
    imap_password: Annotated[str, Form()] = "",
    imap_security: Annotated[str, Form()] = "ssl_tls",
    smtp_password: Annotated[str, Form()] = "",
    smtp_security: Annotated[str, Form()] = "starttls",
) -> HTMLResponse:
    """Process the mailbox edit form."""
    profile = await service.get_mailbox_by_id(db, mailbox_id, current_user.user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")

    form_data, errors = _form_to_schema(
        display_name=display_name,
        email_address=email_address,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_username=imap_username,
        imap_password=imap_password,
        imap_security=imap_security,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_security=smtp_security,
    )

    if errors or form_data is None:
        return templates.TemplateResponse(
            request,
            "mailboxes/form.html",
            {
                "user": get_session_user(request),
                "form_action": f"/mailboxes/{mailbox_id}/edit",
                "title": f"Edit — {profile.display_name}",
                "is_edit": True,
                "profile": profile,
                "prefill": {
                    "display_name": display_name,
                    "email_address": email_address,
                    "imap_host": imap_host,
                    "imap_port": imap_port,
                    "imap_username": imap_username,
                    "imap_security": imap_security,
                    "smtp_host": smtp_host,
                    "smtp_port": smtp_port,
                    "smtp_username": smtp_username,
                    "smtp_security": smtp_security,
                },
                "errors": errors,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    await service.update_mailbox(
        db=db,
        profile=profile,
        form=form_data,
        encryptor=get_encryptor(),
    )

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/mailboxes/{mailbox_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Connectivity test
# ---------------------------------------------------------------------------


@router.post("/{mailbox_id}/test", response_class=HTMLResponse)
async def test_connectivity(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Run upstream IMAP and SMTP connectivity checks and persist the result."""
    profile = await service.get_mailbox_by_id(db, mailbox_id, current_user.user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")

    settings = get_settings()
    await service.test_mailbox_connectivity(
        db=db,
        profile=profile,
        encryptor=get_encryptor(),
        timeout=settings.connection_timeout_seconds,
    )

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/mailboxes/{mailbox_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Reset proxy password
# ---------------------------------------------------------------------------


@router.post("/{mailbox_id}/reset-password", response_class=HTMLResponse)
async def reset_proxy_password(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Generate a new proxy password. The new plaintext is shown once on the detail page."""
    profile = await service.get_mailbox_by_id(db, mailbox_id, current_user.user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")

    proxy_password_plain = await service.reset_proxy_password(db=db, profile=profile)
    request.session[_proxy_pw_key(mailbox_id)] = proxy_password_plain

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/mailboxes/{mailbox_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Delete mailbox
# ---------------------------------------------------------------------------


@router.post("/{mailbox_id}/delete", response_class=HTMLResponse)
async def delete_mailbox(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Permanently delete a mailbox profile and all associated data."""
    profile = await service.get_mailbox_by_id(db, mailbox_id, current_user.user_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")

    await service.delete_mailbox(db=db, profile=profile)

    return RedirectResponse(  # type: ignore[return-value]
        url="/mailboxes/",
        status_code=status.HTTP_303_SEE_OTHER,
    )
