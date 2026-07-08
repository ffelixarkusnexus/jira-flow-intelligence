"""Cumulative Flow Diagram data.

For each day in the requested window, count the number of issues sitting
in each status at end-of-day. Time slices (`status, start_at, end_at`
per issue) are the source of truth — a slice that overlaps a given day
contributes its issue to that day's count for that status.

Statuses are case-folded into the same groups `metrics_service` uses, so
"Code Review" and "CODE REVIEW" stack on the same band.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice
from app.services.metrics_service import discover_status_groups


@dataclass(frozen=True)
class DayBucket:
    date: str  # YYYY-MM-DD, end of day
    by_status: dict[str, int]


@dataclass(frozen=True)
class CfdResult:
    window_start: datetime
    window_end: datetime
    statuses: list[str]
    days: list[DayBucket]


def compute_cfd(
    db: Session,
    ctx: TenantContext,
    *,
    days: int = 30,
    now: datetime | None = None,
    project_key: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CfdResult:
    """When `start`/`end` are provided they take precedence over `days` —
    used by sprint/calendar window callers that want explicit bounds."""
    if end is None:
        end = now or utcnow()
    if start is None:
        start = end - timedelta(days=days)
    # Recompute days from explicit bounds so the per-day bucket loop below
    # doesn't over- or under-shoot. Floor at 1 to avoid an empty result on
    # zero-length windows.
    days = max(1, int((end - start).total_seconds() / 86400))

    # Exclude terminal statuses (Done, Won't Do, Cancelled, Rejected, etc.)
    # so the chart shows *flow*. Terminal bands grow forever and visually
    # drown the in-flight stages — a pilot admin flagged this as the CFD's
    # most misleading default. Per-tenant override on `tenants.terminal_statuses`.
    terminal_set = {s.casefold() for s in ctx.terminal_statuses}

    groups = discover_status_groups(db, ctx.tenant_id, project_key=project_key)
    groups = [
        (display, variants)
        for display, variants in groups
        if display.casefold() not in terminal_set
    ]
    # Build a variant -> display_name lookup so we can collapse slices into
    # the canonical group on the fly.
    variant_to_display: dict[str, str] = {}
    for display, variants in groups:
        for v in variants:
            variant_to_display[v] = display
    statuses = [display for display, _ in groups]

    # Pull every slice that *might* overlap the window — start_at <= window_end
    # AND end_at >= window_start. Filtering at the DB cuts the Python loop.
    slice_stmt = select(TimeSlice).where(
        TimeSlice.tenant_id == ctx.tenant_id,
        TimeSlice.start_at <= end,
        TimeSlice.end_at >= start,
    )
    if project_key:
        slice_stmt = slice_stmt.join(
            Issue,
            (TimeSlice.tenant_id == Issue.tenant_id) & (TimeSlice.issue_id == Issue.id),
        ).where(Issue.project_key == project_key)
    slices = db.scalars(slice_stmt).all()

    # For each day end (start+1d, start+2d, ..., end), count distinct issues
    # per status. We use end-of-day so the bottom of the chart sits at "what
    # was open by close of business that day."
    day_ends: list[datetime] = []
    for i in range(1, days + 1):
        day_ends.append(start + timedelta(days=i))

    buckets: list[DayBucket] = []
    for d in day_ends:
        # status -> set of issue_ids
        per_status: defaultdict[str, set[str]] = defaultdict(set)
        for s in slices:
            if not (s.start_at <= d <= s.end_at):
                continue
            # Slice belongs to a terminal status the user excluded — skip it
            # rather than inventing a band.
            slice_display = variant_to_display.get(s.status)
            if slice_display is None:
                continue
            per_status[slice_display].add(s.issue_id)
        buckets.append(
            DayBucket(
                date=d.strftime("%Y-%m-%d"),
                by_status={k: len(v) for k, v in per_status.items()},
            )
        )

    return CfdResult(window_start=start, window_end=end, statuses=statuses, days=buckets)
