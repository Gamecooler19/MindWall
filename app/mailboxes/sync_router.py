"""Admin routes for mailbox sync and virtual inbox inspection.

All routes require admin or analyst role.

Routes:
  GET  /admin/mailboxes/{mailbox_id}/sync          — sync status + trigger form
  POST /admin/mailboxes/{mailbox_id}/sync          — trigger sync
  GET  /admin/mailboxes/{mailbox_id}/inbox         — Mindwall-visible inbox
  GET  /admin/mailboxes/{mailbox_id}/quarantine    — quarantined messages
  GET  /admin/mailboxes/{mailbox_id}/items/{item_id} — single item detail

Ownership / access:
  Admins can view any mailbox.
  Analysts can view any mailbox (read-only — sync trigger is admin-only).

Design: route handlers are thin; all business logic is in the service layer.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserContext
from app.config import get_settings
from app.dependencies import get_db, get_templates, require_admin, require_analyst
from app.mailboxes import sync_service, view_service
from app.mailboxes.models import MailboxProfile
from app.mailboxes.sync_models import ItemVisibility
from app.messages.storage import RawMessageStore
from app.security.crypto import CredentialEncryptor

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/mailboxes", tags=["admin", "sync"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_mailbox_for_admin(
    db: AsyncSession,
    mailbox_id: int,
) -> MailboxProfile:
    """Load a MailboxProfile by primary key regardless of owner.

    Admins and analysts can inspect any mailbox.
    Raises 404 if not found.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(MailboxProfile).where(MailboxProfile.id == mailbox_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found.")
    return profile


# ---------------------------------------------------------------------------
# Sync status + trigger
# ---------------------------------------------------------------------------


@router.get(
    "/{mailbox_id}/sync",
    response_class=HTMLResponse,
    summary="Mailbox sync status",
)
async def mailbox_sync_status(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates=Depends(get_templates),
):
    """Show current sync state for all folders of a mailbox."""
    profile = await _get_mailbox_for_admin(db, mailbox_id)
    sync_states = await sync_service.get_sync_states_for_mailbox(db, mailbox_id)
    counts = await view_service.get_mailbox_item_counts(db, mailbox_id)

    return templates.TemplateResponse(
        request,
        "admin/mailboxes/sync_status.html",
        {
            "profile": profile,
            "sync_states": sync_states,
            "counts": counts,
            "current_user": current_user,
            "visibility_labels": {
                ItemVisibility.VISIBLE: "Visible",
                ItemVisibility.QUARANTINED: "Quarantined",
                ItemVisibility.HIDDEN: "Hidden",
                ItemVisibility.PENDING: "Pending",
                ItemVisibility.INGESTION_ERROR: "Error",
            },
        },
    )


@router.post(
    "/{mailbox_id}/sync",
    response_class=RedirectResponse,
    summary="Trigger mailbox sync",
)
async def trigger_mailbox_sync(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Trigger an IMAP sync for the default folder of a mailbox."""
    settings = get_settings()
    profile = await _get_mailbox_for_admin(db, mailbox_id)

    encryptor = CredentialEncryptor(settings.encryption_key)
    store = RawMessageStore(settings.raw_message_store_path)

    try:
        result = await sync_service.sync_mailbox_folder(
            db=db,
            profile=profile,
            folder_name=settings.imap_sync_default_folder,
            encryptor=encryptor,
            store=store,
            batch_size=settings.imap_sync_batch_size,
            imap_timeout=settings.imap_sync_timeout_seconds,
            llm_enabled=settings.llm_enabled,
            quarantine_soft_hold=settings.quarantine_soft_hold,
            actor_user_id=current_user.user_id,
        )
        log.info(
            "sync.triggered_by_admin",
            mailbox_id=mailbox_id,
            admin_user_id=current_user.user_id,
            new_messages=result.new_messages,
            errors=result.errors,
        )
    except Exception as exc:
        log.error(
            "sync.trigger_error",
            mailbox_id=mailbox_id,
            exc_type=type(exc).__name__,
        )

    return RedirectResponse(
        url=f"/admin/mailboxes/{mailbox_id}/sync",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Virtual inbox
# ---------------------------------------------------------------------------


@router.get(
    "/{mailbox_id}/inbox",
    response_class=HTMLResponse,
    summary="Mindwall virtual inbox for a mailbox",
)
async def mailbox_inbox(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates=Depends(get_templates),
):
    """Show the Mindwall-filtered inbox for a mailbox profile."""
    profile = await _get_mailbox_for_admin(db, mailbox_id)
    settings = get_settings()
    items = await view_service.get_visible_inbox(
        db,
        mailbox_profile_id=mailbox_id,
        folder_name=settings.imap_sync_default_folder,
    )

    return templates.TemplateResponse(
        request,
        "admin/mailboxes/inbox.html",
        {
            "profile": profile,
            "items": items,
            "current_user": current_user,
            "view": "inbox",
        },
    )


@router.get(
    "/{mailbox_id}/quarantine",
    response_class=HTMLResponse,
    summary="Quarantine view for a mailbox",
)
async def mailbox_quarantine_view(
    request: Request,
    mailbox_id: int,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates=Depends(get_templates),
):
    """Show the quarantined and held messages for a mailbox profile."""
    profile = await _get_mailbox_for_admin(db, mailbox_id)
    settings = get_settings()
    items = await view_service.get_quarantine_inbox(
        db,
        mailbox_profile_id=mailbox_id,
        folder_name=settings.imap_sync_default_folder,
    )

    return templates.TemplateResponse(
        request,
        "admin/mailboxes/inbox.html",
        {
            "profile": profile,
            "items": items,
            "current_user": current_user,
            "view": "quarantine",
        },
    )


# ---------------------------------------------------------------------------
# Item detail
# ---------------------------------------------------------------------------


@router.get(
    "/{mailbox_id}/items/{item_id}",
    response_class=HTMLResponse,
    summary="Mailbox item detail",
)
async def mailbox_item_detail(
    request: Request,
    mailbox_id: int,
    item_id: int,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates=Depends(get_templates),
):
    """Show full detail for a single synced mailbox item."""
    profile = await _get_mailbox_for_admin(db, mailbox_id)
    virtual_item = await view_service.get_item_with_message(db, item_id)

    if virtual_item is None or virtual_item.mailbox_item.mailbox_profile_id != mailbox_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found.")

    import json

    to_list: list[str] = []
    if virtual_item.message and virtual_item.message.to_addresses:
        try:
            to_list = json.loads(virtual_item.message.to_addresses)
        except (json.JSONDecodeError, TypeError):
            to_list = [virtual_item.message.to_addresses]

    return templates.TemplateResponse(
        request,
        "admin/mailboxes/item_detail.html",
        {
            "profile": profile,
            "virtual_item": virtual_item,
            "to_list": to_list,
            "current_user": current_user,
        },
    )
