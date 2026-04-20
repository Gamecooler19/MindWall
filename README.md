# Mindwall

**Privacy-first, self-hosted email security platform.**

Mindwall sits between your users and their existing mail provider using local IMAP and SMTP proxies. Incoming messages are inspected, scored across 12 psychological manipulation dimensions, and either delivered, flagged, or quarantined — all without sending any message data outside your deployment boundary.

> Inference runs 100% on-premises via [Ollama](https://ollama.com) + Llama 3.1 8B.

---

## Architecture

See [architecture.md](architecture.md) for the full system design.

**Current status: Phase 2 — Mailbox Onboarding**

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Complete | Project skeleton, config, auth, health endpoints, DB/Redis wiring |
| 2 | ✅ Complete | Mailbox registration, credential encryption, proxy setup instructions |
| 3 | Planned | IMAP + SMTP proxy services, message parsing |
| 4 | Planned | Analysis engine — deterministic checks + Ollama LLM integration |
| 5 | Planned | Enforcement, quarantine UI, admin alerting |
| 6 | Planned | Workers, observability, hardening |

---

## Prerequisites

| Dependency | Version | Notes |
|-----------|---------|-------|
| Python | 3.11+ | Required (3.12 also supported) |
| PostgreSQL | 15+ | Primary data store |
| Redis | 7+ | Caching, queues, sessions |
| Ollama | Latest | Local LLM inference (Phase 4+) |

---

## Local Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd mindwall
```

### 2. Create a virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -e ".[dev]"
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in the required values:

```bash
# Generate a secret key
python -c "import secrets; print(secrets.token_hex(32))"

# Generate a Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Minimum required `.env` values:

```env
SECRET_KEY=<output from above>
ENCRYPTION_KEY=<output from above>
DATABASE_URL=postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall
REDIS_URL=redis://localhost:6379/0
```

### 5. Start PostgreSQL and Redis

Using Docker Compose (simplest):

```bash
docker run -d --name mindwall-postgres \
  -e POSTGRES_USER=mindwall \
  -e POSTGRES_PASSWORD=mindwall \
  -e POSTGRES_DB=mindwall \
  -p 5432:5432 postgres:15

docker run -d --name mindwall-redis \
  -p 6379:6379 redis:7
```

### 6. Run database migrations

```bash
alembic upgrade head
```

### 7. Start the application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

> **Note:** In Phase 1, no users exist in the database. Use `scripts/create_admin.py` (coming soon) or insert directly to bootstrap the first admin account.

---

## Mailbox Registration (Phase 2)

Users register their upstream IMAP/SMTP credentials through the web UI at `/mailboxes/new`.

1. Fill in your upstream IMAP server details (host, port, username, password, security mode).
2. Fill in your upstream SMTP server details.
3. Mindwall validates connectivity, encrypts your credentials with a Fernet key, and generates unique **Mindwall proxy credentials** for your mail client.
4. On the detail page you are shown your proxy password **exactly once** — copy and save it before navigating away.
5. Configure your mail client to connect to `IMAP_PROXY_DISPLAY_HOST:IMAP_PROXY_PORT` and `SMTP_PROXY_DISPLAY_HOST:SMTP_PROXY_PORT` using the Mindwall proxy credentials.

### How credential security works

- Upstream passwords are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256) using the `ENCRYPTION_KEY`.
- Proxy passwords are stored only as bcrypt hashes — they cannot be recovered. If you lose yours, use the "Reset proxy password" button.
- No credential is ever logged or exposed in API responses.

---

## Running Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=app --cov-report=term-missing
```

Tests use an in-memory SQLite database and do not require PostgreSQL or Redis to be running. Integration tests that check `/health/ready` gracefully handle the absence of Redis by asserting the correct response shape rather than requiring connectivity.

---

## Linting and Formatting

```bash
# Check
ruff check .

# Format
ruff format .

