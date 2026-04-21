# Architecture overview

Mindwall is a modular Python monolith structured for eventual service separation. This document provides a map of the system components and how they interact.

For the full detailed design, see [architecture.md](../architecture.md).

---

## System shape

```
Mail client
    │
    ├── IMAP (port 1993/1143) ──→ IMAP Proxy ──→ Virtual mailbox view
    │                               │
    │                               └─ Mindwall DB (MailboxItem, Message, AnalysisRun)
    │
    └── SMTP (port 1587) ────────→ SMTP Proxy ──→ Capture (.eml) / Relay upstream
                                        │
                                        └─ Mindwall DB (OutboundMessage)

Web browser ──────────────────────────→ FastAPI app (port 8000)
                                            │
                                            ├── Admin UI (quarantine, alerts, policy, audit)
                                            ├── Mailbox registration
                                            └── Message Lab

Background (manual trigger) ──────────→ IMAP Sync ──→ Upstream IMAP ──→ Ingest → Analyze
                                                                            │
                                                                    Ollama (local LLM)
```

---

## Components

### FastAPI application (`app/`)

The core web process. Handles:
- Admin UI (Jinja2 + Tailwind CDN)
- Mailbox registration and management
- Quarantine review workflow
- Alerts and audit log
- Policy editor
- Message Lab
- Health endpoints

### IMAP proxy (`workers/imap_proxy.py`, `app/proxies/imap/`)

A standalone asyncio process implementing a read-only RFC 3501 subset.

- Authenticates with Mindwall proxy credentials
- Presents `INBOX` and `Mindwall/Quarantine` virtual folders
- Returns messages from the local Mindwall store (not from upstream)
- Each TCP connection is handled in its own asyncio task

### SMTP proxy (`workers/smtp_proxy.py`, `app/proxies/smtp/`)

A standalone asyncio process implementing RFC 5321 SMTP submission.

- Authenticates with Mindwall proxy credentials
- Accepts outbound mail via `AUTH PLAIN` or `AUTH LOGIN`
- Captures submissions locally or relays them to upstream via stored credentials
- Each TCP connection is handled in its own asyncio task

### Analysis engine (`app/analysis/`)

Two-stage message analysis:

1. **Deterministic checks** — pure-Python rules, no network calls
2. **LLM analysis** — structured prompt to local Ollama, strict JSON response

Output: risk score, 12 per-dimension scores, verdict, confidence, rationale.

### Policy engine (`app/policies/verdict.py`)

Converts the combined risk score and confidence into a final verdict using configurable thresholds. Supports degraded mode (lower confidence → risk adjustment) and gateway mode (`reject` verdict available).

### Quarantine (`app/quarantine/`)

Encrypted blob storage and state-machine workflow for quarantined messages. Every transition is logged to the append-only audit trail.

### IMAP sync (`app/mailboxes/sync_service.py`)

Pulls new messages from upstream IMAP servers into the local store. Triggered manually from the admin UI. Idempotent — safe to re-run. Handles UIDVALIDITY resets.

---

## Data flow (incoming message)

```
1. Admin triggers sync at /admin/mailboxes/{id}/sync
2. sync_service: decrypt upstream credentials (Fernet)
3. UpstreamImapClient: connect to upstream IMAP server
4. Fetch new UIDs since last checkpoint
5. For each UID:
   a. Fetch raw RFC 5322 bytes
   b. Parse (app/messages/parser.py)
   c. Store raw .eml (SHA-256 addressed)
   d. Persist Message, MessageUrl, MessageAttachment rows
   e. Run deterministic checks
   f. Call Ollama (if enabled) → parse → validate → fallback if needed
   g. Combine scores → compute verdict
   h. Persist AnalysisRun + DimensionScore rows
   i. If verdict requires quarantine → create QuarantineItem + AuditEvent + Alert
   j. Create MailboxItem with appropriate visibility
6. Update MailboxSyncState checkpoint
```

---

## Database schema (summary)

| Table | Description |
|-------|-------------|
| `users` | Admin, analyst, operator, and user accounts |
| `mailbox_profiles` | Registered upstream IMAP/SMTP configs (credentials encrypted) |
| `messages` | Parsed message records |
| `message_urls` | Extracted URLs per message |
| `message_attachments` | Attachment metadata per message |
| `analysis_runs` | One per analysis pipeline run |
| `dimension_scores` | 12 rows per analysis run |
| `quarantine_items` | Quarantined messages with review state |
| `audit_events` | Append-only audit trail |
| `mailbox_sync_states` | Per-folder sync checkpoints |
| `mailbox_items` | Maps upstream UIDs to local messages with visibility state |
| `policy_settings` | Runtime-editable policy overrides |
| `alerts` | Security alert records |
| `outbound_messages` | SMTP proxy submission records |

---

## Module map

```
app/
├── admin/          Admin routes and dashboard
├── alerts/         Alert model, service, lifecycle
├── analysis/       Deterministic checks, Ollama client, orchestrator
├── audit/          Audit event infrastructure
├── auth/           Login, session, RBAC dependencies
├── db/             SQLAlchemy Base, session factory
├── health/         /health/live and /health/ready
├── mailboxes/      Registration, sync, view service, sync router
├── messages/       Parser, HTML sanitizer, URL extractor, storage, ingestion
├── policies/       ManipulationDimension enum, Verdict enum, verdict engine, policy settings
├── proxies/
│   ├── imap/       IMAP server, session auth, mailbox adapter, upstream client
│   └── smtp/       SMTP server, session auth, relay, delivery, outbound model
├── quarantine/     Quarantine service, blob storage, review workflow
├── security/       Fernet encryption utilities
├── templates/      Jinja2 HTML templates
└── users/          User model and UserRole enum
```

---

## Operating modes

### Proxy mode (current default)

Users point their mail client at Mindwall. Mindwall syncs from upstream, analyzes incoming mail, and presents a filtered view. The upstream mailbox remains the system of record.

### Gateway mode (planned / partial)

Mindwall is deployed inline before final mailbox delivery. `reject` verdicts become available. The `GATEWAY_MODE=true` flag enables this; the verdict engine and policy engine are already gateway-aware.

---

## Privacy guarantees enforced in code

- `app/analysis/ollama_client.py`: Refuses to connect to non-localhost endpoints.
- No external HTTP calls with message content anywhere in the codebase.
- HTML templates strip all remote resources before rendering message content.
- Credentials are decrypted in memory only; never logged.
