# Roadmap

This page describes the planned development phases beyond the current implementation.

---

## Current status

Phases 1–10 are complete. The following features are implemented and production-ready at MVP level:

- ✅ FastAPI web application with RBAC, session management, and structured logging
- ✅ PostgreSQL + Redis stack with full async support
- ✅ Mailbox registration with upstream credential encryption
- ✅ RFC 5322 message parsing, HTML sanitization, URL extraction
- ✅ Deterministic security checks (10 rule-based checks)
- ✅ Ollama LLM integration with 12-dimension scoring and graceful degradation
- ✅ Policy verdict engine with configurable thresholds
- ✅ Quarantine workflow with review, release, delete, and audit trail
- ✅ Admin UI: dashboard, quarantine, alerts, policy editor, audit log, model health
- ✅ Upstream IMAP sync with mailbox virtualization
- ✅ Read-only IMAP proxy (RFC 3501 subset)
- ✅ SMTP submission proxy with capture and relay delivery modes
- ✅ Full Docker Compose stack with five services
- ✅ 577 automated tests

---

## Phase 11: Hardening and STARTTLS

**Priority: High**

- **STARTTLS for IMAP proxy.** Allow mail clients to upgrade connections from plaintext to TLS within the same connection.
- **STARTTLS for SMTP proxy.** Same for outbound submission.
- **TLS certificate management.** Self-signed or ACME (Let's Encrypt) support for the proxy listeners.
- **Outbound inspection.** Run the manipulation scoring pipeline on submitted outbound messages to detect compromised-account behavior.
- **Connection rate limiting.** Prevent brute-force attacks on proxy authentication.
- **Automated integration tests.** Spin up a local IMAP/SMTP sandbox (e.g. Greenmail) and run end-to-end tests without the live Docker stack.

---

## Phase 12: Background workers and observability

**Priority: High**

- **Automatic sync worker.** A persistent background process that syncs all active mailbox profiles on a configurable schedule (default: every 5 minutes).
- **Queue-backed analysis.** Move analysis off the sync code path and onto a Redis-backed queue with a dedicated worker process.
- **Prometheus metrics.** Expose a `/metrics` endpoint with counters for sync events, quarantine rate, analysis latency, model latency, error rate, and degraded-mode usage.
- **Health endpoint upgrades.** Expose Ollama availability, background worker heartbeat, and queue depth in `/health/ready`.
- **Structured audit events.** Extend audit coverage to mailbox registration, user management, and policy changes.

---

## Phase 13: Gateway mode

**Priority: Medium**

- **MTA-level integration.** Deploy Mindwall as an SMTP relay in the mail delivery path (before final mailbox delivery), enabling true pre-delivery quarantine without an IMAP sync dependency.
- **Reject verdict enforcement.** In gateway mode, `reject` verdicts refuse the message at the SMTP layer (sending a `550` response to the upstream MTA).
- **Postfix/Exim integration guide.** Documentation for deploying Mindwall as a milter or transport map target.

---

## Phase 14: Multi-tenancy and user self-service

**Priority: Medium**

- **User self-service portal.** Let non-admin users register and manage their own mailboxes without admin intervention.
- **Per-user policy overrides.** Allow users to adjust their own phishing sensitivity thresholds within admin-defined bounds.
- **User-submitted false positive/negative reports.** Route feedback into the analysis improvement workflow.
- **Allowlists and blocklists.** Per-user and global sender/domain allowlists and blocklists.

---

## Phase 15: Advanced analysis

**Priority: Low (research)**

- **Attachment sandboxing.** Static analysis or sandboxed execution for risky attachment types.
- **URL reputation.** Lookup extracted URLs against a local reputation database (no external API calls).
- **Header forgery detection.** Deeper analysis of `Received:` header chains for injection and relay manipulation.
- **Model fine-tuning support.** Infrastructure for running fine-tuned models specific to the phishing detection domain.
- **Prompt versioning.** Full version tracking for prompts with A/B comparison support.

---

## Deferred items (no current phase assignment)

- IMAP IDLE support (push-based upstream notifications)
- Multi-folder sync (folders other than INBOX)
- Bulk quarantine operations (select all, bulk release/delete)
- Export / SIEM integration (syslog, webhook)
- SAML/OIDC SSO for admin login
- API-first mode (machine-readable JSON API for all admin operations)
- Horizontal scaling (shared-nothing worker architecture with Redis coordination)

---

## Contribution priorities

If you want to contribute, the highest-impact areas currently are:

1. STARTTLS for the IMAP and SMTP proxies
2. Automatic background sync worker
3. Prometheus metrics endpoint
4. Additional deterministic check rules
5. Integration test suite using a local IMAP/SMTP sandbox

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.
