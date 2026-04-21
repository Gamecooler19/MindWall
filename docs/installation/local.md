# Local installation

This guide covers setting up Mindwall for local development or local-only deployments without Docker.

---

## Prerequisites

| Dependency | Minimum version | Notes |
|-----------|----------------|-------|
| Python | 3.11 | 3.12 also tested |
| PostgreSQL | 15 | asyncpg driver required |
| Redis | 7 | Used for sessions and future queue support |
| Ollama | Latest | Required only for LLM analysis |

---

## Step 1: Clone the repository

```bash
git clone <repo-url>
cd mindwall
```

---

## Step 2: Create a Python environment

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

---

## Step 3: Install dependencies

```bash
pip install -e ".[dev]"
```

This installs the application package plus all development tools (`pytest`, `ruff`, etc.).

---

## Step 4: Start PostgreSQL and Redis

The simplest approach is a minimal Docker setup for the dependencies only:

```bash
# PostgreSQL
docker run -d --name mindwall-pg \
  -e POSTGRES_USER=mindwall \
  -e POSTGRES_PASSWORD=mindwall \
  -e POSTGRES_DB=mindwall \
  -p 5432:5432 postgres:16-alpine

# Redis
docker run -d --name mindwall-redis \
  -p 6379:6379 redis:7-alpine
```

Alternatively, install PostgreSQL and Redis natively on your system.

---

## Step 5: Configure environment

```bash
cp .env.example .env
```

Open `.env` and set the required values:

```env
# Generate: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<at-least-32-char-random-string>

# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=<valid-fernet-key>

DATABASE_URL=postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall
REDIS_URL=redis://localhost:6379/0
```

Everything else has a safe default. See [configuration.md](../configuration.md) for the full reference.

---

## Step 6: Run migrations

```bash
alembic upgrade head
```

This applies all migrations in `alembic/versions/` (currently 0001–0009).

---

## Step 7: Create the first admin user

```bash
python scripts/create_admin.py
```

The script will prompt for an email and password.

---

## Step 8: Start the application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) and log in.

---

## Step 9: Start the proxies (optional)

To use the IMAP and SMTP proxies locally, start them in separate terminals:

```bash
# IMAP proxy (default port 1993)
python workers/imap_proxy.py

# SMTP submission proxy (default port 1587)
python workers/smtp_proxy.py
```

---

## Step 10: Enable LLM analysis (optional)

1. [Install Ollama](https://ollama.com/download) for your operating system.
2. Pull the model:

```bash
ollama pull llama3.1:8b
```

3. Ensure Ollama is running (it starts automatically on most installs).
4. Set in `.env`:

```env
LLM_ENABLED=true
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

5. Restart the FastAPI app.

---

## Development workflow

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=term-missing

# Lint
ruff check .

# Format
ruff format .

# Type check (if mypy is configured)
mypy app/
```

### Seeding dev data

```bash
# Seed a dev IMAP profile with proxy credentials
python scripts/seed_imap_dev.py
```

This creates a test mailbox profile with known proxy credentials:
- Proxy username: `mw_seed_imap_dev`
- Proxy password: `seed-imap-dev-password-2024`

These are used by the IMAP proxy verification script.

---

## Troubleshooting

See [troubleshooting.md](../troubleshooting.md) for common issues.

Common startup failures:

| Error | Likely cause |
|-------|-------------|
| `ENCRYPTION_KEY must be a valid Fernet key` | Key is missing or malformed — regenerate with `Fernet.generate_key()` |
| `connection refused` on PostgreSQL | PostgreSQL is not running or `DATABASE_URL` is wrong |
| `alembic.util.exc.CommandError` | Migrations out of sync — run `alembic current` to diagnose |
