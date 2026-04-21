# =============================================================================
# Mindwall — Dockerfile
# =============================================================================
# Multi-stage build: builder installs dependencies, final image is lean.
# Designed for local development and single-node self-hosted deployments.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: dependency builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for bcrypt, cryptography, asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency spec first to leverage Docker layer caching
COPY pyproject.toml README.md ./
# Copy source so hatchling can build the wheel properly
COPY app/ ./app/
COPY workers/ ./workers/

# Build and install all dependencies including the package itself
RUN pip install --upgrade pip \
    && pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Stage 2: runtime image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Mindwall"
LABEL org.opencontainers.image.description="Privacy-first, self-hosted email security platform"
LABEL org.opencontainers.image.version="0.1.0"

WORKDIR /app

# Runtime system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY app/ ./app/
COPY workers/ ./workers/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY pyproject.toml README.md ./

# Copy runtime scripts
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
COPY scripts/create_admin.py ./scripts/create_admin.py

RUN chmod +x ./scripts/entrypoint.sh

# Create data directories that the app will use for raw messages and blobs
RUN mkdir -p /app/data/raw_messages /app/data/blobs

# Non-root user for security
RUN useradd --system --no-create-home --shell /bin/false mindwall \
    && chown -R mindwall:mindwall /app

USER mindwall

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8000/health/live || exit 1

ENTRYPOINT ["./scripts/entrypoint.sh"]
