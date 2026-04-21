# Docker installation

This guide covers running the full Mindwall stack with Docker Compose.

---

## Prerequisites

- Docker Engine 24+ with Docker Compose v2 (`docker compose` not `docker-compose`)
- 4 GB RAM minimum; 8 GB recommended when Ollama is enabled

---

## Stack overview

The `docker-compose.yml` defines five services:

| Service | Container | Purpose | Default host port |
|---------|-----------|---------|-----------------|
| `app` | `mindwall_app` | FastAPI web application | 8000 |
| `db` | `mindwall_db` | PostgreSQL 16 | — (internal only) |
| `redis` | `mindwall_redis` | Redis 7 | — (internal only) |
| `imap_proxy` | `mindwall_imap_proxy` | IMAP proxy server | 1143 |
| `smtp_proxy` | `mindwall_smtp_proxy` | SMTP submission proxy | 1587 |

All services communicate on an internal `mindwall_net` bridge network. Only the app (8000), IMAP proxy (1143), and SMTP proxy (1587) expose host ports.

---

## Step 1: Clone and configure

```bash
git clone <repo-url>
cd mindwall
cp .env.example .env.docker
```

### Generate required secrets

```bash
# Session signing key
python -c "import secrets; print(secrets.token_hex(32))"

# Fernet encryption key for credential storage
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Minimum `.env.docker` configuration

```env
SECRET_KEY=<output-from-first-command>
ENCRYPTION_KEY=<output-from-second-command>
MINDWALL_ADMIN_EMAIL=admin@yourdomain.com
MINDWALL_ADMIN_PASSWORD=strong-password-here
```

All other values have defaults suitable for development. See [configuration.md](../configuration.md) for the full reference.

---

## Step 2: Build and start

```bash
docker compose --env-file .env.docker up -d --build
```

On first run, the entrypoint script:
1. Waits for PostgreSQL to be ready
2. Runs `alembic upgrade head` to apply all migrations
3. Creates the admin user if `MINDWALL_CREATE_ADMIN=true`
4. Starts uvicorn

### Check service health

```bash
docker compose --env-file .env.docker ps
```

Wait until all services show `(healthy)`.

---

## Step 3: Access the UI

Open [http://localhost:8000](http://localhost:8000) and log in with the credentials from `.env.docker`.

---

## Port mapping

Host ports are configurable via `.env.docker`:

| Variable | Default host port | Container port |
|----------|-----------------|---------------|
| `APP_PORT` | `8000` | `8000` |
| `IMAP_PROXY_DEV_PORT` | `1143` | `1143` |
| `SMTP_PROXY_DEV_PORT` | `1587` | `1587` |

---

## Enabling Ollama (LLM analysis)

The `docker-compose.yml` includes a commented-out Ollama service definition. To enable it:

### 1. Uncomment the Ollama service

In `docker-compose.yml`, uncomment the `ollama:` block and the `mindwall_ollama_data` volume.

### 2. Enable LLM in the app service

```yaml
# In the app service environment:
LLM_ENABLED: "true"
OLLAMA_BASE_URL: http://ollama:11434
```

### 3. Start and pull the model

```bash
docker compose --env-file .env.docker up -d ollama
docker compose --env-file .env.docker exec ollama ollama pull llama3.1:8b
```

Model data is persisted in the `mindwall_ollama_data` named volume.

> **Resource note:** Llama 3.1 8B requires approximately 5 GB of disk for model weights and 6–8 GB of RAM for inference. Allocate sufficient Docker Desktop memory limits if running on macOS or Windows.

---

## Common operations

```bash
# View all service logs
docker compose --env-file .env.docker logs -f

# Follow only app logs
docker compose --env-file .env.docker logs -f app

# Run a database migration manually
docker exec mindwall_app python -m alembic upgrade head

# Check current migration revision
docker exec mindwall_app python -m alembic current

# Open a PostgreSQL shell
docker exec -it mindwall_db psql -U mindwall -d mindwall

# Run the test suite inside the container
docker exec mindwall_app python -m pytest tests/ -q

# Restart only the app service
docker compose --env-file .env.docker restart app

# Stop all services (preserves data)
docker compose --env-file .env.docker down

# Full reset — destroys all data volumes
docker compose --env-file .env.docker down -v
```

---

## Volumes

| Volume | Contents |
|--------|---------|
| `mindwall_pg_data` | PostgreSQL data directory |
| `mindwall_redis_data` | Redis persistence |
| `mindwall_data` | Application data — raw messages, blobs, outbound messages |

Data volumes persist across `docker compose down`. Use `down -v` for a full reset.

---

## Production considerations

The current Docker configuration is designed for **development and evaluation**. For production:

- Use a proper secret management solution instead of `.env.docker` files.
- Put a TLS-terminating reverse proxy (nginx, Caddy, Traefik) in front of port 8000.
- Do not expose PostgreSQL or Redis ports to the host (`5432`, `6379`).
- Consider network-level isolation for IMAP (1143) and SMTP (1587) proxy ports.
- Set `DEBUG=false` (default) in production.
- Mount external volumes for `mindwall_data` to ensure backups are captured.
- Review the [Security policy](../../SECURITY.md) for credential handling expectations.
