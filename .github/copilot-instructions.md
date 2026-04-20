# Mindwall — Copilot Instructions

## Read this first

You are working on **Mindwall**, a **fully self-hosted, privacy-first email security platform**.

Before making any code changes, read these files in this order:

1. `architecture.md` — product and system source of truth
2. `README.md` — setup and developer workflow
3. `pyproject.toml` — dependencies, tooling, Python version
4. the module you are about to edit and its related tests

If there is any conflict:
- the latest direct user instruction wins,
- then `architecture.md`,
- then this file,
- then local implementation details.

Do not invent product behavior when the architecture or user request already defines it.

---

## What Mindwall is

Mindwall is an **email firewall**.

It sits between users and their existing mail provider using a **local IMAP proxy** and **local SMTP proxy**. It inspects incoming mail, detects phishing and psychological manipulation, scores the message across **12 manipulation dimensions**, and then decides whether to:

- allow,
- allow with warning,
- soft hold,
- quarantine,
- or reject in gateway mode.

Inference must run **100% on-premises** using **Ollama** and a **locally running Llama 3.1 8B model**.

**Zero message data may leave the deployment boundary.**

This product is for privacy-sensitive and enterprise environments. Build it like production software, not a demo.

---

## Non-negotiable product constraints

These are hard requirements. Do not violate them unless the user explicitly changes them.

### Privacy and deployment
- No cloud AI APIs
- No SaaS dependency for message analysis
- No telemetry that sends email content, prompts, attachments, or headers externally
- No remote image, script, or asset loading in any mail preview or sandbox view
- Keep all inference local through Ollama

### Tech stack
- **Backend:** Python only
- **Web framework:** FastAPI
- **Templates:** Jinja2
- **Frontend:** HTML + Tailwind CSS CDN + minimal JavaScript
- **Do not introduce React, Next.js, Vue, Angular, Svelte, or a Node-based frontend build pipeline**
- Prefer server-rendered pages, progressive enhancement, and small focused JS modules

### Architecture shape
- Start as a **modular monolith** with clear module boundaries
- Keep services separable so the app can later split into dedicated workers/proxies if needed
- Favor simple, explicit, maintainable Python code over clever abstractions

### Security
- Encrypt upstream IMAP/SMTP credentials at rest
- Treat quarantined messages and attachments as sensitive data
- Sanitize all rendered HTML
- Never execute scripts, forms, macros, or remote content in previews
- Prefer least privilege and explicit access control

---

## Copilot behavior expectations

When working in agentic mode, behave like a senior engineer on a security-sensitive codebase.

### Always do this
1. Read relevant existing files before editing.
2. Make a short plan for non-trivial work.
3. Reuse existing patterns before creating new ones.
4. Keep changes small, coherent, and reviewable.
5. Add or update tests for any meaningful behavior change.
6. Update docs when behavior, setup, APIs, or architecture changes.
7. Prefer complete implementations over placeholders.
8. Leave the repo in a runnable state.

### Never do this
- Do not add mock/demo-only code into production paths.
- Do not add TODO-based fake implementations unless explicitly requested.
- Do not silently weaken security to make a feature easier.
- Do not bypass validation, encryption, or policy checks.
- Do not hardcode secrets, tokens, or passwords.
- Do not add new infrastructure such as Kafka, RabbitMQ, Elasticsearch, or a JS bundler unless the user explicitly asks.
- Do not replace local inference with hosted inference.

---

## Primary engineering objective

The goal is not just to make features appear to work. The goal is to build **a correct, secure, testable, and enterprise-ready system** aligned with `architecture.md`.

Every implementation should optimize for:
- correctness,
- security,
- observability,
- operational clarity,
- and future maintainability.

---

## Recommended repository shape

Unless the repository already defines a better structure, prefer this layout:

```text
mindwall/
  app/
    main.py
    config.py
    dependencies.py
    logging.py
    security/
    auth/
    users/
    mailboxes/
    proxies/
      imap/
      smtp/
    messages/
    analysis/
    policies/
    quarantine/
    alerts/
    admin/
    audit/
    db/
    templates/
    static/
  workers/
    analysis_worker.py
    llm_worker.py
    maintenance_worker.py
  tests/
    unit/
    integration/
    e2e/
  scripts/
  alembic/
  architecture.md
  README.md
  pyproject.toml
```

Keep domain boundaries clear. Avoid a giant `utils.py`, `helpers.py`, or `misc.py` dumping ground.

---

## Core modules and responsibilities

