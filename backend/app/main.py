"""FastAPI app construction.

Exposes both `create_app(...)` (factory used by tests with custom wiring) and
`app` (the default instance imported by `uvicorn app.main:app`).

Tests that exercise the auth middleware use the default `app` (or build their
own with the middleware enabled). Tests that exercise router behavior in
isolation construct their own app via `create_app(with_jwt_middleware=False)`
and override dependencies.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import init_db
from app.db.session import SessionLocal
from app.forge.middleware import ForgeAuthMiddleware
from app.routers import (
    alerts,
    dev,
    exports,
    forge_backfill,
    forge_issue,
    forge_lifecycle,
    forge_schedule,
    forge_sync,
    insights,
    issues,
    metrics,
    personal_data,
    sprints,
    sync,
)
from app.routers import (
    settings as settings_router,
)

# Configure structlog at import time so any module-level loggers
# elsewhere pick up the same renderer.
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Production deploys run `alembic upgrade head` in the Docker CMD before
    # uvicorn starts, so by the time we get here the schema is current. The
    # init_db() call below is the legacy local-dev path: it no-ops against an
    # existing schema and creates tables for the in-memory SQLite engines
    # tests build.
    init_db()
    yield


def create_app(
    *,
    with_jwt_middleware: bool = True,
    settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> FastAPI:
    """Build a FastAPI app.

    `with_jwt_middleware=False` skips the auth middleware — used by router
    tests that bypass auth via FastAPI's `dependency_overrides`. The
    middleware itself is exercised separately in `test_forge_middleware.py`.
    """
    resolved_settings = settings or get_settings()
    resolved_factory = session_factory or SessionLocal

    fa = FastAPI(
        title="Jira Flow Intelligence",
        description=(
            "Deterministic flow metrics, bottleneck detection, and alerting from Jira changelogs."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )

    # Middleware order: Starlette runs the LAST-added middleware OUTERMOST.
    # Adding Forge auth first then CORS makes CORS outermost, which:
    #   - intercepts OPTIONS preflights without going through auth, and
    #   - adds CORS headers to every response (including 401s).
    # See ADR-0019.
    if with_jwt_middleware:
        if not resolved_settings.forge_app_id:
            # Production deploys set FORGE_APP_ID from SSM via the App Runner
            # env var. Local imports without it
            # (e.g. tests, ad-hoc REPL) get an unauthenticated app — fine,
            # since tests construct their own app with overrides.
            logger.warning("FORGE_APP_ID not set — running without auth middleware")
        else:
            fa.add_middleware(
                ForgeAuthMiddleware,
                forge_app_id=resolved_settings.forge_app_id,
                session_factory=resolved_factory,
            )
    fa.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @fa.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        return {"status": "ok"}

    # All application routes live under /api so the Forge bridge's
    # `requestRemote` calls land on a single, well-known prefix.
    api = APIRouter(prefix="/api")
    api.include_router(sync.router)
    api.include_router(issues.router)
    api.include_router(metrics.router)
    api.include_router(insights.router)
    api.include_router(alerts.router)
    api.include_router(settings_router.router)
    api.include_router(sprints.router)
    api.include_router(forge_lifecycle.router)
    api.include_router(forge_sync.router)
    api.include_router(forge_issue.router)
    api.include_router(forge_schedule.router)
    api.include_router(forge_backfill.router)
    api.include_router(personal_data.router)
    api.include_router(exports.router)
    if resolved_settings.allow_demo_seed:
        api.include_router(dev.router)
    fa.include_router(api)

    return fa


app = create_app()
