# Changelog

All notable changes to Mindwall are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are milestone-based because Mindwall uses a phase-based development model
rather than semantic versioning releases.

---

## [Unreleased] — Phase 11

### Planned
- STARTTLS for IMAP and SMTP proxies
- Outbound message inspection (manipulation scoring on sent mail)
- Inline gateway mode: pre-delivery quarantine without IMAP proxy dependency
- Background sync worker with configurable schedules
- Prometheus metrics endpoint
- Automated integration test suite against live IMAP/SMTP sandboxes
- Deployment hardening guide

---

## [0.10.0] — Phase 10: SMTP Submission Proxy

### Added
- **SMTP proxy server** (`app/proxies/smtp/server.py`): Full asyncio RFC 5321 submission
  server supporting `AUTH PLAIN`, `AUTH LOGIN`, `MAIL FROM`, `RCPT TO`, `DATA`, `RSET`,
  `NOOP`, `QUIT`. Each connection runs in its own asyncio task.
- **SMTP session authentication** (`app/proxies/smtp/session.py`): Validates Mindwall
  proxy credentials against the database. Reuses the shared credential verification
  layer from the IMAP proxy.
- **Dual delivery modes** (`app/proxies/smtp/delivery.py`):
  - `capture` — stores the raw `.eml` and metadata locally; nothing leaves the server.
  - `relay` — forwards the submission to the upstream SMTP server using decrypted stored credentials.
- **Upstream SMTP relay client** (`app/proxies/smtp/relay.py`): Async relay using
  `smtplib` in a thread pool executor. Supports SSL, STARTTLS, and plain connections.
- **OutboundMessage ORM model** (`app/proxies/smtp/models.py`): Persists submission
  metadata (MAIL FROM, RCPT TO, subject, SHA-256, size, delivery mode and status).
- **Alembic migration 0008**: Creates `outbound_messages` table.
- **Admin outbound UI** (`/admin/outbound/`, `/admin/outbound/{id}`): List and detail
  views for captured submissions.
- **SMTP proxy worker** (`workers/smtp_proxy.py`): Standalone entrypoint.
- **Docker service**: `smtp_proxy` service in `docker-compose.yml`, host port 1587.
- **Verification script** (`scripts/verify_smtp_proxy.py`): 22 automated assertions
  covering greeting, auth, mail transactions, enforcement, and DB persistence.
- **Configuration**: `SMTP_DELIVERY_MODE`, `SMTP_RELAY_TIMEOUT_SECONDS`,
  `SMTP_MAX_MESSAGE_BYTES`, `OUTBOUND_MESSAGE_STORE_PATH`.

### Fixed
- Migration 0009: adds missing `created_at`/`updated_at` columns to `policy_settings`
  and `alerts` tables that were created before the shared Base class gained automatic
  timestamp columns.

---

## [0.9.0] — Phase 9: IMAP Proxy MVP

### Added
- **IMAP proxy server** (`app/proxies/imap/server.py`): Read-only RFC 3501-compatible
  asyncio IMAP server. Each TCP connection runs in its own asyncio task.
- **IMAP session authentication** (`app/proxies/imap/session.py`): Validates Mindwall
  proxy credentials, loads the associated mailbox profile.
- **Virtual mailbox adapter** (`app/proxies/imap/mailbox.py`): Exposes `INBOX` and
  `Mindwall/Quarantine` virtual folders backed by the local `MailboxItem` store.
  Generates sequential UIDs, returns RFC 5322-formatted messages on `FETCH`.
- **Upstream IMAP client** (`app/proxies/imap/client.py`): Async wrapper around
  `imaplib.IMAP4`/`IMAP4_SSL` used for upstream connectivity checks and sync.
- **IMAP proxy worker** (`workers/imap_proxy.py`): Standalone entrypoint.
- **Docker service**: `imap_proxy` service in `docker-compose.yml`, host port 1143.
- **Verification script** (`scripts/verify_imap_proxy.py`): 17 automated assertions.
- **Configuration**: `IMAP_PROXY_HOST`, `IMAP_PROXY_PORT`, `IMAP_PROXY_DISPLAY_HOST`.

### Security
- Mutation commands (`STORE`, `COPY`, `EXPUNGE`, etc.) are rejected with `NO [CANNOT]`.
- Line length limits enforced to prevent resource exhaustion.
- Maximum idle connection timeout enforced.

---

## [0.8.0] — Phase 8: Docker Stack and Admin Surface

### Added
- **Docker Compose stack**: `app`, `db`, `redis` services with health checks, named
  volumes, and a dedicated bridge network.
