"""ADR-0046 — Path B retroactive backfill of legacy NULL `status_id` rows.

Single endpoint:
  - POST /api/forge/backfill/status-ids — accepts the current Jira status
    list (fetched by the Forge resolver via `api.asApp().requestJira(...)`
    on /rest/api/3/status), builds a name → id lookup, and invokes the
    backfill service. Returns the counts + unresolved names.

The resolver is the right place for the outbound Jira call: it has the
install's Forge OAuth scopes, the call is one shot per backfill button
click, and keeping the backend out of the Jira-auth path matches the
post-ADR-0019 architecture where Forge handles Atlassian-side auth.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.session import get_db
from app.services.status_id_backfill import backfill_legacy_status_ids

router = APIRouter(prefix="/forge/backfill", tags=["forge-backfill"])


class JiraStatus(BaseModel):
    """One status from /rest/api/3/status. Jira returns id as a string;
    we model it as a string too — coercion happens in the resolver if
    the Atlassian API ever flips back to int."""

    id: str
    name: str


class StatusIdBackfillRequest(BaseModel):
    statuses: list[JiraStatus] = Field(
        ...,
        description=(
            "Result of `GET /rest/api/3/status`, fetched by the Forge "
            "resolver via api.asApp().requestJira. The backend builds a "
            "name -> id lookup from this and updates every NULL-id row "
            "whose name matches."
        ),
    )


class StatusIdBackfillResponse(BaseModel):
    updated_transitions: int
    updated_slices: int
    unresolved_names: list[str] = Field(
        default_factory=list,
        description=(
            "Distinct status names that appeared in NULL-id rows but were "
            "not present in the current Jira status list (typically: a "
            "status renamed AND then deleted between the legacy write and "
            "this backfill). Tenant admin can restore the status and "
            "re-run, or accept the orphan."
        ),
    )


@router.post("/status-ids", response_model=StatusIdBackfillResponse, status_code=200)
def backfill_status_ids(
    payload: StatusIdBackfillRequest,
    ctx: TenantContext = Depends(current_tenant_context),
    db: Session = Depends(get_db),
) -> StatusIdBackfillResponse:
    """Populate `status_id` on every legacy NULL row for the calling tenant.

    Idempotent — re-running scopes to still-NULL rows only. See
    `app.services.status_id_backfill` for the dataflow.
    """
    lookup = {s.name: s.id for s in payload.statuses}
    result = backfill_legacy_status_ids(db, ctx.tenant_id, lookup)
    db.commit()
    return StatusIdBackfillResponse(
        updated_transitions=result.updated_transitions,
        updated_slices=result.updated_slices,
        unresolved_names=result.unresolved_names,
    )
