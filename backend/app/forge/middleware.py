"""Forge auth middleware.

Validates the Forge Invocation Token on every non-skip-listed request and
binds the result to `request.state`:

- `request.state.forge_ctx` — the verified ForgeContext (cloud_id,
  installation_id, app_id).
- `request.state.tenant` — the resolved Tenant ORM row, lazy-upserted
  per ADR-0019 (no separate Forge `installed` webhook).

Routes hitting `/api/forge/lifecycle/uninstalled` get only `forge_ctx`
bound — the route itself does the deletion, so we skip the upsert there
to avoid recreating a row we're about to delete.

While the Forge migration is in flight the middleware is opt-in: enabled
only when `FORGE_APP_ID` is configured. That keeps the Connect path
untouched until it is retired.

ADR-0019 supersedes ADR-0016. The fail-closed-default-with-skip-list
shape carries forward; only the verification primitive changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.logging import get_logger
from app.forge.fit_auth import (
    ForgeAuthError,
    SigningKeyResolver,
    verify_fit,
)
from app.forge.lifecycle import upsert_forge_tenant

logger = get_logger(__name__)


SKIP_PATH_PREFIXES: tuple[str, ...] = (
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
)


# Paths where we validate the FIT but skip the tenant upsert. The uninstall
# route hard-deletes the tenant; upserting first would resurrect a row
# we're about to remove (and on retries, repeatedly so).
NO_UPSERT_PATHS: tuple[str, ...] = ("/api/forge/lifecycle/uninstalled",)


def _should_skip(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in SKIP_PATH_PREFIXES)


def _extract_bearer_token(authorization_header: str | None) -> str | None:
    """Pull the FIT out of an `Authorization: Bearer <token>` header."""
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() not in ("bearer", "jwt"):
        return None
    return token.strip() or None


def _should_upsert(path: str) -> bool:
    return path not in NO_UPSERT_PATHS


def _unauthorized(detail: str) -> Response:
    return JSONResponse({"detail": detail}, status_code=401)


class ForgeAuthMiddleware(BaseHTTPMiddleware):
    """Verifies Forge Invocation Tokens and binds context + tenant to request state."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        forge_app_id: str,
        session_factory: Callable[[], Session],
        resolver: SigningKeyResolver | None = None,
    ) -> None:
        super().__init__(app)
        self.forge_app_id = forge_app_id
        self.session_factory = session_factory
        self.resolver = resolver

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if _should_skip(path):
            return await call_next(request)

        token = _extract_bearer_token(request.headers.get("authorization"))
        if not token:
            return _unauthorized("Missing FIT")

        try:
            ctx = verify_fit(
                token,
                expected_audience=self.forge_app_id,
                resolver=self.resolver,
            )
        except ForgeAuthError as exc:
            logger.warning("Forge FIT validation failed on %s: %s", path, exc)
            return _unauthorized(str(exc))

        request.state.forge_ctx = ctx

        if _should_upsert(path):
            db = self.session_factory()
            try:
                tenant = upsert_forge_tenant(db, ctx)
                # Detach so route handlers can read the Tenant attributes
                # without holding our session open. Same pattern as the
                # Connect path (ADR-0016).
                db.expunge(tenant)
            finally:
                db.close()
            request.state.tenant = tenant

        return await call_next(request)