- **Dockerfile**: Multi-stage build (build → runtime). Non-root user. Minimal image.
- **Entrypoint script** (`scripts/entrypoint.sh`): Waits for PostgreSQL, runs
  `alembic upgrade head`, optionally creates admin user, then starts uvicorn.
- **Admin bootstrap**: `MINDWALL_CREATE_ADMIN`, `MINDWALL_ADMIN_EMAIL`,
  `MINDWALL_ADMIN_PASSWORD` environment variables for first-run admin creation.
- **`.env.docker`** / **`.env.example`**: Full environment configuration templates.
- **Policy editor** (`/admin/policy/`): Read/write UI for runtime-configurable
  policy settings stored in the database.
- **Alerts & Incidents** (`/admin/alerts/`, `/admin/alerts/{id}`): Full alert
  lifecycle — acknowledge, resolve, add resolution notes.
- **Audit log viewer** (`/admin/audit/`): Paginated view of append-only audit events.
- **Model health page** (`/admin/health/model`): Ollama connectivity status and
  available models.
- **Mailboxes admin** (`/admin/mailboxes/`): All registered mailbox profiles overview.
- **PolicySetting and Alert ORM models** with Alembic migration 0007.

---

## [0.7.0] — Phase 7: Admin UI Foundations

### Added
- **Admin dashboard** (`/admin/`): Live counts — pending quarantine, open alerts,
  registered mailboxes.
- **Quarantine inbox** (`/admin/quarantine/`): Filterable queue of quarantined messages.
- **Quarantine detail** (`/admin/quarantine/{id}`): Full message detail with analysis
  results, dimension score grid, evidence, and audit timeline.
- **Quarantine actions**: Release to inbox, delete permanently, add admin note.
- Navigation sidebar and Tailwind-based admin layout template.

---

## [0.6.0] — Phase 6: IMAP Sync and Mailbox Virtualization

### Added
- **Upstream IMAP sync** (`app/mailboxes/sync_service.py`): Pulls new messages from
  upstream IMAP servers, ingests and analyzes each, records `MailboxItem` rows.
- **MailboxSyncState model**: Per-folder sync checkpoints with UIDVALIDITY tracking.
- **MailboxItem model**: Maps upstream UIDs to local messages with visibility state.
- **View service** (`app/mailboxes/view_service.py`): Filtered mailbox views —
  `VISIBLE`, `QUARANTINED`, `HIDDEN`, `PENDING`.
- **Admin sync routes** (`/admin/mailboxes/{id}/sync`, `/inbox`, `/quarantine`,
  `/items/{item_id}`): Sync status, manual trigger, item inspection.
- **Alembic migration 0006**: `mailbox_sync_states`, `mailbox_items` tables.
- **Configuration**: `IMAP_SYNC_TIMEOUT_SECONDS`, `IMAP_SYNC_DEFAULT_FOLDER`,
  `IMAP_SYNC_BATCH_SIZE`.

### Changed
- Sync is idempotent — re-syncing the same UIDs is safe.
- UIDVALIDITY resets trigger full re-sync from UID 0.

---

## [0.5.0] — Phase 5: Quarantine and Enforcement

### Added
- **Quarantine service** (`app/quarantine/service.py`): Create, transition, release,
  and delete quarantine items with full state-machine enforcement.
- **QuarantineItem and AuditEvent ORM models** with Alembic migration 0005.
- **Quarantine actions**: `PENDING_REVIEW` → `IN_REVIEW` → `RELEASED` / `DELETED`.
- **Audit trail**: Every state transition logged to `audit_events` with actor,
  action, timestamp, and structured details (append-only).
- **Blob storage** (`app/quarantine/blob.py`): Encrypted at-rest storage for
  quarantined message content and attachments.
- **Alert creation**: High-risk verdicts automatically create `Alert` records.

---

## [0.4.0] — Phase 4: Analysis Engine

### Added
- **Deterministic checks** (`app/analysis/deterministic.py`): 10 explicit rule-based
  checks — display-name/reply-to mismatch, brand impersonation, link-text/href
  mismatch, suspicious URLs, risky attachments, credential/payment/urgency language,
  missing DKIM/SPF, HTML-only body.
- **Ollama client** (`app/analysis/ollama_client.py`): Async HTTP client enforcing
  localhost-only access. Raises `OllamaError` on timeout, connection failure, or
  invalid response. Never calls external APIs.
- **Prompt builder and parser** (`app/analysis/prompt.py`): Structured prompt with
  envelope, auth signals, body, URLs, and deterministic evidence. Requests strict
  JSON output with all 12 dimension scores. Clamps scores to `[0.0, 1.0]`.
