"""Admin quarantine inbox and review workflow routes.

All routes require at minimum the 'analyst' role (admins included).

Routes:
  GET  /admin/quarantine/           — quarantine inbox (filterable by status)
  GET  /admin/quarantine/{item_id}  — detail view with analysis + audit timeline
  POST /admin/quarantine/{item_id}/action — apply a review action
"""

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import service as analysis_service
from app.auth.schemas import UserContext
from app.dependencies import get_db, get_templates, require_analyst
from app.messages import service as message_service
from app.policies.constants import DIMENSION_LABELS
from app.quarantine import service as quarantine_service
from app.quarantine.models import QuarantineAction, QuarantineStatus
from app.quarantine.service import InvalidTransitionError

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/quarantine", tags=["admin", "quarantine"])

# Mapping from QuarantineStatus values to display labels + badge colours
_STATUS_META: dict[str, dict] = {
    QuarantineStatus.PENDING_REVIEW: {
        "label": "Pending Review",
        "badge": "bg-yellow-900 text-yellow-300 border-yellow-700",
    },
    QuarantineStatus.IN_REVIEW: {
        "label": "In Review",
        "badge": "bg-blue-900 text-blue-300 border-blue-700",
    },
    QuarantineStatus.RELEASED: {
        "label": "Released",
        "badge": "bg-green-900 text-green-300 border-green-700",
    },
    QuarantineStatus.FALSE_POSITIVE: {
        "label": "False Positive",
        "badge": "bg-teal-900 text-teal-300 border-teal-700",
    },
    QuarantineStatus.CONFIRMED_MALICIOUS: {
        "label": "Confirmed Malicious",
        "badge": "bg-red-900 text-red-300 border-red-700",
    },
    QuarantineStatus.DELETED: {
        "label": "Deleted",
        "badge": "bg-gray-800 text-gray-500 border-gray-700",
    },
}

_ACTION_META: dict[str, dict] = {
    QuarantineAction.MARK_IN_REVIEW: {
        "label": "Mark In Review",
        "style": "bg-blue-700 hover:bg-blue-600 text-white",
    },
    QuarantineAction.RELEASE: {
        "label": "Release",
        "style": "bg-green-700 hover:bg-green-600 text-white",
    },
    QuarantineAction.MARK_FALSE_POSITIVE: {
        "label": "Mark False Positive",
        "style": "bg-teal-700 hover:bg-teal-600 text-white",
    },
    QuarantineAction.CONFIRM_MALICIOUS: {
        "label": "Confirm Malicious",
        "style": "bg-red-700 hover:bg-red-600 text-white",
    },
    QuarantineAction.DELETE: {
        "label": "Delete",
        "style": "bg-gray-700 hover:bg-gray-600 text-white",
    },
}

# Which actions are available from each status (for the detail UI)
_ACTIONS_BY_STATUS: dict[str, list[str]] = {
    QuarantineStatus.PENDING_REVIEW: [
        QuarantineAction.MARK_IN_REVIEW,
        QuarantineAction.RELEASE,
        QuarantineAction.MARK_FALSE_POSITIVE,
        QuarantineAction.CONFIRM_MALICIOUS,
        QuarantineAction.DELETE,
    ],
    QuarantineStatus.IN_REVIEW: [
        QuarantineAction.RELEASE,
        QuarantineAction.MARK_FALSE_POSITIVE,
        QuarantineAction.CONFIRM_MALICIOUS,
        QuarantineAction.DELETE,
    ],
    QuarantineStatus.RELEASED: [QuarantineAction.DELETE],
    QuarantineStatus.FALSE_POSITIVE: [QuarantineAction.DELETE],
    QuarantineStatus.CONFIRMED_MALICIOUS: [QuarantineAction.DELETE],
    QuarantineStatus.DELETED: [],
}


@router.get("/", response_class=HTMLResponse)
async def quarantine_inbox(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    status_filter: str | None = None,
) -> HTMLResponse:
    """Quarantine inbox — list all items, optionally filtered by status."""
    # Validate status filter if provided
    valid_statuses = {s.value for s in QuarantineStatus}
    if status_filter and status_filter not in valid_statuses:
        status_filter = None

    items = await quarantine_service.list_quarantine_items(db, status_filter=status_filter)

    # Attach message subject/from for display without a separate join
    enriched: list[dict] = []
    for item in items:
        msg = await message_service.get_message_by_id(db, item.message_id)
        enriched.append(
            {
                "item": item,
                "subject": msg.subject if msg else "(message deleted)",
                "from_address": msg.from_address if msg else None,
                "status_meta": _STATUS_META.get(item.status, {}),
            }
        )

    pending_count = await quarantine_service.count_pending_review(db)

    return templates.TemplateResponse(
        request,
        "admin/quarantine/inbox.html",
        {
            "user": current_user,
            "items": enriched,
            "status_filter": status_filter,
            "status_values": [s.value for s in QuarantineStatus],
            "status_meta": _STATUS_META,
            "pending_count": pending_count,
        },
    )


@router.get("/{item_id}", response_class=HTMLResponse)
async def quarantine_detail(
    request: Request,
    item_id: int,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Quarantine detail — full review view with analysis + audit history."""
    item = await quarantine_service.get_quarantine_item_by_id(db, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quarantine item not found.",
        )

    msg = await message_service.get_message_by_id(db, item.message_id)
    analysis = await analysis_service.get_latest_analysis(db, item.message_id)

    to_list: list[str] = json.loads(msg.to_addresses) if msg and msg.to_addresses else []

    dim_scores: list[dict] = []
    if analysis and analysis.dimension_scores:
        sorted_scores = sorted(
            analysis.dimension_scores, key=lambda d: d.score, reverse=True
        )
        dim_scores = [
            {
                "dimension": ds.dimension,
                "label": DIMENSION_LABELS.get(ds.dimension, ds.dimension),
                "score": ds.score,
                "source": ds.source,
            }
            for ds in sorted_scores
        ]

    available_actions = [
        {"action": a, **_ACTION_META.get(a, {"label": a, "style": ""})}
        for a in _ACTIONS_BY_STATUS.get(item.status, [])
    ]

    return templates.TemplateResponse(
        request,
        "admin/quarantine/detail.html",
        {
            "user": current_user,
            "item": item,
            "msg": msg,
            "analysis": analysis,
            "to_list": to_list,
            "dim_scores": dim_scores,
            "status_meta": _STATUS_META.get(item.status, {}),
            "available_actions": available_actions,
            "action_meta": _ACTION_META,
        },
    )


@router.post("/{item_id}/action", response_class=HTMLResponse)
async def quarantine_action(
    request: Request,
    item_id: int,
    current_user: Annotated[UserContext, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    action: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Apply a review action (release, false-positive, etc.) to a quarantine item."""
    item = await quarantine_service.get_quarantine_item_by_id(db, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quarantine item not found.",
        )

    # Validate action value
    valid_actions = {a.value for a in QuarantineAction}
    if action not in valid_actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {action!r}.",
        )

    # Sanitise note — limit length to prevent abuse
    clean_note = (note or "").strip()[:1000] or None

    try:
        await quarantine_service.apply_action(
            db,
            item=item,
            action=action,
            actor_user_id=current_user.user_id,
            note=clean_note,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    log.info(
        "quarantine.route_action",
        item_id=item_id,
        action=action,
        actor=current_user.user_id,
    )

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/admin/quarantine/{item_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
