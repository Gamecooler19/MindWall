"""Mindwall application factory.

Entry point: `uvicorn app.main:app`

The create_app() factory is used both for production startup and for test
fixture overrides. The module-level `app` instance is what uvicorn loads.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import dependencies
from app.config import Settings, get_settings
from app.db.session import close_db, init_db
from app.logging_config import setup_logging

log = structlog.get_logger(__name__)

# Absolute path to the app/ package directory
_APP_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------


def _register_routers(app: FastAPI, templates: Jinja2Templates) -> None:
    """Attach all domain routers and the home route to the application."""
    from app.admin.router import router as admin_router
    from app.auth.router import router as auth_router
    from app.health.router import router as health_router
    from app.mailboxes.router import router as mailboxes_router

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(mailboxes_router)

    # Home route — server-rendered landing/dashboard page
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request) -> HTMLResponse:
        user = None
        if request.session.get("user_id"):
            user = {
                "id": request.session["user_id"],
                "email": request.session.get("user_email", ""),
                "role": request.session.get("user_role", "user"),
            }
        return templates.TemplateResponse(
            request,
            "index.html",
            {"user": user},
        )


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _register_exception_handlers(app: FastAPI, templates: Jinja2Templates) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> HTMLResponse | JSONResponse:
        # Return HTML error pages for browser requests; JSON for API clients.
        accept = request.headers.get("accept", "")
        if "text/html" in accept and exc.status_code == 404:
            return templates.TemplateResponse(
                request,
                "errors/404.html",
                {},
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return JSONResponse(
            content={"detail": exc.detail},
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            content={"detail": "An internal error occurred."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the Mindwall FastAPI application.

    Args:
        settings: Optional settings override, primarily for testing.

    Returns:
        A fully configured FastAPI application instance.
    """
    if settings is None:
        settings = get_settings()

    setup_logging(settings)

    # ------------------------------------------------------------------
    # Lifespan — startup and shutdown hooks
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        log.info(
            "mindwall.starting",
            app_name=settings.app_name,
            debug=settings.debug,
            gateway_mode=settings.gateway_mode,
        )

        # Initialise the database connection pool
        init_db(settings.database_url)
        log.info("mindwall.db_pool_ready")

        # Ensure blob storage directory exists
        settings.blob_storage_path.mkdir(parents=True, exist_ok=True)

        yield  # Application is running

        log.info("mindwall.shutting_down")
        await close_db()
        log.info("mindwall.shutdown_complete")

    # ------------------------------------------------------------------
    # FastAPI instance
    # ------------------------------------------------------------------
    app = FastAPI(
        title=settings.app_name,
        description=(
            "Privacy-first, self-hosted email security platform. "
            "All inference runs locally — no data leaves the deployment boundary."
        ),
        version="0.1.0",
        # Disable public OpenAPI docs in production
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
        openapi_url="/api/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # Session middleware
    # Signed session cookie — secret_key prevents tampering.
    # https_only=True in production to prevent cookie theft over HTTP.
    # ------------------------------------------------------------------
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=settings.session_max_age,
        session_cookie="mw_session",
        https_only=not settings.debug,
        same_site="lax",
    )

    # ------------------------------------------------------------------
    # Static files
    # ------------------------------------------------------------------
    static_dir = _APP_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------
    templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))
    dependencies.set_templates(templates)

    # ------------------------------------------------------------------
    # Routes and error handlers
    # ------------------------------------------------------------------
    _register_routers(app, templates)
    _register_exception_handlers(app, templates)

    return app


# Module-level application instance used by uvicorn.
app = create_app()