- **Analysis orchestrator** (`app/analysis/service.py`): Coordinates deterministic
  checks → Ollama call → retry on malformed output → graceful degradation →
  combined scoring → verdict → DB persistence.
- **Policy verdict engine** (`app/policies/verdict.py`): Converts risk score and
  confidence into a stable verdict. Supports configurable thresholds, degraded-mode
  risk adjustment, and gateway mode.
- **AnalysisRun and DimensionScore ORM models** with Alembic migration 0004.
- **Degraded mode**: If Ollama fails, deterministic-only analysis proceeds with
  conservative confidence and a risk adjustment applied.
- **Configuration**: `LLM_ENABLED`, `OLLAMA_TIMEOUT_SECONDS`,
  `ANALYSIS_PROMPT_VERSION`, `VERDICT_THRESHOLD_*` variables.

---

## [0.3.0] — Phase 3: Message Ingestion and Message Lab

### Added
- **RFC 5322 parser** (`app/messages/parser.py`): Parses raw `.eml` bytes using
  Python's `email` stdlib. Extracts envelope, plain-text body, HTML body, URLs,
  and attachment metadata. Never raises — degrades gracefully on malformed input.
- **HTML sanitizer** (`app/messages/html_safe.py`): Strips scripts, styles, and
  remote content. Extracts safe plain-text and anchor URLs.
- **URL extractor** (`app/messages/urls.py`): Extracts and normalizes HTTP/HTTPS
  URLs. Blocks `javascript:`, `data:`, `vbscript:`, and `file:` schemes.
- **Raw message store** (`app/messages/storage.py`): SHA-256-addressed `.eml`
  storage with two-char prefix layout. Idempotent writes.
- **Ingestion service** (`app/messages/service.py`): Orchestrates parse → store →
  persist for a single message.
- **Message Lab UI** (`/admin/messages/`): Upload `.eml` files, inspect parse
  results, trigger analysis.
- **ORM models** for `messages`, `message_urls`, `message_attachments` with
  Alembic migration 0003.
- **Configuration**: `RAW_MESSAGE_STORE_PATH`, `MESSAGE_LAB_MAX_UPLOAD_MB`,
  `MESSAGE_LAB_ENABLED`.

---

## [0.2.0] — Phase 2: Mailbox Onboarding

### Added
- **Mailbox registration UI** (`/mailboxes/new`, `/mailboxes/`): Full IMAP + SMTP
  upstream configuration with validation.
- **Upstream connectivity test**: Validates IMAP and SMTP credentials before saving.
- **Credential encryption**: Upstream passwords encrypted at rest with Fernet
  (AES-128-CBC + HMAC-SHA256) via `app/security/crypto.py`.
- **Proxy credential generation**: Unique Mindwall IMAP/SMTP proxy username and
  bcrypt-hashed password generated per mailbox.
- **Proxy password reveal**: Shown once at registration via session flash; never
  stored in plaintext.
- **Proxy password reset**: Re-generates proxy credentials without touching upstream.
- **MailboxProfile ORM model** with Alembic migration 0002.
- **ImapSecurity / SmtpSecurity enums**: `ssl`, `starttls`, `plain`.

---

## [0.1.0] — Phase 1: Foundation

### Added
- **FastAPI application factory** (`app/main.py`) with lifespan, structured logging,
  session middleware, static file serving, and a Jinja2 template engine.
- **Pydantic v2 settings** (`app/config.py`): All configuration from environment
  variables; validated at startup; `ENCRYPTION_KEY` format enforced.
- **PostgreSQL + SQLAlchemy 2.x async** (`app/db/`): Async engine, session factory,
  declarative Base with automatic `created_at`/`updated_at` timestamps.
- **Redis integration**: Session storage and future queue support.
- **Authentication**: Login/logout (`/auth/login`, `/auth/logout`), bcrypt password
  hashing, signed cookie sessions via `itsdangerous`.
- **RBAC**: `UserRole` enum (`user`, `admin`, `analyst`, `operator`). `require_admin`,
  `require_analyst` FastAPI dependencies.
- **Health endpoints**: `GET /health/live` (liveness), `GET /health/ready`
  (readiness — checks DB + Redis).
- **Users ORM model** with Alembic migration 0001.
- **Admin bootstrap script** (`scripts/create_admin.py`).
- **Structured logging** (`app/logging_config.py`) via `structlog`.
- **ManipulationDimension enum** and **Verdict enum** defined as stable constants.
