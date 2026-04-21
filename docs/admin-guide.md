# Admin guide

This guide covers day-to-day operations for Mindwall administrators.

---

## Accessing the admin UI

Navigate to `http://<your-host>:8000` and log in with your admin credentials. All pages listed below require the `admin` role.

The admin sidebar links to all major sections. The dashboard (`/admin/`) is the entry point.

---

## Dashboard

**URL:** `/admin/`

The dashboard shows three live counts:

| Count | Meaning |
|-------|---------|
| Pending quarantine | Messages awaiting review in the quarantine queue |
| Open alerts | Active security alerts that have not been acknowledged |
| Mailboxes | Number of registered mailbox profiles |

Click any count to navigate to the corresponding page.

---

## Quarantine review

**URL:** `/admin/quarantine/`

The quarantine inbox lists all quarantined messages. Filter by status using the tabs:

| Status | Description |
|--------|-------------|
| `pending_review` | Newly quarantined, not yet opened |
| `in_review` | Opened by an admin — assigned to a reviewer |
| `released` | Released back to the user's inbox |
| `deleted` | Permanently deleted |

### Reviewing a message

1. Click a quarantine item to open the detail page.
2. The detail page shows:
   - Sender, recipients, subject
   - Authentication signals (SPF, DKIM, DMARC if extracted)
   - Sanitized message preview (no active content, no remote images)
   - Extracted URLs
   - Attachment metadata
   - Verdict and risk score
   - Per-dimension score grid (12 dimensions)
   - Deterministic findings list
   - LLM rationale and evidence
   - Audit timeline (all actions and their timestamps)

3. Choose an action:

| Action | Effect |
|--------|--------|
| **Release** | Marks the message `VISIBLE` in the sender's inbox. Creates an audit event. |
| **Delete** | Permanently removes the quarantine item. Creates an audit event. |
| **Add note** | Records an admin note in the audit trail without changing state |

### State machine

```
PENDING_REVIEW
    ↓
IN_REVIEW      ← opened by admin
    ↓                ↓
RELEASED      DELETED
```

Direct transitions from `PENDING_REVIEW` to `RELEASED` or `DELETED` are also permitted.

---

## Alerts

**URL:** `/admin/alerts/`

Alerts are created automatically when a message receives a `quarantine` or `escalate_to_admin` verdict. They can also be created by system events.

The alerts list is filterable by severity (`low`, `medium`, `high`, `critical`) and status.

### Alert lifecycle

| Status | Description |
|--------|-------------|
| `open` | New alert — requires attention |
| `acknowledged` | Reviewed and acknowledged by an admin |
| `resolved` | Fully resolved with an optional resolution note |

### Triage actions

On the alert detail page (`/admin/alerts/{id}`):

- **Acknowledge** — marks the alert acknowledged. Records the acting admin and timestamp.
- **Resolve** — marks the alert resolved. Optionally add a resolution note.

---

## Policy editor

**URL:** `/admin/policy/`

The policy editor allows runtime adjustment of policy thresholds and flags without redeployment. Changes take effect immediately and are persisted to the `policy_settings` database table.

Editable settings include the five verdict risk thresholds:

| Setting key | Description |
|------------|-------------|
| `verdict_threshold_allow` | Upper risk bound for `allow` verdict |
| `verdict_threshold_allow_with_banner` | Upper risk bound for `allow_with_banner` |
| `verdict_threshold_soft_hold` | Upper risk bound for `soft_hold` |
| `verdict_threshold_quarantine` | Upper risk bound for `quarantine` |

> **Note:** Runtime policy settings override environment-variable defaults. If a setting is not in the database, the environment-variable value is used. Delete the database row to revert to the environment default.

---

## Audit log

**URL:** `/admin/audit/`

The audit log is an append-only record of all significant system actions. It cannot be edited or deleted through the UI.

Each entry records:
- Event type (e.g., `quarantine.released`, `alert.resolved`)
- Actor (user email)
- Timestamp
- Structured detail payload

The log is paginated (50 entries per page).

---

## Model health

**URL:** `/admin/health/model`

Shows the status of the Ollama connection:
- Whether Ollama is reachable at `OLLAMA_BASE_URL`
- Which models are available
- Whether the configured `OLLAMA_MODEL` is present

If Ollama is unreachable, the analysis engine operates in degraded mode (deterministic-only analysis).

---

## Mailbox profiles

**URL:** `/admin/mailboxes/`

Lists all registered mailbox profiles with their owner, IMAP host, sync status, and item counts.

### Sync management

**URL:** `/admin/mailboxes/{id}/sync`

Shows:
- Per-folder sync checkpoints (last seen UID, last sync timestamp, status)
- Item counts by visibility
- A **Sync Now** button to trigger an immediate upstream sync

Sync status values:

| Status | Meaning |
|--------|---------|
| `idle` | No sync in progress; last run completed normally |
| `syncing` | Sync is currently running |
| `partial` | Last sync completed but some messages failed to ingest |
| `error` | Last sync failed (auth failure, connection error, etc.) |

### Mailbox inbox and quarantine views

- `/admin/mailboxes/{id}/inbox` — messages with `VISIBLE` status
- `/admin/mailboxes/{id}/quarantine` — messages with `QUARANTINED` or `HIDDEN` status

---

## Outbound message review

**URL:** `/admin/outbound/`

Lists all messages submitted through the SMTP proxy. When `SMTP_DELIVERY_MODE=capture` (default), submissions are stored locally and appear here.

Each entry shows:
- MAIL FROM address
- RCPT TO addresses
- Subject
- Delivery mode and status
- Message size and SHA-256
- Storage path

The detail page (`/admin/outbound/{id}`) shows the full metadata record. The raw `.eml` file is on disk at `OUTBOUND_MESSAGE_STORE_PATH`.

---

## Message Lab

**URL:** `/admin/messages/`

An admin-only tool for uploading and inspecting raw `.eml` files. Useful for:
- Testing the analysis pipeline with sample messages
- Debugging parse issues
- Verifying analysis output for specific messages

Upload a `.eml` file to ingest it and view the parse results. Click **Analyse** to trigger the full analysis pipeline and see the verdict, scores, and findings.

The Message Lab can be disabled by setting `MESSAGE_LAB_ENABLED=false`.
