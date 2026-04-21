# Mindwall

**Privacy-first, self-hosted email security platform.**

Mindwall sits between your users and their existing mail provider using local IMAP and SMTP proxies. Incoming messages are inspected, scored across 12 psychological manipulation dimensions, and either delivered, flagged, or quarantined — all without sending any message data outside your deployment boundary.

> Inference runs 100% on-premises via [Ollama](https://ollama.com) + Llama 3.1 8B.

---

## Architecture

See [architecture.md](architecture.md) for the full system design.

**Current status: Phase 4 — Analysis Engine**

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Complete | Project skeleton, config, auth, health endpoints, DB/Redis wiring |
| 2 | ✅ Complete | Mailbox registration, credential encryption, proxy setup instructions |
| 3 | ✅ Complete | RFC 5322 parsing, MIME normalization, HTML sanitization, URL extraction, Message Lab UI |
| 4 | Planned | Analysis engine — deterministic checks + Ollama LLM integration |
| 5 | Planned | IMAP/SMTP proxies, enforcement, quarantine UI, admin alerting |
| 6 | Planned | Workers, observability, hardening |
q11
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

## Message Ingestion (Phase 3)

Phase 3 adds RFC 5322 email parsing, safe content extraction, and the admin Message Lab.

### What was built
- **RFC 5322 parser** (`app/messages/parser.py`): Parses raw `.eml` bytes using Python's stdlib `email` module. Extracts envelope fields, plain-text body, HTML body, URLs, and attachments. Never raises — degrades gracefully on malformed input.
- **HTML sanitizer** (`app/messages/html_safe.py`): Strips scripts, styles, and remote content. Extracts safe plain-text and anchor URLs from HTML parts.
- **URL extractor** (`app/messages/urls.py`): Extracts and normalizes HTTP/HTTPS URLs from plain-text bodies and HTML anchors. Blocks `javascript:`, `data:`, `vbscript:`, and `file:` schemes.
- **Raw message store** (`app/messages/storage.py`): Writes raw `.eml` files to disk at `{root}/{sha256[:2]}/{sha256}.eml`. Idempotent. SHA-256 addressed.
- **Ingestion service** (`app/messages/service.py`): Orchestrates parse → store → persist for a single message.
- **Message Lab UI** (`/admin/messages/`): Admin-only tool for uploading `.eml` files and inspecting parse results. Lists ingested messages and shows per-message detail (envelope, auth headers, body, URLs, attachments, storage info).
- **Alembic migration** (`0003_create_messages.py`): Creates `messages`, `message_urls`, `message_attachments` tables.

### Message Lab usage

1. Log in as an admin user.
2. Navigate to **Admin → Message Lab** (`/admin/messages/`).
3. Click **Upload .eml** and select a raw email file (max `MESSAGE_LAB_MAX_UPLOAD_MB` MB).
4. The file is parsed, stored to `RAW_MESSAGE_STORE_PATH`, and its detail page is shown.

### Raw storage layout

```text
data/
  raw_messages/
    ab/
      ab3f... (sha256 hex).eml
    cd/
      cd7e....eml
```

### New configuration keys

| Key | Default | Description |
|-----|---------|-------------|
| `RAW_MESSAGE_STORE_PATH` | `./data/raw_messages` | Filesystem path for raw .eml storage |
| `MESSAGE_LAB_MAX_UPLOAD_MB` | `25` | Maximum .eml upload size in MB |
| `MESSAGE_LAB_ENABLED` | `true` | Toggle the Message Lab admin UI |

---

## Analysis Engine (Phase 4)

Phase 4 adds deterministic security checks, local Ollama LLM orchestration, 12-dimension manipulation scoring, and policy verdict computation.

### What was built

- **Deterministic checks** (`app/analysis/deterministic.py`): 10 explicit rule-based checks covering display-name/reply-to mismatch, brand impersonation, link-text/href mismatch, suspicious URL structure (IP hosts, deep subdomains, credential keywords), risky attachments, credential/payment/urgency/fear language, missing DKIM/SPF, and HTML-only body.
- **Ollama client** (`app/analysis/ollama_client.py`): Async HTTP client for the local Ollama `/api/generate` endpoint. Enforces localhost-only access (privacy guarantee). Raises `OllamaError` on timeout, connect errors, or bad responses. Never calls external APIs.
- **Prompt builder + parser** (`app/analysis/prompt.py`): Builds a compact structured prompt with envelope, auth signals, body, URLs, and deterministic evidence. Requests strict JSON output with all 12 dimension scores. Parses and validates the response, clamping scores to `[0.0, 1.0]`. Returns `None` on parse failure.
- **Analysis orchestrator** (`app/analysis/service.py`): Coordinates the full pipeline: deterministic checks → (optional) Ollama LLM call → retry with strict prompt if malformed → degrade gracefully on failure → combined score → verdict → DB persistence.
- **Policy verdict engine** (`app/policies/verdict.py`): Converts combined risk + confidence into a stable verdict (`allow` → `allow_with_banner` → `soft_hold` → `quarantine` → `escalate_to_admin` / `reject`). Supports configurable thresholds, degraded-mode adjustment, and gateway mode.
- **DB models** (`app/analysis/models.py`): `AnalysisRun` (one per analysis pipeline run) + `DimensionScore` (12 rows per run). Alembic migration `0004_create_analysis.py`.
- **Message Lab integration**: Detail page shows full analysis results after clicking **Analyse**. Displays verdict badge, risk score, confidence, dimension score grid, deterministic findings, evidence list, degraded banner.

### Analysis pipeline

```
ingest message
    ↓
deterministic checks   →  Finding list + risk score + dim scores
    ↓
build LLM prompt       →  structured prompt with evidence
    ↓
Ollama generate        →  raw JSON response
    ↓
parse + validate       →  LLMAnalysisResponse (or None → retry → degrade)
    ↓
combine scores         →  overall_risk = 0.4 * det + 0.6 * llm
    ↓
compute verdict        →  allow / allow_with_banner / soft_hold / quarantine / escalate
    ↓
persist AnalysisRun    →  DB + DimensionScore rows
```

### Degraded mode

If Ollama is unreachable, returns invalid JSON, or is disabled:
- `is_degraded = True`
- `overall_risk = deterministic_risk_score`
- `confidence = 0.35` (conservative)
- Verdict computed with a +0.10 risk adjustment when `confidence < 0.5`
- `status = "degraded"` recorded in DB
- UI shows a yellow warning banner

### New configuration keys (Phase 4)

| Key | Default | Description |
|-----|---------|-------------|
| `LLM_ENABLED` | `true` | Set to `false` for deterministic-only analysis |
| `OLLAMA_TIMEOUT_SECONDS` | `120.0` | Seconds to wait for Ollama generate response |
| `ANALYSIS_PROMPT_VERSION` | `1.0` | Prompt version string (bump when schema changes) |
| `VERDICT_THRESHOLD_ALLOW` | `0.25` | Risk score upper bound for ALLOW verdict |
| `VERDICT_THRESHOLD_ALLOW_WITH_BANNER` | `0.45` | Upper bound for ALLOW_WITH_BANNER |
| `VERDICT_THRESHOLD_SOFT_HOLD` | `0.65` | Upper bound for SOFT_HOLD |
| `VERDICT_THRESHOLD_QUARANTINE` | `0.85` | Upper bound for QUARANTINE |

---

## Next Development Step (Phase 5)

Enforcement:
1. IMAP proxy with filtered mailbox views and quarantine virtual folder.
2. Quarantine storage, review UI, and release/delete workflows.
3. Admin alerting for high-risk messages.
---

## Completed: Phase 6 — Upstream IMAP Sync + Mailbox Virtualization

### What was built

Phase 6 implements the backend foundation for pulling messages from upstream
IMAP servers into Mindwall's local store — without building the full IMAP
protocol proxy server yet.

**New modules:**

| Module | Purpose |
|--------|---------|
| `app/proxies/imap/client.py` | Async-friendly upstream IMAP client wrapping `imaplib` |
| `app/mailboxes/sync_models.py` | ORM models: `MailboxSyncState`, `MailboxItem` |
| `app/mailboxes/sync_service.py` | Sync orchestrator: auth, fetch, ingest, analyze, quarantine |
| `app/mailboxes/view_service.py` | Virtual inbox layer: visible/quarantined/pending views |
| `app/mailboxes/sync_router.py` | Admin routes: sync status, trigger, inbox, quarantine, item detail |
| `alembic/versions/0006_create_sync_tables.py` | Migration for new tables |
| `app/templates/admin/mailboxes/` | 3 new admin templates |

### How upstream sync works

1. Admin triggers sync via `POST /admin/mailboxes/{id}/sync`
2. Sync service loads or initializes `MailboxSyncState` for the folder
3. Decrypts upstream IMAP credentials with `CredentialEncryptor`
4. Connects to upstream IMAP via `UpstreamImapClient` (SSL/TLS, STARTTLS, or plain)
5. SELECTs the folder; detects UIDVALIDITY resets
6. Fetches UIDs above `last_seen_uid` (up to `batch_size`)
7. For each new UID: fetch raw bytes → ingest → analyze → quarantine decision → record `MailboxItem`
8. Updates checkpoint (`last_seen_uid`, `last_sync_at`, `sync_status`)
9. Commits and returns `SyncResult` summary

**Sync is idempotent** — re-syncing the same UIDs is safe.

### How mailbox virtualization works

`view_service.py` exposes filtered views over `MailboxItem`:

| View | Visibilities included |
|------|-----------------------|
| `get_visible_inbox` | `VISIBLE` |
| `get_quarantine_inbox` | `QUARANTINED`, `HIDDEN` |
| `get_pending_items` | `PENDING` |

Each item is enriched with its associated `Message`, `AnalysisRun`,
and `QuarantineItem` in a single bulk query.

### How degraded/error sync states work

- **Auth failures**: `sync_status=ERROR`, `failed_auth=True`, state committed immediately
- **Connection failures**: `sync_status=ERROR`, `failed_connection=True`
- **Per-message failures**: error recorded on `MailboxItem`, sync continues to next UID
- **Partial sync**: `sync_status=PARTIAL` if some messages ingested and some failed
- UIDVALIDITY reset triggers a full re-sync from UID 0

### New configuration keys (Phase 6)

| Key | Default | Description |
|-----|---------|-------------|
| `IMAP_SYNC_TIMEOUT_SECONDS` | `30` | IMAP connection/operation timeout |
| `IMAP_SYNC_DEFAULT_FOLDER` | `INBOX` | Default folder when triggering sync from UI |
| `IMAP_SYNC_BATCH_SIZE` | `50` | Max new UIDs to fetch per sync run |

### Admin UI

- `GET /admin/mailboxes/{id}/sync` — sync status, per-folder checkpoints, item counts, trigger form
- `GET /admin/mailboxes/{id}/inbox` — Mindwall-visible messages
- `GET /admin/mailboxes/{id}/quarantine` — quarantined/hidden messages
- `GET /admin/mailboxes/{id}/items/{item_id}` — full item detail with analysis

### What is NOT included yet

- Full IMAP proxy protocol server (future phase)
- Background worker / scheduled sync (future phase)
- IMAP IDLE push support (future phase)

---

## Next Development Step (Phase 7)

Background workers and observability:
1. Async background sync worker (APScheduler or similar)
2. Redis-backed task queue for analysis jobs
3. Prometheus metrics: sync latency, quarantine rate, model health
4. Health endpoint upgrades: Ollama, Redis, background workers
5. End-to-end integration tests against a real IMAP sandbox
