# Quarantine workflow

The quarantine system holds suspicious messages for admin review. It maintains a strict state machine, an encrypted blob store, and an append-only audit trail for every action taken.

---

## Quarantine states

```
PENDING_REVIEW ──→ IN_REVIEW ──→ RELEASED
                        └──────→ DELETED

PENDING_REVIEW ──→ RELEASED  (direct, without IN_REVIEW)
PENDING_REVIEW ──→ DELETED   (direct, without IN_REVIEW)
```

| Status | Description |
|--------|-------------|
| `pending_review` | Newly quarantined — awaiting admin attention |
| `in_review` | Opened by an admin — status set automatically when detail page is first loaded |
| `released` | Released back to the user's inbox — `MailboxItem` visibility set to `VISIBLE` |
| `deleted` | Permanently removed from quarantine — message content deleted from blob store |

---

## When quarantine is created

A `QuarantineItem` is created by the analysis orchestrator when the verdict is:

- `quarantine`
- `escalate_to_admin`
- `reject` (gateway mode)
- `soft_hold` (only if `QUARANTINE_SOFT_HOLD=true`)

Each `QuarantineItem` links to the originating `Message`, the `AnalysisRun`, and optionally to a `MailboxItem`.

---

## Reviewing quarantined messages

**URL:** `/admin/quarantine/`

The quarantine inbox is accessible to both `admin` and `analyst` roles.

### Detail page

**URL:** `/admin/quarantine/{item_id}`

The detail page shows:

- Sender, recipients, subject
- Authentication signals (DKIM, SPF, DMARC indicators)
- Sanitized message body preview (scripts, forms, and remote resources stripped)
- Extracted URLs
- Attachment list with file types and sizes
- Verdict badge and overall risk score
- Confidence level and degraded mode indicator
- Per-dimension score grid (all 12 dimensions, color-coded)
- Deterministic findings with severity
- LLM rationale and evidence list
- Complete audit timeline showing every state transition

Opening the detail page automatically transitions the item from `pending_review` to `in_review`.

### Available actions

| Action | Effect |
|--------|--------|
| **Release** | Changes status to `released`. Updates `MailboxItem` visibility to `VISIBLE`. Creates an `AuditEvent`. |
| **Delete** | Changes status to `deleted`. Removes message content from blob store. Creates an `AuditEvent`. |
| **Add note** | Records an admin note in the audit trail. Status unchanged. |

---

## Blob storage

Quarantined message content and attachments are stored encrypted on disk.

**Storage location:** `BLOB_STORAGE_PATH` (default: `./data/blobs`)

The blob store uses a two-level directory layout based on SHA-256 content addressing:

```
data/blobs/
    ab/
        ab3f4e... (sha256).blob
    cd/
        cd7e2a... (sha256).blob
```

Blobs are encrypted at rest. The encryption uses the application's `ENCRYPTION_KEY`.

> **Note:** Currently the blob store holds quarantine item content. The encryption layer is provided by `app/quarantine/blob.py`.

---

## Audit trail

Every action taken on a quarantine item is recorded as an `AuditEvent`:

```python
AuditEvent(
    event_type="quarantine.released",
    actor_user_id=42,
    actor_email="admin@example.com",
    quarantine_item_id=17,
    details={"note": "Confirmed safe — internal newsletter"}
)
```

Events are append-only. They cannot be edited or deleted through any UI or API.

Audit events are viewable in the quarantine detail page timeline and in the global audit log at `/admin/audit/`.

---

## Alerts on quarantine creation

When a `QuarantineItem` is created with a `quarantine` or `escalate_to_admin` verdict, an `Alert` is automatically raised:

- `quarantine` verdicts → alert severity `high`
- `escalate_to_admin` verdicts → alert severity `critical`

The alert links to the quarantine item and can be acknowledged and resolved through the alerts UI (`/admin/alerts/`).

---

## Bulk operations

Bulk review operations (select all, bulk release, bulk delete) are not yet implemented. All actions are per-item in the current release.
