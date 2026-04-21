# Mindwall

**Self-hosted email security platform. Privacy first. No cloud dependencies.**

Mindwall is a local IMAP/SMTP proxy and analysis engine that sits between your mail client and your upstream mail provider. Every incoming message is inspected, scored across **12 psychological manipulation dimensions**, and either delivered, flagged, or quarantined — without any message content leaving your deployment boundary.

> **Inference runs 100% on-premises** via [Ollama](https://ollama.com) + Llama 3.1 8B.  
> No cloud APIs. No SaaS dependencies. No telemetry.

**Source-available. Non-commercial use only.** See [LICENSE](LICENSE).

---

## Overview

| | |
|---|---|
| **Language** | Python 3.11+ |
| **Framework** | FastAPI + Jinja2 |
| **Database** | PostgreSQL 16 |
| **Cache / queues** | Redis 7 |
| **Inference** | Ollama (local, Llama 3.1 8B) |
| **Status** | Beta — actively developed |
| **License** | PolyForm Noncommercial 1.0.0 |

---

## What it does

1. **Proxies your mail.** Users configure their mail client to connect to Mindwall's local IMAP (port 1993) and SMTP (port 1587) proxies instead of their upstream mail server.
2. **Inspects every message.** Incoming mail is pulled from upstream via background sync, parsed (RFC 5322), and run through both deterministic security checks and a local LLM.
3. **Scores 12 manipulation dimensions.** Each message receives per-dimension scores covering authority pressure, urgency, fear, impersonation, credential/payment capture, and eight others.
4. **Enforces a policy verdict.** The combined risk score determines whether the message is `allow`, `allow_with_banner`, `soft_hold`, `quarantine`, or `escalate_to_admin`.
5. **Filters your inbox.** The IMAP proxy presents only cleared messages in `INBOX`; quarantined messages appear in `Mindwall/Quarantine`.
6. **Gives admins full visibility.** A web-based admin UI covers quarantine review, alerts, audit log, policy editor, sync status, model health, and outbound message inspection.

### Key design constraints

- **Zero external data egress.** Message bodies, headers, attachments, and LLM prompts never leave the server.
- **No cloud APIs.** Inference is served by a locally running Ollama instance.
- **Explainable verdicts.** Every quarantine decision includes the technical signals, per-dimension scores, model reasoning, and a complete audit trail.
- **Fail-safe degradation.** If Ollama is unreachable, the system falls back to deterministic-only analysis and marks the verdict as degraded. The request path does not crash.

---

## Implementation status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: config, auth, RBAC, health endpoints, DB/Redis wiring | ✅ Complete |
| 2 | Mailbox onboarding: credential encryption, proxy setup UI | ✅ Complete |
| 3 | Message ingestion: RFC 5322 parsing, HTML sanitization, URL extraction, Message Lab | ✅ Complete |
| 4 | Analysis engine: deterministic checks + Ollama LLM + 12-dimension scoring | ✅ Complete |
| 5 | Enforcement: quarantine storage, review UI, release/delete, audit trail | ✅ Complete |
| 6 | IMAP sync: background pull from upstream mailboxes, mailbox virtualization | ✅ Complete |
| 7 | Admin surfaces: policy editor, alerts, audit log, model health, mailbox admin | ✅ Complete |
| 8 | Docker stack: full Compose setup, entrypoint, migration automation | ✅ Complete |
| 9 | IMAP proxy MVP: RFC 3501 subset, virtual folders, credential auth | ✅ Complete |
| 10 | SMTP proxy MVP: AUTH PLAIN/LOGIN, capture/relay delivery, outbound admin UI | ✅ Complete |
| 11 | Outbound inspection, STARTTLS, gateway mode, hardening | Planned |

---

## Quick start (Docker)

### Prerequisites

- Docker Engine 24+ and Docker Compose v2
- 4 GB RAM minimum (8 GB recommended when Ollama is enabled)
- Ollama installed separately if you want LLM analysis (see [docs/installation/docker.md](docs/installation/docker.md))

### 1. Clone and configure

```bash
git clone <repo-url>
cd mindwall
cp .env.example .env.docker
```

Edit `.env.docker` and set the three required values:

```bash
# Generate a session signing key (min 32 chars)
python -c "import secrets; print(secrets.token_hex(32))"

# Generate a Fernet encryption key for credential storage
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Minimum `.env.docker` values:

```env
SECRET_KEY=<output from first command>
ENCRYPTION_KEY=<output from second command>
MINDWALL_ADMIN_EMAIL=admin@example.com
MINDWALL_ADMIN_PASSWORD=change-me-immediately
```

### 2. Start the stack

```bash
docker compose --env-file .env.docker up -d
```

This starts five services:

| Container | Purpose | Host port |
|-----------|---------|-----------|
| `mindwall_app` | FastAPI web application + admin UI | 8000 |
| `mindwall_db` | PostgreSQL 16 | — (internal) |
| `mindwall_redis` | Redis 7 | — (internal) |
| `mindwall_imap_proxy` | IMAP proxy server | 1143 |
| `mindwall_smtp_proxy` | SMTP submission proxy | 1587 |

### 3. Open the admin UI

```
http://localhost:8000
```

Log in with the email and password you set in `.env.docker`.

### Useful Docker commands

```bash
# View running services
docker compose --env-file .env.docker ps

# Follow application logs
docker compose --env-file .env.docker logs -f app

# Run database migrations manually
docker exec mindwall_app python -m alembic upgrade head

# Check current migration revision
docker exec mindwall_app python -m alembic current

# Full reset (destroys all data)
docker compose --env-file .env.docker down -v
```

---

## Quick start (local development)

### Prerequisites

| Dependency | Version |
|-----------|---------|
| Python | 3.11+ |
| PostgreSQL | 15+ |
| Redis | 7+ |
| Ollama | Latest (optional — required for LLM analysis) |

### 1. Set up Python environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
```

Minimum `.env` values (all others have safe defaults):

```env
SECRET_KEY=<at-least-32-char-random-string>
ENCRYPTION_KEY=<fernet-key>
DATABASE_URL=postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall
REDIS_URL=redis://localhost:6379/0
```

### 3. Start dependencies

```bash
# PostgreSQL
docker run -d --name mindwall-pg \
  -e POSTGRES_USER=mindwall \
  -e POSTGRES_PASSWORD=mindwall \
  -e POSTGRES_DB=mindwall \
  -p 5432:5432 postgres:16-alpine

# Redis
docker run -d --name mindwall-redis \
  -p 6379:6379 redis:7-alpine
```

### 4. Run migrations and seed admin

```bash
alembic upgrade head
python scripts/create_admin.py
```

### 5. Start the application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 6. Start the proxies (optional)

```bash
# IMAP proxy (separate terminal)
python workers/imap_proxy.py

# SMTP submission proxy (separate terminal)
python workers/smtp_proxy.py
```

---

## Mailbox onboarding

Users register their upstream mailbox at `/mailboxes/new`.

1. Enter upstream IMAP server details (host, port, username, password, security mode).
2. Enter upstream SMTP server details.
3. Mindwall validates connectivity, encrypts your credentials with Fernet (AES-128-CBC + HMAC-SHA256), and generates unique **Mindwall proxy credentials**.
4. The proxy password is shown **once** on the detail page — copy and save it before navigating away. It cannot be recovered; use **Reset proxy password** if lost.
5. Configure your mail client:

| Setting | Value |
|---------|-------|
| Incoming server | `IMAP_PROXY_DISPLAY_HOST` (default: `127.0.0.1`) |
| Incoming port | `IMAP_PROXY_PORT` (default: `1993`; Docker dev: `1143`) |
| Outgoing server | `SMTP_PROXY_DISPLAY_HOST` (default: `127.0.0.1`) |
| Outgoing port | `SMTP_PROXY_PORT` (default: `1587`) |
| Username | Your Mindwall proxy username (shown on the mailbox detail page) |
| Password | Your Mindwall proxy password (shown once at registration) |
| Security | None (use a VPN or local-only network; STARTTLS is planned) |

See [docs/mailbox-onboarding.md](docs/mailbox-onboarding.md) for the full workflow.

---

## IMAP proxy

Mindwall's IMAP proxy authenticates with Mindwall proxy credentials and presents a filtered, read-only view of the local message store.

### Virtual folders

| Folder | Contents |
|--------|----------|
| `INBOX` | Messages with `VISIBLE` status (cleared by analysis) |
| `Mindwall/Quarantine` | Messages with `QUARANTINED` or `HIDDEN` status |

### Supported commands

`CAPABILITY`, `NOOP`, `LOGOUT`, `LOGIN`, `LIST`, `SELECT`, `EXAMINE`, `STATUS`, `UID SEARCH`, `UID FETCH`, `FETCH`, `SEARCH`, `CLOSE`

### Mutation commands

Write operations (`STORE`, `COPY`, `APPEND`, `EXPUNGE`, `CREATE`, `DELETE`, `RENAME`, etc.) return `NO [CANNOT]`. This proxy is **read-only** in the current release.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAP_PROXY_HOST` | `0.0.0.0` | Bind address |
| `IMAP_PROXY_PORT` | `1993` | TCP listen port |
| `IMAP_PROXY_DISPLAY_HOST` | `127.0.0.1` | Host shown in proxy setup instructions |

See [docs/imap-proxy.md](docs/imap-proxy.md) for full protocol details and verification steps.

---

## SMTP proxy

Mindwall's SMTP proxy authenticates outbound mail submissions with Mindwall proxy credentials and either captures or relays the message.

### Delivery modes

| Mode | Behaviour |
|------|-----------|
| `capture` | Stores the raw `.eml` and metadata locally; nothing leaves the server |
| `relay` | Forwards the message to the upstream SMTP server using stored encrypted credentials |

### Supported commands

`EHLO`, `HELO`, `AUTH PLAIN`, `AUTH LOGIN`, `MAIL FROM`, `RCPT TO`, `DATA`, `RSET`, `NOOP`, `QUIT`

Unsupported commands return `502 5.5.1 Command not implemented`. STARTTLS is deferred.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_PROXY_HOST` | `0.0.0.0` | Bind address |
| `SMTP_PROXY_PORT` | `1587` | TCP listen port |
| `SMTP_PROXY_DISPLAY_HOST` | `127.0.0.1` | Host shown in proxy setup instructions |
| `SMTP_DELIVERY_MODE` | `capture` | `capture` or `relay` |
| `SMTP_RELAY_TIMEOUT_SECONDS` | `30` | Upstream relay connection timeout |
| `SMTP_MAX_MESSAGE_BYTES` | `26214400` | Maximum message size (25 MB) |
| `OUTBOUND_MESSAGE_STORE_PATH` | `./data/outbound_messages` | Captured `.eml` storage |

See [docs/smtp-proxy.md](docs/smtp-proxy.md) for full details.

---

## Configuration reference

All settings are environment-variable driven. The full list is in [docs/configuration.md](docs/configuration.md) and [`.env.example`](.env.example).

### Required settings

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Session cookie signing key (minimum 32 characters) |
| `ENCRYPTION_KEY` | Fernet key for encrypting upstream credentials at rest |

### Key optional settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall` | Async PostgreSQL connection URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `DEBUG` | `false` | Enable debug logs; exposes `/api/docs` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | Model to use for analysis |
| `LLM_ENABLED` | `true` | Set to `false` for deterministic-only analysis |
| `ANALYSIS_ENABLED` | `true` | Set to `false` to disable all analysis |
| `GATEWAY_MODE` | `false` | Enable pre-delivery inline gateway mode |
| `BLOB_STORAGE_PATH` | `./data/blobs` | Encrypted blob storage root |
| `RAW_MESSAGE_STORE_PATH` | `./data/raw_messages` | Raw `.eml` storage root |

---

## Admin UI

All admin pages require the `admin` role. The UI is server-rendered (Jinja2 + Tailwind CDN).

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/admin/` | Pending quarantine count, open alerts, mailbox count |
| Quarantine inbox | `/admin/quarantine/` | Filterable quarantine review queue |
| Quarantine detail | `/admin/quarantine/{id}` | Full message detail, scores, evidence, audit timeline |
| Alerts | `/admin/alerts/` | Open/acknowledged/resolved alerts |
| Alert detail | `/admin/alerts/{id}` | Alert triage: acknowledge, resolve, add note |
| Audit log | `/admin/audit/` | Paginated append-only audit event log |
| Policy editor | `/admin/policy/` | Edit runtime policy thresholds and flags |
| Model health | `/admin/health/model` | Ollama connectivity and model availability |
| Mailboxes | `/admin/mailboxes/` | All registered mailbox profiles |
| Mailbox sync | `/admin/mailboxes/{id}/sync` | Sync status, trigger, per-folder checkpoints |
| Mailbox inbox | `/admin/mailboxes/{id}/inbox` | Mindwall-visible messages for a mailbox |
| Mailbox quarantine | `/admin/mailboxes/{id}/quarantine` | Quarantined messages for a mailbox |
| Message Lab | `/admin/messages/` | Upload and inspect `.eml` files (enabled by default) |
| Outbound messages | `/admin/outbound/` | Captured SMTP submissions list |
| Outbound detail | `/admin/outbound/{id}` | Submission metadata and storage path |

---

## Analysis engine

Each message is analyzed in two stages:

### 1. Deterministic checks

Pure-Python rules with no network calls:

- SPF/DKIM/DMARC signal analysis
- Display-name vs. From-address mismatch
- Reply-To mismatch
- Brand impersonation patterns
- Link-text/href mismatch
- Suspicious URL patterns (IP hosts, deep subdomains, credential keywords)
- Risky attachment types (`.exe`, `.ps1`, `.docm`, macros, etc.)
- Credential/payment/urgency/fear language detection
- HTML-only body detection

### 2. LLM analysis (Ollama)

A structured prompt is built from the message envelope, auth signals, body, URLs, and deterministic evidence. The model returns a strict JSON response with:

- Overall risk score `[0.0, 1.0]`
- Per-dimension scores for all 12 dimensions
- Rationale summary
- Evidence list
- Recommended action
- Confidence

If the model output is malformed, the system retries once with a stricter prompt, then degrades safely.

### The 12 manipulation dimensions

| Identifier | Description |
|-----------|-------------|
| `authority_pressure` | Abuse of authority or official-sounding sender |
| `urgency_pressure` | Artificial time pressure |
| `scarcity` | False scarcity or limited-availability claims |
| `fear_threat` | Fear-based coercion or threat language |
| `reward_lure` | Promises of unexpected rewards or prizes |
| `curiosity_bait` | Clickbait or information-withholding hooks |
| `reciprocity_obligation` | Manufactured feelings of obligation |
| `social_proof` | False consensus or social validation |
| `secrecy_isolation` | Requests to keep communication secret |
| `impersonation` | Impersonation of individuals or organizations |
| `compliance_escalation` | Incremental compliance requests |
| `credential_or_payment_capture` | Requests for credentials, payment, or PII |

### Verdict thresholds (configurable)

| Verdict | Default risk threshold |
|---------|----------------------|
| `allow` | ≤ 0.25 |
| `allow_with_banner` | ≤ 0.45 |
| `soft_hold` | ≤ 0.65 |
| `quarantine` | ≤ 0.85 |
| `escalate_to_admin` | > 0.85 |
| `reject` | Gateway mode only |

---

## Health endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health/live` | None | Liveness: 200 if the process is alive |
| `GET /health/ready` | None | Readiness: 200 if DB + Redis reachable; 503 if degraded |

---

## Testing

```bash
# Run all tests
pytest

# With coverage report
pytest --cov=app --cov-report=term-missing

# Specific test module
pytest tests/unit/test_verdict.py -v
```

Tests use an in-memory SQLite database. PostgreSQL and Redis are not required for the unit or integration test suites.

```bash
# Integration verification scripts (require running Docker stack)
python scripts/verify_imap_proxy.py   # 17 assertions
python scripts/verify_smtp_proxy.py   # 22 assertions
python scripts/smoke_test.py          # Basic HTTP smoke test
```

---

## Linting and formatting

```bash
# Lint
ruff check .

# Format
ruff format .

# Lint + autofix
ruff check . --fix && ruff format .
```

---

## Project structure

```
mindwall/
├── app/
│   ├── main.py                  # FastAPI app factory, lifespan, middleware
│   ├── config.py                # Pydantic settings (env-driven, cached)
│   ├── dependencies.py          # Shared FastAPI dependencies
│   ├── logging_config.py        # Structured logging via structlog
│   ├── admin/                   # Admin UI routes + dashboard
│   ├── alerts/                  # Alert models, service, routing
│   ├── analysis/                # Deterministic checks, Ollama client, orchestrator
│   ├── audit/                   # Append-only audit event infrastructure
│   ├── auth/                    # Login/logout, session, RBAC
│   ├── db/                      # SQLAlchemy Base, session factory
│   ├── health/                  # /health/live and /health/ready
│   ├── mailboxes/               # Mailbox registration, sync, view service
│   ├── messages/                # RFC 5322 parser, HTML sanitizer, URL extractor
│   ├── policies/                # ManipulationDimension enum, verdict engine, policy settings
│   ├── proxies/
│   │   ├── imap/                # IMAP proxy: server, session, mailbox adapter, upstream client
│   │   └── smtp/                # SMTP proxy: server, session, relay, delivery, outbound model
│   ├── quarantine/              # Quarantine storage, review workflow, audit
│   ├── security/                # Fernet credential encryption
│   ├── templates/               # Jinja2 templates (Tailwind CDN)
│   └── users/                   # User ORM model, UserRole enum
├── workers/
│   ├── imap_proxy.py            # IMAP proxy entrypoint
│   ├── smtp_proxy.py            # SMTP proxy entrypoint
│   ├── analysis_worker.py       # Background analysis worker (planned)
│   ├── llm_worker.py            # LLM worker (planned)
│   └── maintenance_worker.py    # Maintenance worker (planned)
├── tests/
│   ├── conftest.py              # Shared fixtures (in-memory SQLite, test client)
│   ├── unit/                    # Pure unit tests
│   └── integration/             # HTTP + DB integration tests
├── alembic/                     # Alembic migrations (0001–0009)
├── scripts/
│   ├── create_admin.py          # Bootstrap first admin user
│   ├── seed_imap_dev.py         # Seed dev IMAP credentials
│   ├── entrypoint.sh            # Docker entrypoint (migrate + start)
│   ├── verify_imap_proxy.py     # IMAP proxy verification (17 checks)
│   └── verify_smtp_proxy.py     # SMTP proxy verification (22 checks)
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
└── architecture.md
```

---

## Database migrations

Mindwall uses Alembic for schema management. All migrations are in `alembic/versions/`.

| Migration | Description |
|-----------|-------------|
| `0001_create_users` | Users table, UserRole enum |
| `0002_create_mailbox_profiles` | Mailbox profiles, upstream credentials |
| `0003_create_messages` | Messages, URLs, attachments |
| `0004_create_analysis` | AnalysisRun, DimensionScore |
| `0005_create_quarantine_audit` | QuarantineItem, AuditEvent |
| `0006_create_sync_tables` | MailboxSyncState, MailboxItem |
| `0007_create_policy_settings_alerts` | PolicySetting, Alert |
| `0008_create_outbound_messages` | OutboundMessage (SMTP proxy) |
| `0009_fix_base_timestamps` | Corrective: adds missing timestamp columns |

```bash
# Apply all pending migrations
alembic upgrade head

# Check current revision
alembic current

# Show migration history
alembic history
```

---

## Security

- **Credential encryption.** Upstream IMAP/SMTP passwords are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256). The `ENCRYPTION_KEY` must be a valid Fernet key; the application refuses to start if it is missing or malformed.
- **Proxy password hashing.** Mindwall proxy passwords are stored only as bcrypt hashes. They cannot be recovered — use the "Reset proxy password" UI action if lost.
- **Session security.** Cookies are signed with `SECRET_KEY`, flagged `HttpOnly` and `SameSite=Lax`. Secure-only cookies are enforced when `DEBUG=false`.
- **No external data egress.** The Ollama client enforces localhost-only connections at the code level. Message content is never sent to any external service.
- **OpenAPI docs.** The `/api/docs` endpoint is only exposed when `DEBUG=true`.
- **HTML rendering.** All message HTML is sanitized before rendering. Scripts, forms, remote images, and active content are stripped.