# Check + format together
ruff check . --fix && ruff format .
```

---

## Project Structure

```
mindwall/
  app/
    main.py              # FastAPI app factory, lifespan, middleware
    config.py            # Pydantic settings (env-driven, cached)
    dependencies.py      # Shared FastAPI dependencies
    logging_config.py    # Structured logging (structlog)
    security/
      crypto.py          # Fernet credential encryption
    auth/
      router.py          # Login / logout routes
      service.py         # Password hashing, credential verification
      schemas.py         # UserContext and auth Pydantic models
    users/
      models.py          # User ORM model, UserRole enum
    policies/
      constants.py       # ManipulationDimension (12), Verdict enums
    health/
      router.py          # /health/live, /health/ready
    admin/
      router.py          # Admin dashboard (Phase 1 placeholder)
    db/
      base.py            # SQLAlchemy DeclarativeBase with timestamps
      session.py         # Async engine, session factory, get_db_session
    mailboxes/           # Phase 2 — mailbox registration
    proxies/
      imap/              # Phase 3 — IMAP proxy
      smtp/              # Phase 3 — SMTP proxy
    messages/            # Phase 3 — RFC 5322 parsing
    analysis/            # Phase 4 — deterministic checks + LLM
    policies/            # Phase 5 — decision engine
    quarantine/          # Phase 5 — encrypted storage + review UI
    alerts/              # Phase 5 — admin alerting
    audit/               # Phase 6 — immutable audit log
    templates/           # Jinja2 templates (Tailwind CDN)
    static/              # JS + CSS static assets
  workers/
    analysis_worker.py   # Phase 4
    llm_worker.py        # Phase 4
    maintenance_worker.py # Phase 6
  tests/
    conftest.py          # Shared fixtures (in-memory SQLite, test client)
    unit/                # Unit tests (no I/O)
    integration/         # Integration tests (HTTP + DB)
  alembic/               # Database migrations
  alembic.ini
  pyproject.toml
  .env.example
  architecture.md
```

---

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | ✅ | — | Session cookie signing key (min 32 chars) |
| `ENCRYPTION_KEY` | ✅ | — | Fernet key for credential encryption |
| `DATABASE_URL` | — | `postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall` | Async PostgreSQL URL |
| `REDIS_URL` | — | `redis://localhost:6379/0` | Redis connection URL |
| `DEBUG` | — | `false` | Enables debug logs and OpenAPI docs at `/api/docs` |
| `OLLAMA_BASE_URL` | — | `http://localhost:11434` | Local Ollama endpoint |
| `OLLAMA_MODEL` | — | `llama3.1:8b` | Model to use for analysis |
| `IMAP_PROXY_HOST` | — | `0.0.0.0` | IP the IMAP proxy binds to |
| `IMAP_PROXY_PORT` | — | `1993` | Local IMAP proxy listen port |
| `IMAP_PROXY_DISPLAY_HOST` | — | `127.0.0.1` | Host shown to users in proxy setup instructions |
| `SMTP_PROXY_HOST` | — | `0.0.0.0` | IP the SMTP proxy binds to |
| `SMTP_PROXY_PORT` | — | `1587` | Local SMTP proxy listen port |
| `SMTP_PROXY_DISPLAY_HOST` | — | `127.0.0.1` | Host shown to users in proxy setup instructions |
| `CONNECTION_TIMEOUT_SECONDS` | — | `10` | Upstream IMAP/SMTP connectivity check timeout |
| `BLOB_STORAGE_PATH` | — | `./data/blobs` | Path for encrypted mail storage |
| `ANALYSIS_ENABLED` | — | `true` | Enable LLM-based analysis |
| `GATEWAY_MODE` | — | `false` | Enable pre-delivery inline gateway mode |

---

## Health Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health/live` | None | Liveness: 200 if process is alive |
| `GET /health/ready` | None | Readiness: 200 if DB + Redis reachable, 503 if degraded |

---

## Security Notes

- Upstream mail credentials are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256).
- Session cookies are signed with `SECRET_KEY` and flagged `HttpOnly`, `SameSite=Lax`.
- HTTPS-only cookies are enforced in production (`DEBUG=false`).
- No message content is ever sent to external APIs or services.
- OpenAPI docs (`/api/docs`) are only enabled when `DEBUG=true`.

---

## Next Development Step (Phase 3)

IMAP + SMTP proxy services:
1. Build the local IMAP proxy (asyncio TCP server, upstream connection management, UID mapping).
2. Build the local SMTP proxy (upstream forwarding, alert routing).
3. Integrate cached verdict-aware mailbox views.
4. Wire message parsing (RFC 5322, MIME normalization, HTML sanitization).
