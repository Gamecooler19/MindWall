# Alerts and audit

This document covers the alerts (security incident) lifecycle and the append-only audit log.

---

## Alerts

Alerts are security notifications raised when Mindwall detects a high-risk message or a significant system event.

### When alerts are created

Alerts are created automatically by the analysis orchestrator:

| Verdict | Alert created? | Severity |
|---------|---------------|---------|
| `quarantine` | Yes | `high` |
| `escalate_to_admin` | Yes | `critical` |
| `reject` (gateway mode) | Yes | `critical` |
| Others | No | — |

Alerts link to the quarantine item so you can navigate directly from the alert to the message for review.

### Alert severity levels

| Severity | Colour | Meaning |
|---------|--------|---------|
| `low` | Blue | Informational — low urgency |
| `medium` | Yellow | Moderate concern — review when convenient |
| `high` | Orange | Significant risk — review promptly |
| `critical` | Red | Immediate attention required |

### Alert lifecycle

```
OPEN ──→ ACKNOWLEDGED ──→ RESOLVED
OPEN ──────────────────→ RESOLVED  (direct resolve without acknowledging)
```

| Status | Description |
|--------|-------------|
| `open` | New alert — not yet seen by an admin |
| `acknowledged` | Admin has reviewed the alert. Records who acknowledged it and when |
| `resolved` | Alert is closed. Optionally includes a resolution note |

### Triage workflow

**URL:** `/admin/alerts/`

The alerts list shows all alerts filterable by severity and status.

**URL:** `/admin/alerts/{id}`

The alert detail page shows:
- Title and body
- Severity and current status
- Trigger action (e.g. `quarantine.created`)
- Link to the associated quarantine item (if applicable)
- Link to the associated message (if applicable)
- Triage action buttons: **Acknowledge**, **Resolve**
- Resolution note field

### Triage actions

**Acknowledge:**
- Sets status to `acknowledged`
- Records the acting admin's user ID and timestamp
- Does not create an audit event (alerts are not part of the quarantine audit trail)

**Resolve:**
- Sets status to `resolved`
- Records the acting admin's user ID and timestamp
- Resolution note is optional but recommended

---

## Audit log

The audit log is an append-only record of significant system actions. It is the compliance record for all quarantine decisions and admin overrides.

**URL:** `/admin/audit/`

### What is recorded

| Event type | When |
|-----------|------|
| `quarantine.created` | A new quarantine item is created |
| `quarantine.opened` | An admin opens a quarantine item detail page |
| `quarantine.released` | A quarantine item is released to inbox |
| `quarantine.deleted` | A quarantine item is permanently deleted |
| `quarantine.note_added` | An admin note is added to a quarantine item |

### Audit event structure

Each `AuditEvent` record includes:

| Field | Type | Description |
|-------|------|-------------|
| `event_type` | string | Stable event identifier |
| `actor_user_id` | integer | ID of the admin who performed the action |
| `actor_email` | string | Email of the acting admin (denormalized for readability) |
| `quarantine_item_id` | integer (nullable) | Associated quarantine item |
| `message_id` | integer (nullable) | Associated message |
| `details` | JSON | Structured event-specific details (e.g. note text, old/new status) |
| `created_at` | timestamp | UTC timestamp of the event |

### Guarantees

- Audit events are **append-only**. No UI or API endpoint deletes or modifies existing events.
- Every quarantine state transition records an audit event with the actor's identity.
- The audit timeline on the quarantine detail page shows the full event history for that item in chronological order.

### Viewing the log

The audit log UI (`/admin/audit/`) shows 50 events per page, newest first. Filtering by event type or actor is not yet implemented in the UI (filter via direct DB query if needed).
