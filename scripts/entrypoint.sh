#!/bin/sh
# =============================================================================
# Mindwall — container entrypoint
# =============================================================================
# Waits for PostgreSQL, runs Alembic migrations, then starts uvicorn.
# All configuration comes from environment variables.
# =============================================================================

set -e

echo "[entrypoint] Starting Mindwall container..."

# ---------------------------------------------------------------------------
# 1. Wait for PostgreSQL to be ready (pure Python, no pg_isready dependency)
# ---------------------------------------------------------------------------
echo "[entrypoint] Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432}..."

python - <<'PYCHECK'
import sys, socket, os, time

host = os.environ.get('DB_HOST', 'db')
port = int(os.environ.get('DB_PORT', '5432'))
max_tries = 30
for i in range(max_tries):
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        print(f"[entrypoint] PostgreSQL is reachable at {host}:{port}")
        sys.exit(0)
    except OSError:
        print(f"[entrypoint] Waiting for PostgreSQL at {host}:{port} ({i+1}/{max_tries})...")
        time.sleep(2)
print(f"[entrypoint] ERROR: PostgreSQL not reachable after {max_tries} attempts")
sys.exit(1)
PYCHECK

# ---------------------------------------------------------------------------
# 2. Run Alembic migrations
# ---------------------------------------------------------------------------
echo "[entrypoint] Running database migrations..."
python -m alembic upgrade head
echo "[entrypoint] Migrations complete."

# ---------------------------------------------------------------------------
# 3. Optional: seed dev admin user
#    Set MINDWALL_CREATE_ADMIN=true to create a default admin on first boot.
#    Uses MINDWALL_ADMIN_EMAIL and MINDWALL_ADMIN_PASSWORD from env.
# ---------------------------------------------------------------------------
if [ "${MINDWALL_CREATE_ADMIN:-false}" = "true" ]; then
    echo "[entrypoint] Creating/verifying dev admin user..."
    python ./scripts/create_admin.py
fi

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 4. Start the requested process
# ---------------------------------------------------------------------------
# If arguments were passed (e.g. via docker-compose command: [...]) run them
# instead of the default uvicorn web server.  This allows workers to share the
# same image while still running migrations and waiting for the DB.
if [ "$#" -gt 0 ]; then
    echo "[entrypoint] Delegating to command: $*"
    exec "$@"
fi

echo "[entrypoint] Starting uvicorn on 0.0.0.0:8000..."
exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level warning