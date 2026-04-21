"""Admin-only Message Lab routes.

The Message Lab is an internal tool that lets administrators upload raw .eml
files to test the ingestion pipeline before the IMAP/SMTP proxies exist.

All routes require the 'admin' role and are only registered when the
MESSAGE_LAB_ENABLED setting is True (the default).

Routes:
  GET  /admin/messages/          — list all ingested messages
  GET  /admin/messages/upload    — upload form
  POST /admin/messages/upload    — ingest an uploaded .eml file
  GET  /admin/messages/{id}      — detail view (metadata, URLs, attachments, analysis)
  POST /admin/messages/{id}/analyze — trigger analysis for a message
"""

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import service as analysis_service
from app.analysis.ollama_client import OllamaClient
from app.auth.schemas import UserContext
from app.config import get_settings
from app.dependencies import get_db, get_templates, require_admin
from app.messages import service
from app.messages.models import IngestionSource
from app.messages.storage import get_raw_message_store
from app.policies.constants import DIMENSION_LABELS
from app.policies.verdict import VerdictThresholds

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/messages", tags=["admin", "messages"])


@router.get("/", response_class=HTMLResponse)
async def list_messages(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Message Lab — list all ingested messages, newest first."""
    messages = await service.list_messages(db)
    return templates.TemplateResponse(
        request,
        "admin/messages/list.html",
        {"user": current_user, "messages": messages},
    )


@router.get("/upload", response_class=HTMLResponse)
async def upload_form(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the .eml file upload form."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "admin/messages/upload.html",
        {
            "user": current_user,
            "max_upload_mb": settings.message_lab_max_upload_mb,
            "errors": [],
        },
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_message(
    request: Request,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    eml_file: Annotated[UploadFile, File(...)],
) -> HTMLResponse:
    """Ingest an uploaded .eml file and redirect to its detail page."""
    settings = get_settings()
    max_bytes = settings.message_lab_max_upload_mb * 1024 * 1024

    # Read with size guard — read one byte past the limit to detect oversize files.
    raw_bytes = await eml_file.read(max_bytes + 1)

    if len(raw_bytes) > max_bytes:
        return templates.TemplateResponse(
            request,
            "admin/messages/upload.html",
            {
                "user": current_user,
                "max_upload_mb": settings.message_lab_max_upload_mb,
                "errors": [
                    f"File exceeds the maximum allowed size "
                    f"of {settings.message_lab_max_upload_mb} MB."
                ],
            },
        )

    if not raw_bytes:
        return templates.TemplateResponse(
            request,
            "admin/messages/upload.html",
            {
                "user": current_user,
                "max_upload_mb": settings.message_lab_max_upload_mb,
                "errors": ["The uploaded file is empty. Please select a valid .eml file."],
            },
        )

    store = get_raw_message_store(settings)

    try:
        message = await service.ingest_raw_message(
            db=db,
            raw_bytes=raw_bytes,
            source=IngestionSource.MESSAGE_LAB,
            store=store,
            mailbox_profile_id=None,
        )
    except Exception as exc:
        log.exception("messages.lab_ingest_failed", error=str(exc))
        return templates.TemplateResponse(
            request,
            "admin/messages/upload.html",
            {
                "user": current_user,
                "max_upload_mb": settings.message_lab_max_upload_mb,
                "errors": ["Failed to ingest the message. See server logs for details."],
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    log.info(
        "messages.lab_upload_complete",
        admin_user_id=current_user.user_id,
        message_db_id=message.id,
        sha256=message.raw_sha256,
    )

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/admin/messages/{message.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{message_id}", response_class=HTMLResponse)
async def message_detail(
    request: Request,
    message_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Message Lab — detailed view of a single ingested message."""
    msg = await service.get_message_by_id(db, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found."
        )

    to_list: list[str] = json.loads(msg.to_addresses) if msg.to_addresses else []
    cc_list: list[str] = json.loads(msg.cc_addresses) if msg.cc_addresses else []

    # Load the latest analysis run (if any)
    analysis = await analysis_service.get_latest_analysis(db, message_id)

    # Build a sorted dimension score list for the template
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

    settings = get_settings()

    return templates.TemplateResponse(
        request,
        "admin/messages/detail.html",
        {
            "user": current_user,
            "msg": msg,
            "to_list": to_list,
            "cc_list": cc_list,
            "analysis": analysis,
            "dim_scores": dim_scores,
            "analysis_enabled": settings.analysis_enabled,
        },
    )


@router.post("/{message_id}/analyze", response_class=HTMLResponse)
async def analyze_message(
    request: Request,
    message_id: int,
    current_user: Annotated[UserContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    """Trigger analysis for an ingested message and redirect to its detail page."""
    msg = await service.get_message_by_id(db, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found."
        )

    settings = get_settings()

    ollama_client: OllamaClient | None = None
    if settings.llm_enabled:
        try:
            ollama_client = OllamaClient(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout=settings.ollama_timeout_seconds,
            )
        except ValueError:
            log.warning(
                "analysis.ollama_client_init_failed",
                base_url=settings.ollama_base_url,
            )

    thresholds = VerdictThresholds(
        allow=settings.verdict_threshold_allow,
        allow_with_banner=settings.verdict_threshold_allow_with_banner,
        soft_hold=settings.verdict_threshold_soft_hold,
        quarantine=settings.verdict_threshold_quarantine,
    )

    await analysis_service.run_analysis(
        db=db,
        msg=msg,
        ollama_client=ollama_client,
        llm_enabled=settings.llm_enabled,
        thresholds=thresholds,
        gateway_mode=settings.gateway_mode,
    )

    log.info(
        "messages.lab_analysis_triggered",
        admin_user_id=current_user.user_id,
        message_id=message_id,
    )

    return RedirectResponse(  # type: ignore[return-value]
        url=f"/admin/messages/{message_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
