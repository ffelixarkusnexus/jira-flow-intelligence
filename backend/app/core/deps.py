"""FastAPI dependencies — the seam where authentication binds a tenant to a request.

In production (Phase 1, after Stream 2 lands JWT auth), `JWTAuthMiddleware` sets
`request.state.tenant` from the verified Atlassian JWT. The `current_tenant`
dependency reads it.

For tests we override this dependency directly via `app.dependency_overrides`.
There is no header-based bypass in production code — the only way to bind a
tenant is through the JWT path.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.tenant_context import TenantContext
from app.db.models import Tenant
from app.db.session import get_db


def current_tenant(request: Request, db: Session = Depends(get_db)) -> Tenant:
    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="No tenant context on request.")
    # Bind the tenant identifier to the Postgres session so RLS
    # policies (current_setting('app.current_tenant')) can apply.
    #
    # Uses set_config(setting, value, is_local=true) instead of `SET LOCAL`
    # — the SET statement does NOT support bound parameters in Postgres
    # (it's parsed at parse time, before parameter substitution), which
    # we discovered the hard way: prod 500'd on every authenticated
    # request with `syntax error at or near "$1"`. set_config is a
    # function call, accepts parameters cleanly, and the third arg=true
    # gives the same transaction-scope semantics as SET LOCAL.
    #
    # Skipped on SQLite (which doesn't support GUCs); the test fixtures
    # override `current_tenant` anyway, so RLS isn't the test path.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": tenant.client_key},
        )
    return tenant


def current_tenant_context(
    tenant: Tenant = Depends(current_tenant),
    settings: Settings = Depends(get_settings),
) -> TenantContext:
    return TenantContext(tenant=tenant, settings=settings)
