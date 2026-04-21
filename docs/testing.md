# Testing

Mindwall has a comprehensive test suite covering unit behavior, integration flows, and end-to-end proxy verification.

---

## Test suite structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Pure unit tests (no I/O, in-memory SQLite)
│   ├── test_analysis_*.py   # Analysis service, deterministic checks, prompt, Ollama client
│   ├── test_auth_*.py       # Auth service, hashing
│   ├── test_crypto.py       # Fernet encryption utilities
│   ├── test_imap_*.py       # IMAP proxy session, mailbox adapter, server
│   ├── test_mailbox_*.py    # Mailbox service, sync service, view service
│   ├── test_message_*.py    # Parser, sanitizer, URL extractor, storage
│   ├── test_policy_*.py     # Verdict engine, policy settings
│   ├── test_quarantine_*.py # Quarantine service, state transitions
│   ├── test_smtp_*.py       # SMTP proxy session, server, delivery
│   └── test_verdict.py      # Verdict threshold logic
└── integration/             # HTTP + database integration tests
    ├── test_admin_routes.py # Admin UI routes
    ├── test_auth_routes.py  # Login/logout flow
    ├── test_health.py       # /health/live and /health/ready
    ├── test_imap_proxy.py   # IMAP proxy integration
    ├── test_mailbox_routes.py # Mailbox registration/edit routes
    ├── test_quarantine_routes.py # Quarantine review routes
    └── test_smtp_proxy.py   # SMTP proxy integration
```

---

## Running tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/unit/test_verdict.py -v

# Run a specific test
pytest tests/unit/test_verdict.py::test_verdict_thresholds -v

# Run with coverage report
pytest --cov=app --cov-report=term-missing

# Run only unit tests
pytest tests/unit/

# Run only integration tests
pytest tests/integration/
```

---

## Test configuration

Tests use the `asyncio_mode = "auto"` setting from `pyproject.toml`, so all async tests run automatically without the `@pytest.mark.asyncio` decorator.

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
log_cli = true
log_cli_level = "WARNING"
```

---

## Test fixtures (conftest.py)

The main fixtures are defined in `tests/conftest.py`:

### `async_engine`

Creates an in-memory SQLite engine using `aiosqlite`. All schema is created via `Base.metadata.create_all()`. No PostgreSQL required.

### `db_session`

Provides an `AsyncSession` connected to the in-memory SQLite engine. Rolls back after each test.

### `client`

A `TestClient` (or `AsyncClient`) configured with the FastAPI test application. Uses dependency overrides to inject the in-memory database session.

### `admin_user` / `regular_user`

Pre-created test user fixtures with known credentials and roles.

---

## Database isolation

All tests use an in-memory SQLite database. Tests are isolated:
- Each test gets a fresh session
- Transactions are rolled back after each test
- No PostgreSQL or Redis instance required

Integration tests that check `/health/ready` gracefully handle the absence of Redis — they assert the correct response shape rather than requiring connectivity.

---

## Test coverage targets

High-priority coverage areas:

| Area | Status |
|------|--------|
| Credential encryption/decryption | ✅ Covered |
| Mailbox registration validation | ✅ Covered |
| LLM output parsing and fallback | ✅ Covered |
| Policy verdict by threshold | ✅ Covered |
| Quarantine state transitions | ✅ Covered |
| RBAC enforcement on admin routes | ✅ Covered |
| HTML sanitization | ✅ Covered |
| IMAP proxy session auth | ✅ Covered |
| IMAP proxy command handling | ✅ Covered |
| SMTP proxy auth flows | ✅ Covered |
| SMTP proxy delivery modes | ✅ Covered |
| Message parsing | ✅ Covered |
| URL extraction | ✅ Covered |
| Deterministic checks | ✅ Covered |

---

## End-to-end verification scripts

These scripts require a running Docker stack (all services healthy).

### IMAP proxy verification

```bash
python scripts/verify_imap_proxy.py
```

Runs 17 assertions:
- TCP connection and greeting
- CAPABILITY command
- LOGIN with valid and invalid credentials
- LIST folder enumeration
- SELECT INBOX / Mindwall/Quarantine
- UID SEARCH ALL
- FETCH message data
- Mutation command rejection
- LOGOUT

### SMTP proxy verification

```bash
python scripts/verify_smtp_proxy.py
```

Runs 22 assertions:
- TCP connection and greeting
- EHLO capability negotiation
- AUTH PLAIN and AUTH LOGIN (valid and invalid)
- MAIL FROM / RCPT TO sequencing
- DATA transaction
- RSET behavior
- Message size rejection
- NOOP / QUIT
- Database record persistence

### HTTP smoke test

```bash
python scripts/smoke_test.py
```

Basic HTTP connectivity test against the running app.

---

## Linting

```bash
# Check all files
ruff check .

# Fix auto-fixable issues
ruff check . --fix

# Format
ruff format .
```

Ruff is configured in `pyproject.toml` with the following rule sets enabled:
`E`, `W`, `F`, `I`, `N`, `UP`, `S`, `B`, `A`, `C4`, `PTH`, `RUF`

Key ignores:
- `S101` — `assert` statements (expected in pytest)
- `B008` — `Depends()` in default arguments (FastAPI idiom)

---

## Adding tests

### For new features
- Add unit tests in `tests/unit/` for all new service methods and business logic
- Add integration tests in `tests/integration/` for new routes
- Run `pytest tests/ -q` before submitting

### For bug fixes
- Add a regression test that fails before the fix and passes after
- Do not remove existing tests to make builds pass

### Test file naming
- Unit tests: `tests/unit/test_<module>.py`
- Integration tests: `tests/integration/test_<domain>_routes.py` or `test_<component>.py`
