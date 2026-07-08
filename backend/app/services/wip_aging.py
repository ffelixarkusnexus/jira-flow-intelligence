"""WIP Aging chart data.

Returns the list of in-flight tickets with each one's age in its current
status, ready for the bubble-chart Custom UI to render. Plus a P95
cycle-time overlay so users can see which tickets are aging beyond
normal flow.

Definition of "in-flight": `done_at IS NULL` on the Issue row. We
deliberately don't filter by current_status because workflow vocabulary
is per-tenant — anything not yet Done is in flight. The per-tenant
settings UI lets customers narrow this if they want.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Issue
from app.services.working_time import working_seconds_between


@dataclass(frozen=True)
class TicketRow:
    key: str
    summary: str | None
    status: str
    days_in_status: float
    cycle_days: float
    assignee: str | None
    priority: str | None
    story_points: float | None
    issue_type: str | None


@dataclass(frozen=True)
class WipAgingResult:
    tickets: list[TicketRow]
    p95_cycle_days: float | None
    sample_size: int


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile to match what `metrics_service` does."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def compute_wip_aging(
    db: Session,
    ctx: TenantContext,
    *,
    now: datetime | None = None,
    project_key: str | None = None,
) -> WipAgingResult:
    now = now or utcnow()

    # P95 from completed tickets in the last 90 days. 90d covers both
    # short-cycle and longer-cycle work without letting ancient outliers
    # skew the boundary line.
    cutoff = now - timedelta(days=90)
    cycle_stmt = select(Issue.created_at, Issue.done_at).where(
        Issue.tenant_id == ctx.tenant_id,
        Issue.done_at.is_not(None),
        Issue.done_at >= cutoff,
    )
    if project_key:
        cycle_stmt = cycle_stmt.where(Issue.project_key == project_key)
    cycle_rows = db.execute(cycle_stmt).all()
    # ADR-0043: when a tenant has an active work schedule, every duration
    # in this service runs through working_seconds_between. The helper
    # falls back to calendar seconds when schedule is None — existing
    # tenants land here and see no behavior change.
    schedule = ctx.work_schedule
    cycle_days_list = [
        max(working_seconds_between(r.created_at, r.done_at, schedule), 0) / 86400.0
        for r in cycle_rows
    ]
    p95 = _percentile(cycle_days_list, 0.95) if cycle_days_list else None

    # Pull in-flight issues + their full transition history. SQLAlchemy
    # eager-loads transitions via the configured relationship; we sort by
    # transitioned_at to find the most recent `to_status == current_status`.
    issue_stmt = select(Issue).where(Issue.tenant_id == ctx.tenant_id, Issue.done_at.is_(None))
    if project_key:
        issue_stmt = issue_stmt.where(Issue.project_key == project_key)
    issues = db.scalars(issue_stmt).all()

    tickets: list[TicketRow] = []
    for issue in issues:
        if issue.current_status is None:
            continue
        # The "current status" entry timestamp = max(transitioned_at) where
        # to_status == current_status. Fallback: created_at (issue has been
        # in its initial status the whole time).
        latest_entry: datetime = issue.created_at
        for t in issue.transitions:
            if t.to_status == issue.current_status and t.transitioned_at > latest_entry:
                latest_entry = t.transitioned_at
        days_in_status = max(working_seconds_between(latest_entry, now, schedule), 0) / 86400.0
        cycle_days = max(working_seconds_between(issue.created_at, now, schedule), 0) / 86400.0

        tickets.append(
            TicketRow(
                key=issue.key,
                summary=issue.summary,
                status=issue.current_status,
                days_in_status=round(days_in_status, 2),
                cycle_days=round(cycle_days, 2),
                assignee=issue.assignee,
                priority=issue.priority,
                story_points=issue.story_points,
                issue_type=issue.issue_type,
            )
        )

    # Most-aged first — the chart can re-sort but this gives a sane default
    # for any caller that consumes the list in order.
    tickets.sort(key=lambda t: -t.days_in_status)
    return WipAgingResult(
        tickets=tickets,
        p95_cycle_days=round(p95, 2) if p95 is not None else None,
        sample_size=len(cycle_days_list),
    )
