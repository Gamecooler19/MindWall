# Troubleshooting

This page covers common issues and diagnostic steps.

---

## Application won't start

### `ENCRYPTION_KEY must be a valid Fernet key`

The application validates the Fernet key at startup. Generate a valid key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the output as `ENCRYPTION_KEY` in your `.env` file.

### `Field required: secret_key`

`SECRET_KEY` is missing. Generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### `connection refused` (PostgreSQL)

The application cannot connect to PostgreSQL. Check:
1. Is PostgreSQL running? `docker ps` or `pg_isready -h localhost -p 5432`
2. Is `DATABASE_URL` correct in `.env`?
3. Do the credentials in `DATABASE_URL` match your PostgreSQL setup?

### `connection refused` (Redis)

Similar to PostgreSQL. Check:
1. Is Redis running? `docker ps`
2. Is `REDIS_URL` correct?

---

## Migration issues

### `alembic.util.exc.CommandError: Can't locate revision identified by '...'`

The database is at a revision that the local code doesn't know about. Most common cause: a migration file exists in the database (`alembic_version`) but not in `alembic/versions/`.

Diagnose:

```bash
# What does the DB say?
docker exec mindwall_db psql -U mindwall -d mindwall -c "SELECT * FROM alembic_version;"

# What does the local code know?
alembic history
```

If a migration ran in an older container image but the file was never committed, recover the file from the container:

```bash
docker exec mindwall_app cat /app/alembic/versions/<filename>.py
```

Copy the output to the local `alembic/versions/` directory.

### `column X does not exist` at runtime

The ORM model expects a column that doesn't exist in the database. This is schema drift. Steps:

1. Check which columns are missing:
   ```bash
   docker exec mindwall_db psql -U mindwall -d mindwall -c "\d <table_name>"
   ```
2. Compare to the ORM model definition.
3. Create a corrective migration that adds the missing columns (with `server_default=sa.text("now()")` for timestamp columns to backfill existing rows).
4. Apply: `docker exec mindwall_app python -m alembic upgrade head`

---

## IMAP proxy issues

### Mail client can't connect

1. Is the proxy running? `docker ps` — check `mindwall_imap_proxy` status.
2. Check logs: `docker logs mindwall_imap_proxy --tail=50`
3. Check the port binding: the proxy listens on `IMAP_PROXY_PORT` (default 1993; Docker dev 1143).
4. Verify the proxy is accepting connections:
   ```bash
   python -c "import socket; s=socket.create_connection(('localhost',1143),timeout=3); print(s.recv(100)); s.close()"
   ```
   You should see a greeting like `* OK Mindwall IMAP4rev1 ...`.

### Login fails with `NO [AUTHENTICATIONFAILED]`

1. Confirm you are using the **proxy** username and password (not your upstream email credentials).
2. The proxy username is in the format `mw_<name>_<suffix>` — visible on the mailbox detail page.
3. If you lost the proxy password, use **Reset proxy password** on the mailbox detail page.

### Inbox is empty after sync

1. Trigger a sync at `/admin/mailboxes/{id}/sync`.
2. Check the sync status — look for errors.
3. Check IMAP proxy logs for any fetch errors.
4. Confirm that the mailbox profile has valid upstream credentials (test connectivity at `/mailboxes/{id}/test`).

---

## SMTP proxy issues

### Mail client gets `535 Authentication failed`

1. Confirm you are using the **proxy** username and password.
2. If you lost the proxy password, reset it.

### Mail client gets `530 Authentication required`

You are trying to submit a message before authenticating. Ensure your mail client is configured to use password authentication.

### Relay mode fails

Check:
1. Is `SMTP_DELIVERY_MODE=relay` set?
2. Do the upstream SMTP credentials on the mailbox profile work? Test at `/mailboxes/{id}/test`.
3. Check SMTP proxy logs: `docker logs mindwall_smtp_proxy --tail=50`

---

## Analysis issues

### Analysis is not running (degraded mode everywhere)

1. Is Ollama running? Check `OLLAMA_BASE_URL` (must be `http://localhost:11434` or equivalent).
2. Is the model available?
   ```bash
   curl http://localhost:11434/api/tags
   ```
3. Is `LLM_ENABLED=true`?
4. Check the model health page: `/admin/health/model`

### All messages get `allow` verdict regardless of content

If `LLM_ENABLED=false`, only deterministic checks run. With no obvious signals in a test message, the deterministic risk score may be low. Try:
1. Enable LLM analysis
2. Upload a known-phishing sample to the Message Lab and run analysis

---

## Docker issues

### Container fails health check

```bash
docker compose --env-file .env.docker ps
docker logs mindwall_app --tail=100
```

Common causes:
- Missing environment variables (`SECRET_KEY`, `ENCRYPTION_KEY`)
- Database not ready when app starts (entrypoint has a wait loop but may time out on slow machines)
- Migration failure on startup

### Data volume conflict after code change

If you change the database schema and restart without running migrations:

```bash
docker exec mindwall_app python -m alembic upgrade head
docker compose --env-file .env.docker restart app
```

### Full reset

```bash
docker compose --env-file .env.docker down -v
docker compose --env-file .env.docker up -d --build
```

This destroys all data. Back up the database first if needed.

---

## Admin UI issues

### Admin page returns 401

You are not logged in, or your session has expired. Navigate to `/auth/login`.

### Admin page returns 403

Your account does not have the `admin` role. Only admin-role users can access admin pages.

### Policy editor changes not taking effect

Runtime policy overrides are stored in `policy_settings`. After saving, the application reads the new values immediately (no restart required). If a change does not take effect, check:
1. The change was saved (the page should show a success message)
2. The analysis engine is using the database-backed settings (not a cached in-memory value from startup)

---

## Getting more diagnostic information

### Structured logs

Enable debug logging to see detailed request and analysis logs:

```env
DEBUG=true
```

In Docker:

```bash
docker compose --env-file .env.docker logs -f app 2>&1 | grep -i "error\|warn\|exception"
```

### Database inspection

```bash
# Open a psql shell
docker exec -it mindwall_db psql -U mindwall -d mindwall

# Common diagnostic queries
SELECT * FROM alembic_version;
SELECT id, email, role FROM users;
SELECT id, display_name, imap_host, sync_status FROM mailbox_profiles LEFT JOIN mailbox_sync_states ON mailbox_profiles.id = mailbox_sync_states.mailbox_profile_id;
SELECT COUNT(*) FROM quarantine_items WHERE status = 'pending_review';
SELECT COUNT(*) FROM alerts WHERE status = 'open';
```
