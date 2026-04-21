"""Admin UI routes.

All admin routes require the 'admin' role.
This module contains route handlers only — business logic lives in services.

Phase 1 provides a placeholder dashboard that will be expanded in later phases
with quarantine review, policy editing, mailbox management, and audit logs.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserContext
from app.dependencies import get_db, get_templates, require_admin

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Admin dashboard — shows quarantine queue depth and system status."""
    from app.quarantine import service as quarantine_service

    pending_count = await quarantine_service.count_pending_review(db)

    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {"user": current_user, "pending_quarantine_count": pending_count},
    )