See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy.

---

## Documentation

Full documentation is in the [`docs/`](docs/) directory:

| Document | Description |
|----------|-------------|
| [Getting started](docs/getting-started.md) | First steps for new users |
| [Local installation](docs/installation/local.md) | Full local dev setup |
| [Docker installation](docs/installation/docker.md) | Full Docker setup with Ollama |
| [Configuration](docs/configuration.md) | Every environment variable explained |
| [Architecture overview](docs/architecture-overview.md) | System design and components |
| [Admin guide](docs/admin-guide.md) | Admin UI workflow and daily operations |
| [Mailbox onboarding](docs/mailbox-onboarding.md) | Registering mailboxes and configuring mail clients |
| [Analysis engine](docs/analysis-engine.md) | Deterministic checks, LLM pipeline, scoring |
| [Policy engine](docs/policy-engine.md) | Verdict logic, thresholds, gateway mode |
| [Quarantine workflow](docs/quarantine-workflow.md) | Review, release, and delete |
| [Alerts and audit](docs/alerts-and-audit.md) | Alert triage and audit log |
| [Mailbox sync](docs/mailbox-sync.md) | Upstream IMAP sync internals |
| [IMAP proxy](docs/imap-proxy.md) | Protocol subset, virtual folders, client setup |
| [SMTP proxy](docs/smtp-proxy.md) | Submission proxy, delivery modes, client setup |
| [Message Lab](docs/message-lab.md) | Admin tool for manual message inspection |
| [Database and migrations](docs/database-and-migrations.md) | Schema, Alembic workflow |
| [Testing](docs/testing.md) | Test suite, fixtures, verification scripts |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and diagnostics |
| [Roadmap](docs/roadmap.md) | Planned phases and future work |

---

## Contributing

Mindwall is **source-available** under the [PolyForm Noncommercial 1.0.0](LICENSE) license. Commercial use is not permitted.

Before contributing, read [CONTRIBUTING.md](CONTRIBUTING.md). Key points:

- Keep all changes compatible with the non-commercial license.
- Follow the coding standards described in `.github/copilot-instructions.md`.
- Run `ruff check .` and `pytest` before submitting.
- All security-relevant changes require a clear rationale.

---

## License

Mindwall is released under the **PolyForm Noncommercial 1.0.0** license.

You are free to use, study, and modify this software for non-commercial purposes. Commercial use of any kind requires explicit written permission from the copyright holder.

See [LICENSE](LICENSE) for the full license text.
