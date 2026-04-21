# Mindwall Documentation

Welcome to the Mindwall documentation. Mindwall is a self-hosted, privacy-first email security platform that inspects, scores, and enforces policy on incoming mail — entirely on-premises.

---

## Documentation index

### Getting started

- [Getting started](getting-started.md) — first steps for new users
- [Local installation](installation/local.md) — full local development setup
- [Docker installation](installation/docker.md) — production-ready Docker stack

### Reference

- [Configuration](configuration.md) — every environment variable documented
- [Architecture overview](architecture-overview.md) — system design and component map

### Operations

- [Admin guide](admin-guide.md) — day-to-day admin UI workflow
- [Mailbox onboarding](mailbox-onboarding.md) — registering mailboxes and configuring mail clients
- [Mailbox sync](mailbox-sync.md) — upstream IMAP sync internals
- [Quarantine workflow](quarantine-workflow.md) — review, release, and delete
- [Alerts and audit](alerts-and-audit.md) — alert triage and the audit log

### Proxies

- [IMAP proxy](imap-proxy.md) — read-only IMAP proxy, virtual folders, supported commands
- [SMTP proxy](smtp-proxy.md) — SMTP submission proxy, delivery modes, client setup

### Analysis

- [Analysis engine](analysis-engine.md) — deterministic checks, LLM pipeline, 12-dimension scoring
- [Policy engine](policy-engine.md) — verdict logic, thresholds, gateway mode
- [Message Lab](message-lab.md) — admin tool for manual message inspection

### Developer

- [Database and migrations](database-and-migrations.md) — schema overview, Alembic workflow
- [Testing](testing.md) — test suite structure, fixtures, verification scripts
- [Troubleshooting](troubleshooting.md) — common issues and diagnostics
- [Roadmap](roadmap.md) — planned phases and future work

---

## Project links

- [README](../README.md) — project overview
- [Architecture](../architecture.md) — full system architecture document
- [Changelog](../CHANGELOG.md) — release history
- [Security policy](../SECURITY.md) — vulnerability disclosure
- [License](../LICENSE) — PolyForm Noncommercial 1.0.0
