"""SMTP submission proxy worker — standalone entrypoint.

Starts the Mindwall SMTP submission proxy server on the configured host/port.
This process is separate from the FastAPI web application.

Usage:
    python workers/smtp_proxy.py

Configuration comes from environment variables / .env file via app.config.
Key variables:
    SMTP_PROXY_HOST          — bind address (default: 0.0.0.0)
    SMTP_PROXY_PORT          — TCP port (default: 1587)
    SMTP_DELIVERY_MODE       — "capture" (default) or "relay"
    OUTBOUND_MESSAGE_STORE_PATH — root dir for captured .eml files
    DATABASE_URL             — async PostgreSQL connection string
    ENCRYPTION_KEY           — Fernet key for credential decryption
    SECRET_KEY               — required by Settings even for the proxy

The worker performs a graceful shutdown on SIGTERM and SIGINT.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from app.config import get_settings
from app.db.session import close_db, init_db
from app.logging_config import setup_logging
from app.proxies.smtp.server import SmtpServer

log = structlog.get_logger(__name__)


async def main() -> None:
    """Set up the database connection pool and run the SMTP submission proxy."""
    settings = get_settings()
    setup_logging(settings)

    log.info(
        "smtp_proxy_worker.starting",
        host=settings.smtp_proxy_host,
        port=settings.smtp_proxy_port,
        delivery_mode=settings.smtp_delivery_mode,
    )

    init_db(settings.database_url)

    from app.db.session import (
        _async_session_factory as session_factory,  # type: ignore[attr-defined]
    )

    if session_factory is None:
        log.error("smtp_proxy_worker.db_init_failed")
        sys.exit(1)

    # Ensure the outbound store root exists.
    store_root = settings.outbound_message_store_path
    store_root.mkdir(parents=True, exist_ok=True)

    server = SmtpServer(
        host=settings.smtp_proxy_host,
        port=settings.smtp_proxy_port,
        session_factory=session_factory,
        store_root=store_root,
        delivery_mode=settings.smtp_delivery_mode,
        max_message_bytes=settings.smtp_max_message_bytes,
        relay_timeout=settings.smtp_relay_timeout_seconds,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(sig: int) -> None:
        log.info("smtp_proxy_worker.signal_received", signal=sig)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows does not support add_signal_handler.
            pass

    await server.start()

    log.info(
        "smtp_proxy_worker.ready",
        host=settings.smtp_proxy_host,
        port=settings.smtp_proxy_port,
    )

    await stop_event.wait()

    log.info("smtp_proxy_worker.stopping")
    await server.stop()
    await close_db()
    log.info("smtp_proxy_worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
