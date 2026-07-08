"""Dev-only endpoints. Mounted on the backend ONLY when
`Settings.allow_demo_seed` is true (set on the dev backend's App Runner
env vars; never on prod).

Exists so a freshly-installed Forge tenant can render a populated
dashboard (bottleneck, alerts, trends) without waiting on a real Jira
sync. It loads the 250-issue Review-stage-bottleneck dataset in
`app.seeds.demo`, designed for demo screenshots and manual UI exercise.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Tenant
from app.db.session import get_db
from app.seeds.demo import DEFAULT_DEMO_PROJECT_KEY, seed_demo_data_for_tenant

logger = get_logger(__name__)

router = APIRouter(prefix="/dev", tags=["dev"])

MARKETPLACE_FIXTURE_NAME = "marketplace"
_VALID_FIXTURES = (MARKETPLACE_FIXTURE_NAME,)


@router.post("/seed-demo", status_code=200)
def seed_demo(
    request: Request,
    project_key: str = Query(
        default=DEFAULT_DEMO_PROJECT_KEY,
        description=(
            "Jira project key the synthetic issues should be bucketed under. "
            "Pass the project the caller is currently viewing — the dashboard "
            "filters every chart query by project_key, so a mismatch here "
            "produces a successful seed but an empty dashboard."
        ),
    ),
    fixture: str = Query(
        default=MARKETPLACE_FIXTURE_NAME,
        description=(
            "Which seed fixture to load. 'marketplace' (default) loads the "
            "250-issue Review-stage bottleneck dataset."
        ),
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, int | str | list[str]]:
    if not settings.allow_demo_seed:
        # Belt-and-suspenders: the router is only mounted when this is true,
        # but if it ever flips off we want a clean 404 rather than a stack.
        raise HTTPException(status_code=404, detail="Not found")

    if fixture not in _VALID_FIXTURES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown fixture '{fixture}'. Valid: {', '.join(_VALID_FIXTURES)}",
        )

    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="No tenant context")

    report = seed_demo_data_for_tenant(db, tenant, settings, project_key=project_key)
    db.commit()
    logger.info(
        "Seeded marketplace fixture for tenant=%s project=%s issues=%s transitions=%s slices=%s",
        tenant.client_key,
        project_key,
        report.issues_processed,  # type: ignore[attr-defined]
        report.transitions_written,  # type: ignore[attr-defined]
        report.slices_written,  # type: ignore[attr-defined]
    )
    return {
        "fixture": MARKETPLACE_FIXTURE_NAME,
        "issues": report.issues_processed,  # type: ignore[attr-defined]
        "transitions": report.transitions_written,  # type: ignore[attr-defined]
        "slices": report.slices_written,  # type: ignore[attr-defined]
    }
