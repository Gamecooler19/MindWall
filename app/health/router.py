"""Health check endpoints.

/health/live  — liveness probe (process is running)
/health/ready — readiness probe (dependencies are reachable)

These are designed to be used by container orchestrators and load balancers.
No authentication is required so they can be polled without credentials.
"""

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

import redis.asyncio as aioredis

from app.config import get_settings
from app.db.session import get_engine

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health/live", include_in_schema=True)
async def liveness() -> dict:
    """Liveness probe.

    Returns 200 as long as the Python process is running and the event loop
    is not blocked. Suitable for use as a Kubernetes liveness probe.
    """
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=True)
async def readiness() -> JSONResponse:
    """Readiness probe.

    Checks that the database and Redis are reachable.
    Returns 200 when all checks pass, 503 when any check fails.
    Suitable for use as a Kubernetes readiness probe.
    """
    settings = get_settings()
    checks: dict[str, str] = {}
    healthy = True

    # ------------------------------------------------------------------
    # Database check
    # ------------------------------------------------------------------
    engine = get_engine()
    if engine is None:
        checks["database"] = "not_initialised"
        healthy = False
    else:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            log.error("health.db_check_failed", error=str(exc))
            checks["database"] = "error"
            healthy = False

    # ------------------------------------------------------------------
    # Redis check
    # ------------------------------------------------------------------
    try:
        client: aioredis.Redis = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        log.error("health.redis_check_failed", error=str(exc))
        checks["redis"] = "error"
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        content={
            "status": "ready" if healthy else "degraded",
            "checks": checks,
        },
        status_code=status_code,
    )
