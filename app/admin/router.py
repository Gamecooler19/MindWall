"""Admin UI routes.

All admin routes require the 'admin' role.
This module contains route handlers only — business logic lives in services.

Phase 1 provides a placeholder dashboard that will be expanded in later phases
with quarantine review, policy editing, mailbox management, and audit logs.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth.schemas import UserContext
from app.dependencies import get_templates, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Admin dashboard placeholder.

    In later phases this will surface quarantine queue depth, model health,
    recent alerts, and system-wide configuration status.
    """
    return templates.TemplateResponse(
        "admin/index.html",
        {"request": request, "user": current_user},
    )
