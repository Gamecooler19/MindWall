"""Shared pytest fixtures for the Mindwall test suite.

Environment setup:
  Test environment variables are set at module import time, before any app
  code runs. This ensures get_settings() caches test values rather than
  reading from a local .env file.

  A valid Fernet key is required even in tests — the all-zeros key used here
  is cryptographically weak but structurally correct for testing purposes.

Database:
  Tests use an in-memory SQLite database via aiosqlite.
  SQLite lacks PostgreSQL-specific features (e.g., native ENUM types), but
  SQLAlchemy transparently maps SAEnum to VARCHAR for SQLite, which is
  sufficient for unit and integration tests.
  Full PostgreSQL integration tests should be run in CI against a real instance.
"""

import os

import app.analysis.models
import app.mailboxes.models
import app.mailboxes.sync_models
import app.messages.models
import app.quarantine.models
import app.users.models
import pytest
import pytest_asyncio
from app.db.base import Base
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Test environment — set BEFORE any app imports that call get_settings()
# ---------------------------------------------------------------------------

# A valid 44-char Fernet key (32 zero-bytes, URL-safe base64-encoded).
# Weak on purpose — never use this value outside of tests.
_TEST_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_TEST_SECRET_KEY = "mindwall-test-secret-key-do-not-use-in-prod"

os.environ.setdefault("SECRET_KEY", _TEST_SECRET_KEY)
os.environ.setdefault("ENCRYPTION_KEY", _TEST_FERNET_KEY)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("DEBUG", "true")

# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_settings():
    """Return a Settings instance configured for the test environment."""
    # Clear the lru_cache so test env vars are picked up.
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()


# ---------------------------------------------------------------------------
# In-memory database fixtures
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Create an in-memory SQLite engine and initialise the schema once per session."""
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Provide a transactional database session that rolls back after each test."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Application and HTTP client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app(test_settings, db_engine):
    """Create the FastAPI application wired to the test settings.

    The database dependency is overridden to use the in-memory SQLite engine
    so tests never touch a real PostgreSQL instance.
    """
    from app.db import session as db_session_module
    from app.dependencies import get_db
    from app.main import create_app

    # Point the module-level engine to our test engine so health checks pass.
    db_session_module._engine = db_engine
    db_session_module._async_session_factory = async_sessionmaker(
        db_engine, expire_on_commit=False, class_=AsyncSession
    )

    application = create_app(settings=test_settings)

    # Override get_db to return sessions from the test engine.
    async def override_get_db():
        factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture()
def client(app):
    """Synchronous TestClient for testing HTTP endpoints without a running server.

    Function-scoped so that each test starts with a fresh HTTP client:
    - no session cookies carried over from previous tests
    - the app lifespan (init_db / close_db) runs cleanly per test
    - ordering-dependent failures from shared auth state are eliminated

    The session-scoped ``app`` is reused, so the dependency overrides and the
    test database engine remain stable across the whole suite.
    """
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
