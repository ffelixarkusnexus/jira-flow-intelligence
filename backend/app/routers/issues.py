from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Issue, IssueMetric, TimeSlice
from app.db.session import get_db
from app.schemas.api import IssueDetailOut, IssueOut, TimeSliceOut

router = APIRouter(prefix="/issues", tags=["issues"])


def _serialize_issue(issue: Issue, metric: IssueMetric | None) -> dict:
    return {
        "id": issue.id,
        "key": issue.key,
        "project_key": issue.project_key,
        "summary": issue.summary,
        "issue_type": issue.issue_type,
        "current_status": issue.current_status,
        "created_at": issue.created_at,
        "updated_at": issue.updated_at,
        "done_at": issue.done_at,
        "cycle_seconds": metric.cycle_seconds if metric else None,
        "active_seconds": metric.active_seconds if metric else None,
        "wait_seconds": metric.wait_seconds if metric else None,
    }


@router.get("", response_model=list[IssueOut])
def list_issues(
    project: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, le=2000),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> list[IssueOut]:
    stmt = select(Issue).where(Issue.tenant_id == ctx.tenant_id)
    if project:
        stmt = stmt.where(Issue.project_key == project)
    if status:
        stmt = stmt.where(Issue.current_status == status)
    stmt = stmt.order_by(Issue.updated_at.desc()).limit(limit)
    issues = list(db.scalars(stmt))
    metrics = {
        m.issue_id: m
        for m in db.scalars(
            select(IssueMetric).where(
                IssueMetric.tenant_id == ctx.tenant_id,
                IssueMetric.issue_id.in_([i.id for i in issues]),
            )
        )
    }
    return [IssueOut(**_serialize_issue(i, metrics.get(i.id))) for i in issues]


@router.get("/{key_or_id}", response_model=IssueDetailOut)
def get_issue(
    key_or_id: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> IssueDetailOut:
    issue = db.scalar(
        select(Issue).where(
            Issue.tenant_id == ctx.tenant_id,
            (Issue.id == key_or_id) | (Issue.key == key_or_id),
        )
    )
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {key_or_id} not found")
    metric = db.get(IssueMetric, (ctx.tenant_id, issue.id))
    slices = list(
        db.scalars(
            select(TimeSlice)
            .where(TimeSlice.tenant_id == ctx.tenant_id, TimeSlice.issue_id == issue.id)
            .order_by(TimeSlice.start_at.asc())
        )
    )
    return IssueDetailOut(
        **_serialize_issue(issue, metric),
        time_slices=[
            TimeSliceOut(
                status=s.status,
                start_at=s.start_at,
                end_at=s.end_at,
                duration_seconds=s.duration_seconds,
                is_open=s.is_open,
            )
            for s in slices
        ],
    )
