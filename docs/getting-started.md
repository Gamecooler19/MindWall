# Getting started

This guide walks you through the fastest path to a running Mindwall instance.

---

## Choose your setup path

| Path | When to use |
|------|-------------|
| [Docker quickstart](installation/docker.md) | You want the full stack running immediately with minimal setup |
| [Local development](installation/local.md) | You are developing, debugging, or running without Docker |

---

## What you will need

- **Docker Engine 24+** and **Docker Compose v2** (for the Docker path)
- Or **Python 3.11+**, **PostgreSQL 15+**, **Redis 7+** (for local dev)
- **Ollama** installed and running if you want LLM-based analysis (optional but recommended)

---

## Five-minute Docker quickstart

### 1. Clone and configure

```bash
git clone <repo-url>
cd mindwall
cp .env.example .env.docker
```

Open `.env.docker` and fill in these four values:

```env
# Run: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<your-secret>

# Run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=<your-fernet-key>

MINDWALL_ADMIN_EMAIL=admin@example.com
MINDWALL_ADMIN_PASSWORD=change-me-on-first-login
```

### 2. Start the stack

```bash
docker compose --env-file .env.docker up -d
```

Watch for all services to become healthy:

```bash
docker compose --env-file .env.docker ps
```

Expected output:

```
NAME                  STATUS
mindwall_app          Up (healthy)
mindwall_db           Up (healthy)
mindwall_redis        Up (healthy)
mindwall_imap_proxy   Up (healthy)
mindwall_smtp_proxy   Up (healthy)
```

### 3. Log in

Open [http://localhost:8000](http://localhost:8000) and log in with the admin credentials you set in `.env.docker`.

---

## First steps after login

1. **Register a mailbox** — go to `/mailboxes/new` and enter your upstream IMAP/SMTP details.
2. **Note your proxy credentials** — the mailbox detail page shows your proxy username and password. Copy the password — it is shown only once.
3. **Configure your mail client** — point it at `localhost:1143` (IMAP) and `localhost:1587` (SMTP) using your proxy credentials.
4. **Trigger a sync** — go to `/admin/mailboxes/{id}/sync` and click **Sync Now** to pull messages from your upstream mailbox.
5. **Review the quarantine** — check `/admin/quarantine/` for any flagged messages.

---

## Next steps

- [Mailbox onboarding guide](mailbox-onboarding.md) — detailed walkthrough
- [Admin guide](admin-guide.md) — managing the platform day-to-day
- [Configuration](configuration.md) — all environment variables
- [Analysis engine](analysis-engine.md) — how messages are scored
