# Mailbox sync

This document describes how Mindwall pulls messages from upstream IMAP servers and builds the local virtual mailbox.

---

## Overview

Mindwall does not receive mail directly. Instead, it connects to your existing upstream IMAP server, fetches new messages, runs them through the analysis pipeline, and stores the results locally. The local store then backs the IMAP proxy virtual mailbox that your mail client connects to.

This design means:
- Your upstream mailbox remains unchanged
- Mail is pulled on demand (or on a schedule, in future phases)
- Analysis results are cached — the IMAP proxy does not re-analyze on every fetch

---

## Triggering a sync

Currently, sync is triggered manually from the admin UI:

**URL:** `/admin/mailboxes/{id}/sync`

Click **Sync Now** to start a sync for the default folder (`INBOX` by default, configurable via `IMAP_SYNC_DEFAULT_FOLDER`).

The sync result is displayed after completion:
- Number of new messages fetched
- Number of messages ingested successfully
- Number of errors
- Updated sync status

---

## Sync process internals

```
1. Load or create MailboxSyncState for the folder
2. Decrypt upstream IMAP credentials (Fernet)
3. Connect to upstream IMAP server (SSL/STARTTLS/plain)
4. SELECT the target folder
5. Check UIDVALIDITY — if changed, reset checkpoint to 0 (full re-sync)
6. Fetch UIDs greater than last_seen_uid (up to IMAP_SYNC_BATCH_SIZE)
7. For each new UID:
   a. Fetch raw RFC 5322 bytes
   b. Parse message (app/messages/parser.py)
   c. Write raw .eml to disk (SHA-256 addressed)
   d. Persist Message + MessageUrl + MessageAttachment rows
   e. Run deterministic security checks
   f. Call Ollama (if LLM_ENABLED=true)
   g. Combine scores → compute verdict
   h. Persist AnalysisRun + DimensionScore rows
   i. Create QuarantineItem + AuditEvent + Alert if verdict requires it
   j. Create MailboxItem with appropriate visibility
8. Update MailboxSyncState: last_seen_uid, last_sync_at, sync_status
9. Commit transaction
```

Sync is **idempotent** — re-running against the same UIDs is safe because each UID is checked against existing `MailboxItem` records before processing.

---

## Sync state model

Each mailbox folder has a `MailboxSyncState` record:

| Field | Description |
|-------|-------------|
| `mailbox_profile_id` | Associated mailbox profile |
| `folder_name` | IMAP folder name (e.g. `INBOX`) |
| `last_seen_uid` | The highest UID successfully processed |
| `uid_validity` | UIDVALIDITY value; change triggers full re-sync |
| `last_sync_at` | Timestamp of the last completed sync |
| `sync_status` | `idle`, `syncing`, `partial`, `error` |
| `error_message` | Last error description (if status is `error` or `partial`) |
| `failed_auth` | True if the last failure was an authentication error |
| `failed_connection` | True if the last failure was a connection error |

---

## Error handling

| Error type | Behavior |
|-----------|----------|
| Authentication failure | `sync_status=error`, `failed_auth=True`, state committed immediately |
| Connection failure | `sync_status=error`, `failed_connection=True` |
| Per-message ingest failure | Error recorded on `MailboxItem`, sync continues to next UID |
| Partial sync | `sync_status=partial` if some messages were ingested and some failed |
| UIDVALIDITY reset | Full re-sync triggered from UID 0; existing `MailboxItem` records are preserved |

---

## Mailbox item visibility states

Each message pulled from upstream is represented as a `MailboxItem` with a visibility state:

| Visibility | Meaning |
|-----------|---------|
| `PENDING` | Analysis not yet complete |
| `VISIBLE` | Cleared by analysis — shown in INBOX via IMAP proxy |
| `QUARANTINED` | Quarantined — shown in `Mindwall/Quarantine` via IMAP proxy |
| `HIDDEN` | Removed from normal view (e.g. after a deleted quarantine item) |

The IMAP proxy uses these visibility states to build the filtered virtual mailbox view.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAP_SYNC_TIMEOUT_SECONDS` | `30` | Timeout for individual IMAP operations during sync |
| `IMAP_SYNC_DEFAULT_FOLDER` | `INBOX` | Default folder when triggering sync from the admin UI |
| `IMAP_SYNC_BATCH_SIZE` | `50` | Maximum new UIDs to process in a single sync run |

---

## Admin views

| URL | Description |
|-----|-------------|
| `/admin/mailboxes/{id}/sync` | Sync status, checkpoints, trigger form |
| `/admin/mailboxes/{id}/inbox` | Messages with `VISIBLE` status |
| `/admin/mailboxes/{id}/quarantine` | Messages with `QUARANTINED` or `HIDDEN` status |
| `/admin/mailboxes/{id}/items/{item_id}` | Full item detail with analysis |

---

## Planned improvements

- **Background worker:** Scheduled automatic sync (configurable interval) via a persistent worker process
- **IMAP IDLE:** Push-based notification from upstream servers to avoid polling
- **Multi-folder sync:** Sync folders other than INBOX
- **Delta sync optimization:** More efficient UID range fetching for large mailboxes