### `auth`
- login/logout/session handling
- admin authentication
- RBAC roles such as user, admin, analyst, operator
- password hashing and secure session handling

### `mailboxes`
- user mailbox registration
- upstream IMAP/SMTP configuration storage
- connection validation
- proxy identity generation
- credential encryption/decryption

### `proxies.imap`
- local IMAP proxy behavior
- upstream mailbox connection handling
- UID mapping
- filtered mailbox views
- quarantine folder virtualization or placeholder behavior

### `proxies.smtp`
- local SMTP proxy behavior
- upstream SMTP forwarding
- alert email routing
- future outbound inspection hooks

### `messages`
- RFC 5322 parsing
- MIME normalization
- HTML-to-safe-text extraction
- URL extraction
- attachment metadata and hashing
- header analysis support

### `analysis`
- deterministic security checks
- LLM prompt building
- model output parsing
- scoring orchestration
- fallback behavior when inference fails

### `policies`
- final decision engine
- thresholds
- allowlists/blocklists
- quarantine rules
- fail-open / fail-closed behavior

### `quarantine`
- encrypted message storage
- attachment storage
- safe preview generation
- release/delete workflows
- review timeline

### `alerts`
- admin notifications
- incident creation
- severity routing

### `audit`
- immutable or append-only audit events
- actor/action/timestamp/details structure

### `admin`
- dashboards
- queue health
- model health
- quarantine review UI
- policy editor
- system settings

---

## Implementation priorities

Build in this order unless the user asks otherwise.

### Phase 1 — Foundation
- project skeleton
- configuration system
- database models and migrations
- logging
- auth/session basics
- RBAC scaffolding
- health endpoints

### Phase 2 — Mailbox onboarding
- register upstream IMAP/SMTP config
- test upstream connectivity
- encrypt and store upstream credentials
- generate Mindwall proxy credentials
- show user proxy setup instructions in UI

### Phase 3 — Message ingestion
- RFC 5322 parsing
- body extraction
- HTML sanitization pipeline
- URL and attachment extraction
- header normalization
- message persistence

### Phase 4 — Analysis pipeline
- deterministic checks
- Ollama client integration
- strict structured LLM output
- 12-dimension score mapping
- combined policy verdicts

### Phase 5 — Enforcement
- IMAP filtered visibility
- quarantine storage and review UI
- release and delete actions
- admin alerting

### Phase 6 — Hardening
- background workers
- observability
- resilience/fallbacks
- integration tests
- deployment docs

Do not skip foundational security or persistence just to get UI screenshots quickly.

---

## Architectural rules you must preserve

### 1. Support two operating modes
The architecture defines two important modes:

#### Proxy mode
Users configure their mail client to use Mindwall’s IMAP/SMTP proxy.
- Incoming mail is analyzed when accessed through Mindwall.
- Mindwall can hide, flag, or virtually quarantine messages.
- Upstream mailbox remains system of record.

#### Gateway mode
Mindwall is deployed inline before final mailbox delivery.
- This is the true pre-delivery quarantine model.
- Keep the analysis and policy engine reusable for this mode.

Code should not paint the project into a corner where gateway mode becomes impossible.

### 2. IMAP proxy must stay lightweight
- Avoid synchronous heavy analysis in the critical request path when possible.
- Use cached verdicts and background processing where practical.
- Separate protocol handling from expensive model inference.

### 3. LLM analysis is one layer, not the whole system
Always combine model reasoning with deterministic security evidence:
- SPF/DKIM/DMARC results when available
- display-name vs reply-to mismatch
- suspicious links
- lookalike domains
- risky attachments
- credential/payment capture patterns
- forged or abnormal headers

### 4. Every verdict must be explainable
For quarantine-worthy decisions, preserve:
- technical signals
- per-dimension scores
- confidence
- human-readable rationale
- model/prompt version metadata when applicable

### 5. Degraded mode must still work
If Ollama fails or returns invalid output:
- do not crash the request path,
- degrade to deterministic checks,
- mark the verdict as degraded,
- and surface system health appropriately.

---

## The 12 manipulation dimensions

Treat these as first-class product concepts. They should have stable identifiers in code, clear labels in UI, and explicit tests.

Recommended default identifiers:
- `authority_pressure`
- `urgency_pressure`
- `scarcity`
- `fear_threat`
- `reward_lure`
- `curiosity_bait`
- `reciprocity_obligation`
- `social_proof`
- `secrecy_isolation`
- `impersonation`
- `compliance_escalation`
- `credential_or_payment_capture`

