"""IMAP proxy worker — standalone entrypoint.

Starts the Mindwall read-only IMAP proxy server on the configured host/port.
This process is separate from the FastAPI web application.

Usage:
    python workers/imap_proxy.py

Configuration comes from environment variables / .env file via app.config.
Key variables:
    IMAP_PROXY_HOST  — bind address (default: 0.0.0.0)
    IMAP_PROXY_PORT  — TCP port (default: 1993; set 1143 for Docker dev)
    DATABASE_URL     — async PostgreSQL connection string
    ENCRYPTION_KEY   — Fernet key for credential decryption
    SECRET_KEY       — required by Settings even for the proxy

The worker performs a graceful shutdown on SIGTERM and SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

# Ensure the project root is on the path when run directly.
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from app.config import get_settings
from app.db.session import close_db, init_db
from app.logging_config import setup_logging
from app.proxies.imap.server import ImapServer

log = structlog.get_logger(__name__)


async def main() -> None:
    """Set up the database connection pool and run the IMAP proxy server."""
    settings = get_settings()
    setup_logging(settings)

    log.info(
        "imap_proxy_worker.starting",
        host=settings.imap_proxy_host,
        port=settings.imap_proxy_port,
    )

    init_db(settings.database_url)

    # The session_factory is module-level in app.db.session after init_db()
    from app.db.session import (
        _async_session_factory as session_factory,  # type: ignore[attr-defined]
    )

    if session_factory is None:
        log.error("imap_proxy_worker.db_init_failed")
        sys.exit(1)

    server = ImapServer(
        host=settings.imap_proxy_host,
        port=settings.imap_proxy_port,
        session_factory=session_factory,
        raw_store_root=settings.raw_message_store_path,
    )

    # Graceful shutdown handler.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(sig: int) -> None:
        log.info("imap_proxy_worker.signal_received", signal=sig)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows does not support add_signal_handler for all signals.
            pass

    await server.start()
    log.info(
        "imap_proxy_worker.ready",
        host=settings.imap_proxy_host,
        port=settings.imap_proxy_port,
    )

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await server.stop()
        await close_db()
        log.info("imap_proxy_worker.stopped")


if __name__ == "__main__":
    # Suppress noisy library warnings.
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
