# syntax=docker/dockerfile:1.7
# Multi-stage build for the FastAPI backend.
# - builder: pulls deps via uv into a venv
# - runtime: copies the venv + app code, runs uvicorn

FROM python:3.14-slim AS builder

# Install uv (matching the version in pyproject's `uv.lock` lockfile).
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install only runtime deps first for layer caching. The lockfile + pyproject
# define the full set; dev deps excluded but `--all-extras` pulls in
# [postgres] (psycopg, the production DB driver) and [ai] (anthropic, for
# the AI explanation layer when a key is configured at runtime).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --all-extras

# Copy the app code and reinstall to register the project itself.
COPY backend ./backend
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-extras


FROM python:3.14-slim AS runtime

# `curl` is needed once at build time to fetch Atlassian's JWKS document so
# the backend can validate Forge Invocation Tokens without public-internet
# egress at runtime (App Runner VPC connector + no NAT can't reach the
# public internet). Atlassian rotates these keys infrequently; rotation =
# rebuild the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user for defense-in-depth.
RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app
COPY --from=builder /app /app

# Bake the Forge JWKS document. The fit_auth module reads it via
# FORGE_JWKS_PATH at startup if set; otherwise it falls back to live URL.
RUN curl -fsSL https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json \
      -o /app/forge_jwks.json \
    && chown app:app /app/forge_jwks.json

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    FORGE_JWKS_PATH=/app/forge_jwks.json \
    APP_RUN_MIGRATIONS=1

USER app
EXPOSE 8000

# Run pending migrations BEFORE uvicorn starts so the server is healthy as
# soon as it answers /healthz. Doing this in the FastAPI lifespan instead
# blocked startup long enough for App Runner's health check to roll back
# the deploy. The bootstrap module also stamps the alembic baseline if the
# DB was first populated by the legacy create_all path. exec replaces the
# shell with uvicorn so signals propagate.
CMD ["sh", "-c", "python -m app.db.bootstrap && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