Use a centralized enum or constant mapping. Do not duplicate string literals everywhere.

---

## Python engineering standards

### Language and typing
- Target modern Python, preferably Python 3.12+
- Use type hints broadly
- Public functions and classes should have clear docstrings when non-obvious
- Prefer explicit data models with Pydantic v2 for request/response/config schemas

### Style
- Small focused modules
- Clear function names
- No deeply nested business logic in route handlers
- Move domain logic into services, not template routes
- Avoid overly magical decorators or metaprogramming
- Prefer composition over inheritance

### Error handling
- Raise domain-specific exceptions where helpful
- Convert exceptions into clear user-safe messages at boundaries
- Never expose secrets or raw tracebacks in templates
- Log enough detail for debugging without leaking sensitive data

### Concurrency
- Use `async` where it is actually useful, especially for IO-heavy services
- Keep CPU-heavy or long-running work out of request handlers
- Protect queues and shared resources with sensible backpressure

---

## Preferred libraries and tooling

Unless the repository already chose alternatives, prefer:

- **FastAPI** for web and internal APIs
- **Jinja2** for templates
- **SQLAlchemy 2.x** for ORM/data access
- **Alembic** for migrations
- **Pydantic v2** for schemas and settings
- **Redis** for caching/queues/rate limiting
- **httpx** for internal HTTP clients where needed
- **email** stdlib + focused parsing helpers for message parsing
- **pytest** for tests
- **ruff** for linting/formatting
- **mypy** for static checking where practical

Avoid bringing in heavy dependencies unless they materially simplify security or correctness.

---

## Configuration rules

Use a single, explicit configuration layer.

### Requirements
- environment-variable driven
- strongly typed settings
- separate dev/test/prod behavior only when necessary
- secure defaults
- no secrets in source control

### Expected config areas
- app settings
- database
- redis
- session/auth
- encryption keys
- ollama endpoint/model name
- proxy listener ports
- TLS settings
- storage paths
- feature flags

Do not scatter `os.getenv()` calls throughout the codebase.

---

## Data and persistence rules

### Database
Use PostgreSQL as the primary relational store.

Core entities should eventually include:
- users
- roles
- mailbox profiles
- proxy credentials
- messages
- message artifacts
- dimension scores
- verdicts
- quarantine items
- alerts
- audit events
- prompt versions
- model versions

### Persistence principles
- prefer normalized schema for control-plane data
- use indexed lookup keys for Message-ID, mailbox profile, and upstream UID
- preserve raw mail when needed for audit and release workflows
- separate raw blobs from derived safe previews

### Migrations
- all schema changes require Alembic migrations
- do not change models without migration files
- keep migrations deterministic and reversible where possible

---

## Credential and encryption rules

Mindwall stores upstream mail credentials. This is security-critical.

### Requirements
- encrypt upstream credentials at rest
- separate password hashing from encryption concerns
- proxy credentials should not reuse upstream credentials
- use dedicated encryption utilities and key management abstractions
- never log plaintext secrets
- redact secrets in admin UI and logs

If a secure crypto choice already exists in the repo, use it consistently. If not, add one carefully and document it.

---

## IMAP proxy implementation guidance

The IMAP proxy is a product-defining feature.

### Must support
- Mindwall credential authentication
- upstream mailbox lookup by Mindwall identity
- upstream connection lifecycle management
- UID/message mapping
- cached verdict-aware mailbox views
- quarantine-aware visibility logic

### Must not do
- do not tightly couple raw protocol handling with database models
- do not block protocol reads on avoidable heavy work
- do not mutate upstream mailbox content by default

### Design hint
Implement protocol parsing/handling, mailbox mapping, verdict lookup, and upstream transport as separate concerns.

---

## SMTP proxy implementation guidance

### Must support
- Mindwall credential authentication
- upstream SMTP relay using stored upstream settings
- secure forwarding
- local notification generation for alerts

### Later-friendly design
Keep extension points for:
- outbound anomaly detection
- compromised-account detection
- policy-based outbound enforcement

---

## Analysis engine guidance

The analysis layer should combine deterministic evidence and LLM reasoning.

### Deterministic checks should cover
- authentication signals
- display-name mismatch
- reply-to mismatch
- suspicious URL patterns
- link text mismatch
- risky file types
- credential/payment language signals
- forged or unusual headers

### LLM prompt requirements
- concise and structured
- include relevant extracted text and evidence
- request machine-readable JSON
- avoid free-form paragraphs as the primary output
- validate model output strictly

