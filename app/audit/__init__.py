"""Audit domain — immutable, append-only audit log.

Phase 6 will implement:
  - Structured audit events: actor, action, target, timestamp, details
  - Database-backed append-only log
  - Admin UI viewer with filtering
  - Export for on-premises SIEM / SYSLOG integrations

Every security-relevant action in Mindwall (login, quarantine, release,
policy change, credential rotation) must produce an audit event.
"""
