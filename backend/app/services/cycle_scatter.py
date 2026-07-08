"""Cycle Time Scatter chart data.

One dot per completed issue: X = completion date, Y = cycle days. Plus
P50/P85/P95 of the same set as overlay lines so outliers above the
percentiles are visually obvious.

Window default: 90 days, the same window WIP Aging uses for its P95 line
so the two charts agree on what counts as "recent normal" for cycle time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Issue


@dataclass(frozen=True)
class ScatterPoint:
    key: str
    summary: str | None
    completed_at: str  # ISO 8601, easier for the chart to parse
    cycle_days: float
    issue_type: str | None
    priority: str | None
    assignee: str | None


@dataclass(frozen=True)
class CycleScatterResult:
    window_start: datetime
    window_end: datetime
    points: list[ScatterPoint]
    p50_cycle_days: float | None
    p85_cycle_days: float | None
    p95_cycle_days: float | None


def _percentile(values: list[float], p: float) -> float:
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


def compute_cycle_scatter(
    db: Session,
    ctx: TenantContext,
    *,
    days: int = 90,
    now: datetime | None = None,
    project_key: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CycleScatterResult:
    """When `start`/`end` are provided they take precedence over `days` —
    used by sprint/calendar window callers."""
    if end is None:
        end = now or utcnow()
    if start is None:
        start = end - timedelta(days=days)

    stmt = select(Issue).where(
        Issue.tenant_id == ctx.tenant_id,
        Issue.done_at.is_not(None),
        Issue.done_at >= start,
    )
    if project_key:
        stmt = stmt.where(Issue.project_key == project_key)
    rows = db.scalars(stmt).all()

    points: list[ScatterPoint] = []
    cycles: list[float] = []
    for issue in rows:
        # done_at is mapped Optional but the SQL WHERE filtered nulls out;
        # mypy doesn't know that, so widen type-side.
        assert issue.done_at is not None
        cycle_days = max((issue.done_at - issue.created_at).total_seconds(), 0.0) / 86400.0
        cycles.append(cycle_days)
        points.append(
            ScatterPoint(
                key=issue.key,
                summary=issue.summary,
                completed_at=issue.done_at.isoformat(),
                cycle_days=round(cycle_days, 2),
                issue_type=issue.issue_type,
                priority=issue.priority,
                assignee=issue.assignee,
            )
        )

    return CycleScatterResult(
        window_start=start,
        window_end=end,
        points=sorted(points, key=lambda p: p.completed_at),
        p50_cycle_days=round(_percentile(cycles, 0.5), 2) if cycles else None,
        p85_cycle_days=round(_percentile(cycles, 0.85), 2) if cycles else None,
        p95_cycle_days=round(_percentile(cycles, 0.95), 2) if cycles else None,
    )