### LLM output rules
The result should include at least:
- overall risk score
- per-dimension scores
- rationale summary
- evidence list
- recommended action
- confidence

If the model output is malformed, retry in a strict schema mode once, then degrade safely.

---

## Policy engine guidance

The policy engine determines the final verdict.

### Inputs
- deterministic signals
- dimension scores
- overall risk
- confidence
- sender/user/tenant policy
- allowlists/blocklists
- system health/degraded state

### Outputs
Use stable verdict identifiers such as:
- `allow`
- `allow_with_banner`
- `soft_hold`
- `quarantine`
- `reject`
- `escalate_to_admin`

Keep policy rules configurable. Avoid hardcoding thresholds in scattered code.

---

## Quarantine and sandbox guidance

Quarantine must be secure and analyst-friendly.

### Requirements
- store raw messages and attachments encrypted at rest
- generate safe previews
- render sanitized HTML only
- block active content
- support release/delete/comment/audit actions
- preserve review history

### UI requirements
The quarantine detail page should make it easy to inspect:
- sender identity
- auth results
- subject
- extracted body text
- suspicious links
- attachment list
- dimension scores
- final verdict
- reasoning/evidence
- timeline of actions

---

## Web UI guidance

The product UI should feel like an internal security appliance, not a consumer app.

### General UI rules
- server-rendered first
- fast and readable
- accessible labels and keyboard-friendly forms
- calm, professional styling
- use Tailwind utility classes directly in templates
- use minimal JavaScript for filtering, polling, modals, and confirmations only when needed

### Do not introduce
- SPA routing
- client state management frameworks
- Node bundlers
- unnecessary animations

### Main pages to support
- login
- dashboard
- mailbox registration/edit
- proxy instructions page
- quarantine inbox
- quarantine detail
- alerts/incidents
- policy settings
- system/model health
- audit log viewer

---

## Logging, metrics, and observability

Mindwall needs enterprise-grade visibility.

### Logging
- structured logs
- correlation/request/message IDs
- redact secrets and sensitive fields
- clear severity levels

### Metrics
Track at least:
- request counts
- proxy connection counts
- message analysis counts
- queue depth
- model latency
- error rate
- quarantine rate
- release rate
- degraded-mode usage

### Health
Expose clear health/readiness checks for:
- app
- database
- redis
- ollama/model availability
- storage
- background workers

---

## Testing standards

This repository should be test-driven where practical and always test-backed for critical behavior.

### Required test types
- unit tests for core services and policy logic
- integration tests for database-backed flows
- integration tests for Ollama client behavior with mocked responses
- protocol-focused tests for IMAP/SMTP components where feasible
- route tests for critical admin flows

### High-priority test coverage
- upstream credential encryption/decryption
- mailbox registration validation
- LLM output parsing and fallback behavior
- policy decisions by threshold and evidence mix
- quarantine transitions
- RBAC enforcement
- sanitized preview rendering

### Rules
- every bug fix should include a regression test when possible
- do not remove tests to make builds pass
- prefer deterministic fixtures
- avoid network access in tests unless explicitly integration-scoped

---

## Quality gates before considering work done

Before considering a change complete, ensure the repo can pass or is updated to support:

- formatting/linting
- test suite
- type checks where configured
- migrations for schema changes
- updated documentation for new behavior

If build commands already exist, use them. If not, standardize around explicit commands and document them.

Suggested defaults if the repo does not yet define them:

```bash
ruff check .
ruff format .
pytest
```

Add `mypy` only if the repo is configured for it or the user asks to enforce it.

---

## Documentation standards

When you add or change meaningful behavior, update the relevant docs.

### Update when needed
- `README.md` for setup/run/test changes
- `architecture.md` for significant architectural changes
- inline docstrings for non-obvious public APIs
- example env files for new config keys

Prefer concise, high-value documentation over long generic prose.

---

## Definition of done for Mindwall work

A task is complete only when:
- the implementation follows `architecture.md`,
- security constraints are preserved,
- the code is coherent and maintainable,
- tests cover the critical behavior,
- docs are updated where necessary,
- and the change could realistically ship as part of an enterprise product.

---

## Final guidance for Copilot

Be strict about architecture, privacy, and security.

When in doubt:
- choose the simpler design,
- keep everything local,
- preserve explainability,
- and avoid introducing technology outside the agreed Python + Jinja + Tailwind CDN + JS stack.

Mindwall is not a generic mail app. It is a **privacy-first, self-hosted email security platform**. Every code change should reinforce that identity.