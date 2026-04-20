"""Async SQLAlchemy engine and session management.

Usage:
    Call init_db(database_url) once at application startup.
    Use get_db_session() as a FastAPI dependency to obtain a per-request session.
    Call close_db() at application shutdown to release the connection pool.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    """Create the async engine and session factory.

    SQLite (used in tests) does not support pool_size/max_overflow.
    These parameters are omitted when an SQLite URL is detected.
    """
    global _engine, _async_session_factory

    is_sqlite = database_url.startswith("sqlite")

    engine_kwargs: dict = {
        "echo": False,
        "pool_pre_ping": True,
    }
    if not is_sqlite:
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    _engine = create_async_engine(database_url, **engine_kwargs)
    _async_session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


def get_engine() -> AsyncEngine | None:
    """Return the current async engine, or None if not yet initialised."""
    return _engine


async def close_db() -> None:
    """Dispose the connection pool. Call during application shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a managed async database session.

    Commits on success and rolls back on any unhandled exception.
    Raises RuntimeError if init_db() has not been called.
    """
    if _async_session_factory is None:
        raise RuntimeError(
            "Database session factory is not initialised. "
            "Ensure init_db() is called during application startup."
        )

    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
