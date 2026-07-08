"""One-shot DB bootstrap run from the Docker CMD before uvicorn starts.

The Phase 1 deploy created the schema via `Base.metadata.create_all` (legacy
init_db path) without going through Alembic, so RDS has all the tables but
no `alembic_version` row. A naive `alembic upgrade head` therefore tries to
re-apply the initial migration against a non-empty DB and crashes.

This script handles both states:

- Fresh DB (no tables): `alembic upgrade head` builds everything.
- Pre-Alembic DB (tables present, no version row): stamp the baseline so
  Alembic skips the initial migration, then upgrade applies the rest.
- Already-managed DB (tables + version row): plain `upgrade head`.

Idempotent. Safe to run on every container start.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

# bootstrap.py runs as a standalone script before the FastAPI app loads,
# so it owns its own structlog config (configure_logging is idempotent).
configure_logging()
logger = get_logger(__name__)

# The baseline migration's revision id. Bumping or removing it would break the
# stamp path; pinning here makes that visible in code review.
INITIAL_REVISION = "9e687de34bce"


def main() -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    has_tenants = "tenants" in tables
    has_alembic_version = "alembic_version" in tables
    engine.dispose()

    candidates = [Path("/app/alembic.ini"), Path("alembic.ini")]
    ini = next((p for p in candidates if p.is_file()), None)
    if ini is None:
        logger.error("alembic.ini not found in /app or cwd")
        return 1
    cfg = Config(str(ini))

    if has_tenants and not has_alembic_version:
        logger.info(
            "Stamping baseline %s (legacy create_all schema, no version row)",
            INITIAL_REVISION,
        )
        command.stamp(cfg, INITIAL_REVISION)

    logger.info("Upgrading to head")
    command.upgrade(cfg, "head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
