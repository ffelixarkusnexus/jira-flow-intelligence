"""Per-issue Forge endpoints — used by the ADR-0044 Issue View Panel.

Read-only. Reuses existing `time_slices` data the changelog ingestion has
already computed; no scoring, no mutations. The panel surface in the Jira
issue view consumes this endpoint via the issuePanelResolver.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice
from app.db.session import get_db
from app.schemas.api import IssuePanelData, IssuePanelSlice

router = APIRouter(prefix="/forge/issue", tags=["forge-issue"])


@router.get("/{issue_key}/panel-data", response_model=IssuePanelData)
def get_issue_panel_data(
    issue_key: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> IssuePanelData:
    """Return per-issue time-per-status data for the Issue View Panel.

    Tenant-scoped. The `external_blocking` flag on each slice surfaces the
    ADR-0042 marker — the panel renders a visual indicator next to those
    statuses so the engineer triaging the ticket immediately understands
    why their team is "stuck" in that status (it's an external block,
    not a team-controllable delay).
    """
    issue = db.scalar(
        select(Issue).where(
            Issue.tenant_id == ctx.tenant_id,
            (Issue.id == issue_key) | (Issue.key == issue_key),
        )
    )
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_key} not found")

    slices = list(
        db.scalars(
            select(TimeSlice)
            .where(TimeSlice.tenant_id == ctx.tenant_id, TimeSlice.issue_id == issue.id)
            .order_by(TimeSlice.start_at.asc())
        )
    )

    external_blocking_set = {s.casefold() for s in ctx.external_blocking_statuses}

    panel_slices = [
        IssuePanelSlice(
            status=s.status,
            entered_at=s.start_at,
            exited_at=None if s.is_open else s.end_at,
            duration_seconds=s.duration_seconds,
            is_external_blocking=s.status.casefold() in external_blocking_set,
        )
        for s in slices
    ]

    total_cycle = sum(s.duration_seconds for s in slices)

    # Deep-link target on the project dashboard. Forge resolves this against
    # the Atlassian site URL on the frontend; we just emit the relative path
    # the dashboard recognizes.
    project_url = (
        f"/jira/software/projects/{issue.project_key}/boards" if issue.project_key else "/jira"
    )

    return IssuePanelData(
        issue_key=issue.key,
        current_status=issue.current_status or "Unknown",
        status_history=panel_slices,
        total_cycle_time_seconds=total_cycle,
        # `is_in_current_bottleneck` requires a snapshot to determine; computing
        # it on every panel request is wasteful (the bottleneck is project-level
        # and changes on the daily insight refresh, not per-issue). v1 leaves
        # this False; v1.1 can wire it up after caching the per-project named
        # bottleneck. Documented in ADR-0044 §Future considerations.
        is_in_current_bottleneck=False,
        project_dashboard_url=project_url,
    )
