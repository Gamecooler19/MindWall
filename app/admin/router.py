"""Admin UI routes.

All admin routes require the 'admin' role.
This module contains route handlers only — business logic lives in services.

Routes:
  GET  /admin/                          — dashboard
  GET  /admin/audit/                    — audit log viewer
  GET  /admin/health/model              — Ollama / model health page
  GET  /admin/policy/                   — policy editor (read)
  POST /admin/policy/                   — policy editor (save single setting)
  GET  /admin/alerts/                   — alerts & incidents list
  GET  /admin/alerts/{id}               — alert detail
  POST /admin/alerts/{id}/acknowledge   — acknowledge alert
  POST /admin/alerts/{id}/resolve       — resolve alert
  GET  /admin/mailboxes/                — mailbox profiles overview
  GET  /admin/outbound/                 — outbound SMTP submissions list
  GET  /admin/outbound/{id}             — outbound submission detail
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserContext
from app.dependencies import get_db, get_templates, require_admin

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Admin dashboard — shows live queue depths and system status."""
    from app.alerts import service as alerts_service
    from app.mailboxes import service as mailbox_service
    from app.quarantine import service as quarantine_service

    pending_count = await quarantine_service.count_pending_review(db)
    open_alerts_count = await alerts_service.count_open_alerts(db)
    mailbox_count = await mailbox_service.count_mailboxes(db)

    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {
            "user": current_user,
            "pending_quarantine_count": pending_count,
            "open_alerts_count": open_alerts_count,
            "mailbox_count": mailbox_count,
        },
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/audit/", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    page: int = 1,
) -> HTMLResponse:
    """Paginated audit log viewer."""
    from sqlalchemy import func, select

    from app.quarantine.models import AuditEvent

    page_size = 50
    offset = (page - 1) * page_size

    total_result = await db.execute(select(func.count()).select_from(AuditEvent))
    total = total_result.scalar_one()

    events_result = await db.execute(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(page_size).offset(offset)
    )
    events = list(events_result.scalars().all())

    total_pages = max(1, (total + page_size - 1) // page_size)

    return templates.TemplateResponse(
        request,
        "admin/audit_log.html",
        {
            "user": current_user,
            "events": events,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        },
    )


# ---------------------------------------------------------------------------
# Model health
# ---------------------------------------------------------------------------


@router.get("/health/model", response_class=HTMLResponse)
async def model_health(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Ollama inference health check and analysis pipeline statistics."""
    import httpx
    from sqlalchemy import func, select

    from app.analysis.models import AnalysisRun, AnalysisStatus
    from app.config import get_settings

    cfg = get_settings()

    # Check Ollama connectivity (quick HTTP check, not a full generate call)
    ollama_ok = False
    ollama_error: str | None = None
    ollama_models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{cfg.ollama_base_url}/api/tags")
        if resp.status_code == 200:
            ollama_ok = True
            data = resp.json()
            ollama_models = [m.get("name", "") for m in data.get("models", [])]
        else:
            ollama_error = f"HTTP {resp.status_code}"
    except httpx.ConnectError:
        ollama_error = f"Cannot connect to {cfg.ollama_base_url}"
    except httpx.TimeoutException:
        ollama_error = "Connection timed out (5 s)"
    except Exception as exc:
        ollama_error = str(exc)

    # Analysis run statistics
    total_runs = (
        await db.execute(select(func.count()).select_from(AnalysisRun))
    ).scalar_one()
    degraded_runs = (
        await db.execute(
            select(func.count()).where(AnalysisRun.is_degraded == True)  # noqa: E712
        )
    ).scalar_one()
    complete_runs = (
        await db.execute(
            select(func.count()).where(AnalysisRun.status == AnalysisStatus.COMPLETE)
        )
    ).scalar_one()

    # Average latency proxy: count recent runs
    recent_result = await db.execute(
        select(AnalysisRun).order_by(AnalysisRun.id.desc()).limit(10)
    )
    recent_runs = list(recent_result.scalars().all())

    return templates.TemplateResponse(
        request,
        "admin/model_health.html",
        {
            "user": current_user,
            "ollama_ok": ollama_ok,
            "ollama_error": ollama_error,
            "ollama_base_url": cfg.ollama_base_url,
            "ollama_model": cfg.ollama_model,
            "ollama_models": ollama_models,
            "llm_enabled": cfg.llm_enabled,
            "analysis_enabled": cfg.analysis_enabled,
            "total_runs": total_runs,
            "degraded_runs": degraded_runs,
            "complete_runs": complete_runs,
            "recent_runs": recent_runs,
        },
    )


# ---------------------------------------------------------------------------
# Policy editor
# ---------------------------------------------------------------------------


@router.get("/policy/", response_class=HTMLResponse)
async def policy_editor(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    saved: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Policy editor — show all editable settings with current effective values."""
    from app.policies import service as policy_service

    effective = await policy_service.get_effective_policy(db)

    return templates.TemplateResponse(
        request,
        "admin/policy_editor.html",
        {
            "user": current_user,
            "effective": effective,
            "editable": policy_service.EDITABLE_SETTINGS,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/policy/", response_class=HTMLResponse)
async def policy_editor_save(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    key: Annotated[str, Form()],
    value: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Save a single policy setting and redirect back to the editor."""
    from app.policies import service as policy_service

    try:
        await policy_service.save_setting(
            db,
            key=key,
            value=value.strip(),
            actor_user_id=current_user.user_id,
            note=note or None,
        )
    except ValueError as exc:
        log.warning("policy.save_failed", key=key, error=str(exc))
        return RedirectResponse(
            url=f"/admin/policy/?error={str(exc)[:120]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/policy/?saved={key}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Alerts & Incidents
# ---------------------------------------------------------------------------


@router.get("/alerts/", response_class=HTMLResponse)
async def alerts_list(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    filter_status: str | None = None,
) -> HTMLResponse:
    """List all alerts with optional status filter."""
    from app.alerts import service as alerts_service
    from app.alerts.models import AlertStatus

    status_filter = None
    if filter_status and filter_status in AlertStatus.__members__:
        status_filter = AlertStatus[filter_status]

    alerts = await alerts_service.list_alerts(db, status=status_filter, limit=200)
    open_count = await alerts_service.count_open_alerts(db)

    return templates.TemplateResponse(
        request,
        "admin/alerts.html",
        {
            "user": current_user,
            "alerts": alerts,
            "open_count": open_count,
            "filter_status": filter_status,
        },
    )


@router.get("/alerts/{alert_id}", response_class=HTMLResponse)
async def alert_detail(
    request: Request,
    alert_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Alert detail page."""
    from app.alerts import service as alerts_service

    alert = await alerts_service.get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    return templates.TemplateResponse(
        request,
        "admin/alert_detail.html",
        {"user": current_user, "alert": alert},
    )


@router.post("/alerts/{alert_id}/acknowledge", response_class=HTMLResponse)
async def alert_acknowledge(
    request: Request,
    alert_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    note: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Acknowledge an open alert."""
    from app.alerts import service as alerts_service

    try:
        await alerts_service.acknowledge_alert(
            db, alert_id, actor_user_id=current_user.user_id, note=note or None
        )
    except ValueError as exc:
        log.warning("alert.acknowledge_failed", alert_id=alert_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(
        url=f"/admin/alerts/{alert_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/alerts/{alert_id}/resolve", response_class=HTMLResponse)
async def alert_resolve(
    request: Request,
    alert_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    note: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Resolve an alert."""
    from app.alerts import service as alerts_service

    try:
        await alerts_service.resolve_alert(
            db, alert_id, actor_user_id=current_user.user_id, note=note or None
        )
    except ValueError as exc:
        log.warning("alert.resolve_failed", alert_id=alert_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(
        url=f"/admin/alerts/{alert_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Mailbox profiles overview
# ---------------------------------------------------------------------------


@router.get("/mailboxes/", response_class=HTMLResponse)
async def admin_mailboxes(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Admin overview of all registered mailbox profiles."""
    from app.mailboxes import service as mailbox_service

    profiles = await mailbox_service.list_all_mailboxes(db)

    return templates.TemplateResponse(
        request,
        "admin/mailboxes.html",
        {"user": current_user, "profiles": profiles},
    )


# ---------------------------------------------------------------------------
# Outbound SMTP submissions
# ---------------------------------------------------------------------------


@router.get("/outbound/", response_class=HTMLResponse)
async def outbound_list(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    page: int = 1,
) -> HTMLResponse:
    """List recent outbound SMTP submissions (newest first, paginated)."""
    from sqlalchemy import func, select

    from app.proxies.smtp.models import OutboundMessage

    page_size = 50
    offset = (page - 1) * page_size

    total = (
        await db.execute(select(func.count()).select_from(OutboundMessage))
    ).scalar_one()
    result = await db.execute(
        select(OutboundMessage)
        .order_by(OutboundMessage.submitted_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    messages = list(result.scalars().all())
    total_pages = max(1, (total + page_size - 1) // page_size)

    return templates.TemplateResponse(
        request,
        "admin/outbound_list.html",
        {
            "user": current_user,
            "messages": messages,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        },
    )


@router.get("/outbound/{outbound_id}", response_class=HTMLResponse)
async def outbound_detail(
    request: Request,
    outbound_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Detail view for a single outbound SMTP submission."""
    import json

    from sqlalchemy import select

    from app.proxies.smtp.models import OutboundMessage

    result = await db.execute(
        select(OutboundMessage).where(OutboundMessage.id == outbound_id)
    )
    msg = result.scalar_one_or_none()
    if msg is None:
        raise HTTPException(status_code=404, detail="Outbound message not found")

    recipients = json.loads(msg.envelope_to_json)

    return templates.TemplateResponse(
        request,
        "admin/outbound_detail.html",
        {
            "user": current_user,
            "msg": msg,
            "recipients": recipients,
        },
    )
