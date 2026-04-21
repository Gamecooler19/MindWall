# Configuration reference

All Mindwall configuration is driven by environment variables. The application reads from a `.env` file (local dev) or from the process environment (Docker / systemd). No `os.getenv()` calls exist outside `app/config.py`.

The `Settings` class is a Pydantic v2 `BaseSettings` model. It validates all values at startup and raises a clear error if required values are missing or malformed.

---

## Required settings

These must be set before the application will start.

| Variable | Type | Description |
|----------|------|-------------|
| `SECRET_KEY` | string (≥32 chars) | Signs session cookies. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ENCRYPTION_KEY` | Fernet key string | Encrypts upstream IMAP/SMTP credentials at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

---

## Application

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `Mindwall` | Application name (used in templates) |
| `DEBUG` | `false` | Enables debug-level logging and exposes OpenAPI docs at `/api/docs` |

---

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall` | Async PostgreSQL connection URL. Must use the `postgresql+asyncpg://` scheme |

> **Note:** Alembic uses a synchronous version of this URL (substituting `postgresql+psycopg2://`) automatically via `Settings.sync_database_url`.

---

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |

---

## Session

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_MAX_AGE` | `3600` | Session cookie lifetime in seconds |

---

## Ollama (LLM inference)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama API endpoint. **Must resolve to localhost** — the client enforces this as a privacy guarantee |
| `OLLAMA_MODEL` | `llama3.1:8b` | Model name to use for analysis |
| `OLLAMA_TIMEOUT_SECONDS` | `120.0` | Seconds to wait for an Ollama generate response before aborting |

---

## Analysis engine

| Variable | Default | Description |
|----------|---------|-------------|
| `ANALYSIS_ENABLED` | `true` | Set to `false` to disable all analysis. Messages are ingested but not scored |
| `LLM_ENABLED` | `true` | Set to `false` to use deterministic-only analysis (no Ollama calls) |
| `ANALYSIS_PROMPT_VERSION` | `1.0` | Prompt version identifier. Bump this when the prompt schema changes significantly |

---

## Verdict thresholds

Risk scores are in `[0.0, 1.0]`. The verdict is the lowest threshold that the score exceeds.

| Variable | Default | Verdict assigned when risk ≤ threshold |
|----------|---------|---------------------------------------|
| `VERDICT_THRESHOLD_ALLOW` | `0.25` | `allow` |
| `VERDICT_THRESHOLD_ALLOW_WITH_BANNER` | `0.45` | `allow_with_banner` |
| `VERDICT_THRESHOLD_SOFT_HOLD` | `0.65` | `soft_hold` |
| `VERDICT_THRESHOLD_QUARANTINE` | `0.85` | `quarantine` |
| — | above 0.85 | `escalate_to_admin` |

These thresholds can also be overridden at runtime via the Policy Editor UI (`/admin/policy/`).

---

## Proxy listeners

### IMAP proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAP_PROXY_HOST` | `0.0.0.0` | Bind address for the IMAP proxy listener |
| `IMAP_PROXY_PORT` | `1993` | TCP port the IMAP proxy listens on |
| `IMAP_PROXY_DISPLAY_HOST` | `127.0.0.1` | Hostname shown to users in the proxy setup instructions page |

### SMTP proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_PROXY_HOST` | `0.0.0.0` | Bind address for the SMTP proxy listener |
| `SMTP_PROXY_PORT` | `1587` | TCP port the SMTP proxy listens on |
| `SMTP_PROXY_DISPLAY_HOST` | `127.0.0.1` | Hostname shown to users in the proxy setup instructions page |
| `SMTP_DELIVERY_MODE` | `capture` | `capture` stores submissions locally; `relay` forwards them upstream |
| `SMTP_RELAY_TIMEOUT_SECONDS` | `30` | Seconds to wait when connecting to the upstream SMTP relay |
| `SMTP_MAX_MESSAGE_BYTES` | `26214400` | Maximum accepted message size in bytes (default 25 MB) |
| `OUTBOUND_MESSAGE_STORE_PATH` | `./data/outbound_messages` | Root directory for captured outbound `.eml` files |

---

## Upstream connectivity

| Variable | Default | Description |
|----------|---------|-------------|
| `CONNECTION_TIMEOUT_SECONDS` | `10` | Timeout for upstream IMAP/SMTP connectivity checks during mailbox registration |

---

## Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `BLOB_STORAGE_PATH` | `./data/blobs` | Root directory for encrypted message blobs and quarantine attachments |
| `RAW_MESSAGE_STORE_PATH` | `./data/raw_messages` | Root directory for raw `.eml` files. Layout: `<root>/<first2chars>/<sha256>.eml` |

---

## Message Lab

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGE_LAB_MAX_UPLOAD_MB` | `25` | Maximum size for `.eml` uploads via the Message Lab UI |
| `MESSAGE_LAB_ENABLED` | `true` | Set to `false` to disable the Message Lab routes entirely |

---

## Feature flags

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_MODE` | `false` | Enable inline pre-delivery quarantine mode. When `true`, `reject` verdicts are used for high-risk messages |
| `QUARANTINE_SOFT_HOLD` | `false` | When `true`, `soft_hold` verdicts also create quarantine items. Default: only `quarantine` and above create quarantine items |

---

## IMAP sync

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAP_SYNC_TIMEOUT_SECONDS` | `30` | Timeout for individual IMAP operations during upstream sync |
| `IMAP_SYNC_DEFAULT_FOLDER` | `INBOX` | Default folder name used when triggering sync from the admin UI |
| `IMAP_SYNC_BATCH_SIZE` | `50` | Maximum number of new UIDs to process in a single sync run |

---

## Docker-specific variables

These are used by the Docker entrypoint (`scripts/entrypoint.sh`) and are not part of the `Settings` model.

| Variable | Default | Description |
|----------|---------|-------------|
| `MINDWALL_CREATE_ADMIN` | — | Set to `true` to create the first admin user on startup |
| `MINDWALL_ADMIN_EMAIL` | — | Email address for the bootstrapped admin user |
| `MINDWALL_ADMIN_PASSWORD` | — | Password for the bootstrapped admin user |
| `DB_HOST` | — | PostgreSQL hostname (used by entrypoint wait loop) |
| `DB_PORT` | — | PostgreSQL port (used by entrypoint wait loop) |
| `APP_PORT` | `8000` | Host port mapping for the app container |
| `IMAP_PROXY_DEV_PORT` | `1143` | Host port mapping for the IMAP proxy container |
| `SMTP_PROXY_DEV_PORT` | `1587` | Host port mapping for the SMTP proxy container |
