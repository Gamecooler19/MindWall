"""Structured logging configuration for Mindwall.

Produces JSON logs in production and human-readable console output in debug mode.
All log records include a timestamp, level, logger name, and any bound context.
Secrets must never appear in log records — callers are responsible for redaction.
"""

import logging
import sys

import structlog

from app.config import Settings


def setup_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib root logger.

    Call this once at application startup before any log messages are emitted.
    """
    log_level = logging.DEBUG if settings.debug else logging.INFO

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if settings.debug:
        # Human-readable output for local development
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # Machine-readable JSON for production / log aggregators
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Align the stdlib root logger so libraries that use logging.getLogger()
    # also respect the configured level.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Suppress noisy third-party output unless debug mode is active.
    if not settings.debug:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("asyncpg").setLevel(logging.WARNING)
