"""Postgres-side smoke test for the RLS GUC binding.

The 175-test backend suite runs on SQLite, with Postgres-specific code
paths gated behind a `dialect.name == "postgresql"` check (see
`backend/app/core/deps.py:current_tenant`). That gating saved the test
suite from pretending Postgres-only logic worked, but it also meant the
v2.36-era `SET LOCAL ... = $1` syntax bug shipped to prod and 500'd
every authenticated /api/* request before anyone noticed.

This module is **gated behind a `postgres` marker** so it only runs when
a real Postgres is wired up via `DATABASE_URL`. Locally:

    DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/flow \\
      uv run pytest backend/tests/test_postgres_rls_smoke.py -m postgres

CI provides Postgres as a service container (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.db.models import Issue, Tenant
from app.db.session import get_db
from app.main import create_app

pytestmark = pytest.mark.postgres


def _have_postgres_url() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith("postgresql")


pytest.importorskip("psycopg", reason="psycopg not installed; install with `uv sync --all-extras`")

if not _have_postgres_url():
    pytest.skip(
        "DATABASE_URL is not set to a postgresql:// URL; this module needs a real Postgres",
        allow_module_level=True,
    )


def _enable_fks(*_args: Any) -> None:  # pragma: no cover — postgres has FKs by default
    pass


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    """Connect to the test Postgres and run all migrations against it.
    Module-scoped so the migration cost is paid once."""
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url, future=True)

    # Run the actual Alembic migrations end-to-end. This is the load-bearing
    # part of the smoke test — if the RLS migration syntax is wrong, this
    # fails BEFORE the test body runs.
    from alembic.config import Config

    from alembic import command

    cfg = Config()
    cfg.set_main_option("script_location", "backend/alembic")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    yield engine
    engine.dispose()


TENANT_KEY_A = "smoke-tenant-a"
TENANT_KEY_B = "smoke-tenant-b"


class _InjectTenantMiddleware(BaseHTTPMiddleware):
    """Sets `request.state.tenant` to mimic what JWTAuthMiddleware does
    in prod, so `current_tenant` (and its `set_config(...)` call) runs
    end-to-end. This is the load-bearing piece — without it the
    `current_tenant_context` override would bypass the GUC binding and
    the smoke test wouldn't actually catch the bug it claims to catch.
    """

    def __init__(self, app: Any, *, get_tenant: Any) -> None:
        super().__init__(app)
        self._get_tenant = get_tenant

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/api/"):
            request.state.tenant = self._get_tenant()
        return await call_next(request)


@pytest.fixture
def pg_client(pg_engine: Engine) -> Iterator[TestClient]:
    """A TestClient with the Postgres engine wired in + a middleware that
    populates `request.state.tenant` so the real `current_tenant`
    dependency (the one that calls `set_config(...)`) runs against
    Postgres on every authenticated request. Seeds two tenants so we
    can verify cross-tenant RLS isolation, not just that the GUC
    binding doesn't crash."""
    SessionFactory = sessionmaker(
        bind=pg_engine, autoflush=False, expire_on_commit=False, future=True
    )

    # Truncate the tables we touch so successive test runs don't collide.
    # CASCADE handles all the FKs.
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE tenants RESTART IDENTITY CASCADE"))

    seed = SessionFactory()
    for key in (TENANT_KEY_A, TENANT_KEY_B):
        seed.add(
            Tenant(
                client_key=key,
                cloud_id=f"{key}-cloud",
                base_url=f"https://{key}.atlassian.net",
                display_url=f"https://{key}.atlassian.net",
                product_type="jira",
                forge_installation_id=key,
                plan="free",
                enabled=True,
                installed_at=datetime.now(UTC),
            )
        )
    seed.flush()
    # Seed one issue per tenant so RLS has something to filter on.
    for key in (TENANT_KEY_A, TENANT_KEY_B):
        seed.add(
            Issue(
                tenant_id=key,
                id=f"{key}-1",
                key=f"{key.upper()}-1",
                project_key="X",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                current_status="In Progress",
            )
        )
    seed.commit()
    seed.close()

    test_app = create_app(with_jwt_middleware=False)

    def _override_db() -> Iterator[Session]:
        s = SessionFactory()
        try:
            yield s
        finally:
            s.close()

    test_app.dependency_overrides[get_db] = _override_db

    # Inject the Tenant ORM row into request.state so `current_tenant`
    # (un-overridden) reads it, calls SET LOCAL via the dependency, and
    # the smoke test exercises the full GUC-binding code path.
    def _get_tenant_a() -> Tenant:
        s = SessionFactory()
        try:
            t = s.get(Tenant, TENANT_KEY_A)
            assert t is not None
            return t
        finally:
            s.close()

    test_app.add_middleware(_InjectTenantMiddleware, get_tenant=_get_tenant_a)

    return TestClient(test_app)


def test_authenticated_endpoint_returns_200_under_real_postgres(
    pg_client: TestClient,
) -> None:
    """Regression catcher for the SET LOCAL = $1 syntax bug: hits an
    authenticated endpoint that runs through `current_tenant`, which
    runs the `set_config('app.current_tenant', :t, true)` SQL. If that
    SQL has a syntax error, this returns 500. Today's fix made it
    return 200 again."""
    res = pg_client.get("/api/issues")
    assert res.status_code == 200, (
        f"Expected 200; got {res.status_code} — likely the GUC-binding "
        f"SQL broke. Body: {res.text[:200]}"
    )


def test_rls_isolates_tenants_at_the_database_layer(pg_client: TestClient) -> None:
    """When the tenant context is set to A, RLS should hide B's row even
    though both rows live in the same table. App-side filtering ALSO
    excludes them, so this isn't a stronger guarantee — but it confirms
    the policy is in place and the GUC is being honored."""
    res = pg_client.get("/api/issues")
    assert res.status_code == 200
    issues = res.json()
    assert len(issues) == 1
    assert issues[0]["id"] == f"{TENANT_KEY_A}-1"
    # Confirm tenant B's issue isn't surfaced.
    assert all(i["id"] != f"{TENANT_KEY_B}-1" for i in issues)
