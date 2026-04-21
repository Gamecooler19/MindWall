# Database and migrations

Mindwall uses PostgreSQL 16 as the primary relational store. Schema management is handled by Alembic. All database access in the application layer uses SQLAlchemy 2.x with an async engine (asyncpg driver).

---

## Schema overview

### Table list

| Table | Description |
|-------|-------------|
| `alembic_version` | Alembic migration tracking |
| `users` | Admin, analyst, operator, and user accounts |
| `mailbox_profiles` | Upstream IMAP/SMTP configurations (credentials encrypted) |
| `messages` | Parsed message records |
| `message_urls` | Extracted URLs — one row per URL per message |
| `message_attachments` | Attachment metadata — one row per attachment per message |
| `analysis_runs` | One record per analysis pipeline run |
| `dimension_scores` | 12 rows per analysis run — one per manipulation dimension |
| `quarantine_items` | Quarantined message records with review state |
| `audit_events` | Append-only audit trail |
| `mailbox_sync_states` | Per-folder upstream sync checkpoints |
| `mailbox_items` | Maps upstream IMAP UIDs to local messages with visibility state |
| `policy_settings` | Runtime-editable policy configuration |
| `alerts` | Security alert records |
| `outbound_messages` | SMTP proxy submission records |

### Automatic timestamp columns

Every model inherits from `app/db/base.py::Base`, which automatically adds:

| Column | Type | Description |
|--------|------|-------------|
| `created_at` | `TIMESTAMPTZ NOT NULL` | Set at insert time by Python |
| `updated_at` | `TIMESTAMPTZ NOT NULL` | Set at insert time; updated on each write |

---

## Migration chain

Migrations are in `alembic/versions/` and are applied in order:

| File | Revision | Description |
|------|---------|-------------|
| `0001_create_users.py` | `a1b2c3d4e5f6` | Users table, UserRole |
| `0002_create_mailbox_profiles.py` | `b2c3d4e5f6a7` | MailboxProfile, credentials |
| `0003_create_messages.py` | `c3d4e5f6a7b8` | Message, MessageUrl, MessageAttachment |
| `0004_create_analysis.py` | `d4e5f6a7b8c9` | AnalysisRun, DimensionScore |
| `0005_create_quarantine_audit.py` | `e5b2c1d8f047` | QuarantineItem, AuditEvent |
| `0006_create_sync_tables.py` | `f6c3d2e9a158` | MailboxSyncState, MailboxItem |
| `0007_create_policy_settings_alerts.py` | `g7d4e3f2a169` | PolicySetting, Alert |
| `0008_create_outbound_messages.py` | `h8e5f4g3b270` | OutboundMessage |
| `0009_fix_base_timestamps.py` | `i9f6g5h4c381` | Corrective: adds missing timestamp columns |

---

## Common Alembic commands

```bash
# Apply all pending migrations
alembic upgrade head

# Check the current revision in the database
alembic current

# Show migration history
alembic history

# Show pending migrations
alembic upgrade head --sql   # dry run (shows SQL)

# Downgrade one revision
alembic downgrade -1

# Downgrade to a specific revision
alembic downgrade g7d4e3f2a169
```

### In Docker

```bash
docker exec mindwall_app python -m alembic upgrade head
docker exec mindwall_app python -m alembic current
```

---

## Connection URLs

The application uses `asyncpg` for async operations:

```env
DATABASE_URL=postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall
```

Alembic uses a synchronous `psycopg2` URL for migrations, derived automatically by `Settings.sync_database_url`:

```python
# Converts: postgresql+asyncpg:// → postgresql+psycopg2://
```

Both drivers must be installed. They are included in `pyproject.toml` dependencies.

---

## Creating a new migration

1. Make your ORM model changes.
2. Generate a migration:

```bash
alembic revision --autogenerate -m "describe what changed"
```

3. Review the generated file in `alembic/versions/`. Autogenerate is not perfect — always verify the generated `upgrade()` and `downgrade()` functions.

4. Apply:

```bash
alembic upgrade head
```

### Migration conventions

- Use descriptive `--message` text: e.g. `add alert resolution fields`
- Include both `upgrade()` and `downgrade()` functions
- For `NOT NULL` columns added to existing tables, always use `server_default` to backfill existing rows
- After backfilling, remove the `server_default` if the application sets the value in Python (to match ORM behavior)
- Do not edit committed migrations to fix bugs — create a new corrective migration

---

## Inspecting the live schema

```bash
# List all tables
docker exec mindwall_db psql -U mindwall -d mindwall -c "\dt"

# Describe a specific table
docker exec mindwall_db psql -U mindwall -d mindwall -c "\d policy_settings"

# Check specific columns
docker exec mindwall_db psql -U mindwall -d mindwall \
  -c "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'alerts' ORDER BY ordinal_position;"
```

---

## ORM base class

`app/db/base.py` defines the shared `Base` with automatic timestamps:

```python
class Base(DeclarativeBase):
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False
    )
```

All models inherit from this `Base`. Individual models can override `created_at`/`updated_at` if they need different semantics (e.g., `Alert.created_at` is stored as `String(40)` for ISO-8601 string representation).
