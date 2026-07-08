from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Issue, IssueMetric, StatusWindowMetric, TimeSlice


@dataclass
class IssueMetricResult:
    tenant_id: str
    issue_id: str
    cycle_seconds: int
    active_seconds: int
    wait_seconds: int
    is_done: bool


@dataclass
class StatusWindowResult:
    status: str
    window_start: datetime
    window_end: datetime
    avg_seconds: float
    p50_seconds: float
    p90_seconds: float
    wip_avg: float
    throughput: int
    sample_size: int


@dataclass
class WindowSnapshot:
    window_start: datetime
    window_end: datetime
    statuses: dict[str, StatusWindowResult] = field(default_factory=dict)


def compute_issue_metrics(
    issue: Issue, slices: Iterable[TimeSlice], ctx: TenantContext, now: datetime | None = None
) -> IssueMetricResult:
    if now is None:
        now = utcnow()

    active_set = set(ctx.active_statuses)
    active_seconds = 0
    for s in slices:
        if s.status in active_set:
            active_seconds += s.duration_seconds

    if issue.done_at is not None:
        cycle_seconds = int((issue.done_at - issue.created_at).total_seconds())
        is_done = True
    else:
        cycle_seconds = int((now - issue.created_at).total_seconds())
        is_done = False

    wait_seconds = max(cycle_seconds - active_seconds, 0)

    return IssueMetricResult(
        tenant_id=issue.tenant_id,
        issue_id=issue.id,
        cycle_seconds=cycle_seconds,
        active_seconds=active_seconds,
        wait_seconds=wait_seconds,
        is_done=is_done,
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = pct * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _slice_overlap_seconds(
    slice_start: datetime, slice_end: datetime, win_start: datetime, win_end: datetime
) -> float:
    start = max(slice_start, win_start)
    end = min(slice_end, win_end)
    if end <= start:
        return 0.0
    return (end - start).total_seconds()


def compute_status_window(
    session: Session,
    tenant_id: str,
    status: str,
    window_start: datetime,
    window_end: datetime,
    *,
    project_key: str | None = None,
) -> StatusWindowResult:
    """Status metrics for a single (tenant, status) within a window. Used by
    callers (alerts router, ad-hoc queries) that already know a specific
    casing. The dashboard's snapshot path goes through
    `compute_window_snapshot` which groups case-equivalent variants.

    `project_key` scopes to a single Jira project — the standard
    behavior for a `jira:projectPage` plugin. None = tenant-wide.
    """
    return _compute_status_window_for_variants(
        session, tenant_id, status, [status], window_start, window_end, project_key=project_key
    )


def _compute_status_window_for_variants(
    session: Session,
    tenant_id: str,
    display_name: str,
    variants: list[str],
    window_start: datetime,
    window_end: datetime,
    *,
    project_key: str | None = None,
) -> StatusWindowResult:
    """Aggregate slices across one or more case-equivalent status names."""
    stmt = select(TimeSlice).where(TimeSlice.tenant_id == tenant_id, TimeSlice.status.in_(variants))
    if project_key:
        stmt = stmt.join(
            Issue,
            (TimeSlice.tenant_id == Issue.tenant_id) & (TimeSlice.issue_id == Issue.id),
        ).where(Issue.project_key == project_key)
    slices = session.scalars(stmt).all()

    durations: list[float] = []
    distinct_completing: set[str] = set()
    overlap_total = 0.0
    window_seconds = (window_end - window_start).total_seconds()
    if window_seconds <= 0:
        window_seconds = 1.0

    for s in slices:
        if window_start <= s.end_at <= window_end and not s.is_open:
            durations.append(float(s.duration_seconds))
            distinct_completing.add(s.issue_id)
        overlap_total += _slice_overlap_seconds(s.start_at, s.end_at, window_start, window_end)

    avg = float(sum(durations) / len(durations)) if durations else 0.0
    p50 = _percentile(durations, 0.5)
    p90 = _percentile(durations, 0.9)
    wip_avg = overlap_total / window_seconds
    throughput = len(distinct_completing)

    return StatusWindowResult(
        status=display_name,
        window_start=window_start,
        window_end=window_end,
        avg_seconds=avg,
        p50_seconds=p50,
        p90_seconds=p90,
        wip_avg=wip_avg,
        throughput=throughput,
        sample_size=len(durations),
    )


def discover_statuses(
    session: Session, tenant_id: str, *, project_key: str | None = None
) -> list[str]:
    stmt = select(TimeSlice.status).where(TimeSlice.tenant_id == tenant_id).distinct()
    if project_key:
        stmt = stmt.join(
            Issue,
            (TimeSlice.tenant_id == Issue.tenant_id) & (TimeSlice.issue_id == Issue.id),
        ).where(Issue.project_key == project_key)
    rows = session.execute(stmt).all()
    return sorted({r[0] for r in rows if r[0]})


def discover_status_groups(
    session: Session, tenant_id: str, *, project_key: str | None = None
) -> list[tuple[str, list[str]]]:
    """Group equivalent statuses across renames and case differences.

    Returns `(display_name, variants)` tuples sorted by display_name.

    Two-pass grouping (ADR-0045):

    1. **ID-keyed pass.** For slices with non-NULL `status_id`, group by ID.
       Display name = the variant with the most-recent `MAX(end_at)` (current
       name post-rename). All historical name variants for that ID are
       returned in `variants` so downstream `WHERE status IN (variants)`
       queries pick up the renamed history correctly. This is the path the
       article's *"join on ID, render name"* prescription describes.
    2. **Name-keyed fallback.** For slices with NULL `status_id` (legacy data
       written before the column existed), fall back to the pre-ADR-0045
       case-folded name grouping. These groups can't be merged with the
       ID-keyed groups — that's the bound the legacy data sets, closed by
       the Path B historical backfill when scheduled.

    Names that appear in both passes (a status that has both legacy NULL-id
    rows AND new id-populated rows under the same current name) are merged
    under the ID-keyed group so the chart renders one row, not two.
    """
    stmt = (
        select(
            TimeSlice.status,
            TimeSlice.status_id,
            func.count(TimeSlice.id),
            func.max(TimeSlice.end_at).label("latest_end"),
        )
        .where(TimeSlice.tenant_id == tenant_id, TimeSlice.status.is_not(None))
        .group_by(TimeSlice.status, TimeSlice.status_id)
    )
    if project_key:
        stmt = stmt.join(
            Issue,
            (TimeSlice.tenant_id == Issue.tenant_id) & (TimeSlice.issue_id == Issue.id),
        ).where(Issue.project_key == project_key)
    rows = session.execute(stmt).all()

    # Pass 1: ID-keyed groups. Track (status_id) -> list of (name, count, latest_end).
    id_groups: dict[str, list[tuple[str, int, datetime]]] = {}
    # Pass 2: name-fallback rows (status_id IS NULL). Same shape as before
    # the ADR-0045 change.
    name_only: list[tuple[str, int]] = []
    # Names already claimed by an ID-keyed group — used to merge a name's
    # NULL-id rows into the ID-keyed bucket when the name matches the
    # current display name.
    name_to_id: dict[str, str] = {}

    for status, status_id, count, latest_end in rows:
        if not status:
            continue
        if status_id is not None:
            id_groups.setdefault(status_id, []).append((status, int(count), latest_end))
            name_to_id[status] = status_id
        else:
            name_only.append((status, int(count)))

    result: list[tuple[str, list[str]]] = []

    for variants in id_groups.values():
        # Display name = the variant with the most-recent end_at (the current
        # post-rename name). Tiebreak by row count, then alphabetical.
        variants.sort(key=lambda v: (v[2], v[1], v[0]), reverse=True)
        display = variants[0][0]
        names = [v[0] for v in variants]
        result.append((display, names))

    # Fold NULL-id rows into existing ID-keyed groups when the name matches
    # any variant the ID-keyed group already lists. Otherwise treat as a
    # legacy-only group via the case-folded fallback.
    legacy_groups: dict[str, list[tuple[str, int]]] = {}
    for status, count in name_only:
        if status in name_to_id:
            # Same name lives in an id-keyed group too — merge so the chart
            # renders one row. The (status, NULL-id) slices will be pulled
            # in by the variants `WHERE status IN (...)` filter at query time.
            continue
        legacy_groups.setdefault(status.casefold(), []).append((status, count))

    for variants_legacy in legacy_groups.values():
        variants_legacy.sort(key=lambda v: (-v[1], v[0]))
        display = variants_legacy[0][0]
        result.append((display, [v[0] for v in variants_legacy]))

    result.sort(key=lambda g: g[0])
    return result


def compute_window_snapshot(
    session: Session,
    tenant_id: str,
    window_start: datetime,
    window_end: datetime,
    statuses: list[str] | None = None,
    *,
    project_key: str | None = None,
) -> WindowSnapshot:
    snap = WindowSnapshot(window_start=window_start, window_end=window_end)
    if statuses is None:
        # Default path: group case-equivalent variants. Dashboard renders one
        # row per group instead of one per Jira-stored casing.
        for display, variants in discover_status_groups(
            session, tenant_id, project_key=project_key
        ):
            snap.statuses[display] = _compute_status_window_for_variants(
                session,
                tenant_id,
                display,
                variants,
                window_start,
                window_end,
                project_key=project_key,
            )
        return snap
    # Explicit-status path: caller knows what it wants, no grouping. Used by
    # tests and any caller that still passes a hand-curated list.
    for status in statuses:
        snap.statuses[status] = compute_status_window(
            session, tenant_id, status, window_start, window_end, project_key=project_key
        )
    return snap


def cycle_time_throughput(
    session: Session,
    tenant_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    project_key: str | None = None,
) -> tuple[int, list[int]]:
    """System-wide throughput for a tenant: count of issues with done_at in window."""
    stmt = select(Issue.id, Issue.created_at, Issue.done_at).where(
        Issue.tenant_id == tenant_id,
        Issue.done_at.is_not(None),
        Issue.done_at >= window_start,
        Issue.done_at <= window_end,
    )
    if project_key:
        stmt = stmt.where(Issue.project_key == project_key)
    rows = session.execute(stmt).all()
    cycle_times = [int((r.done_at - r.created_at).total_seconds()) for r in rows]
    return len(rows), cycle_times


def persist_issue_metrics(
    session: Session, results: Iterable[IssueMetricResult], now: datetime | None = None
) -> int:
    if now is None:
        now = utcnow()
    count = 0
    for r in results:
        existing = session.get(IssueMetric, (r.tenant_id, r.issue_id))
        if existing is None:
            existing = IssueMetric(tenant_id=r.tenant_id, issue_id=r.issue_id)
            session.add(existing)
        existing.cycle_seconds = r.cycle_seconds
        existing.active_seconds = r.active_seconds
        existing.wait_seconds = r.wait_seconds
        existing.is_done = r.is_done
        existing.computed_at = now
        count += 1
    return count


def persist_status_window(
    session: Session, tenant_id: str, results: Iterable[StatusWindowResult]
) -> int:
    count = 0
    for r in results:
        existing = session.execute(
            select(StatusWindowMetric).where(
                StatusWindowMetric.tenant_id == tenant_id,
                StatusWindowMetric.status == r.status,
                StatusWindowMetric.window_start == r.window_start,
                StatusWindowMetric.window_end == r.window_end,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = StatusWindowMetric(
                tenant_id=tenant_id,
                status=r.status,
                window_start=r.window_start,
                window_end=r.window_end,
            )
            session.add(existing)
        existing.avg_seconds = r.avg_seconds
        existing.p50_seconds = r.p50_seconds
        existing.p90_seconds = r.p90_seconds
        existing.wip_avg = r.wip_avg
        existing.throughput = r.throughput
        existing.sample_size = r.sample_size
        count += 1
    return count


def recompute_all_issue_metrics(session: Session, ctx: TenantContext) -> int:
    issues = session.scalars(select(Issue).where(Issue.tenant_id == ctx.tenant_id)).all()
    results: list[IssueMetricResult] = []
    now = utcnow()
    for issue in issues:
        results.append(compute_issue_metrics(issue, issue.time_slices, ctx, now=now))
    return persist_issue_metrics(session, results, now=now)


def recompute_issue_metrics_for(
    session: Session, ctx: TenantContext, issue_ids: Iterable[str]
) -> int:
    """Recompute metrics for a specific set of issue IDs only.

    The sync path uses this so an ingest of N changed issues costs O(N), not
    O(total tenant issues). Important once a tenant grows past a few hundred
    issues — recompute_all_issue_metrics gets slow enough to push a Forge
    resolver call past its 25s limit.
    """
    ids = list(issue_ids)
    if not ids:
        return 0
    issues = session.scalars(
        select(Issue).where(Issue.tenant_id == ctx.tenant_id, Issue.id.in_(ids))
    ).all()
    results: list[IssueMetricResult] = []
    now = utcnow()
    for issue in issues:
        results.append(compute_issue_metrics(issue, issue.time_slices, ctx, now=now))
    return persist_issue_metrics(session, results, now=now)


def default_windows(
    now: datetime | None = None, days: int = 7
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    if now is None:
        now = utcnow()
    cur_end = now
    cur_start = cur_end - timedelta(days=days)
    prev_end = cur_start
    prev_start = prev_end - timedelta(days=days)
    return (cur_start, cur_end), (prev_start, prev_end)


def calendar_windows(
    period: str, now: datetime | None = None
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Calendar-bucketed windows (requested by a pilot admin).

    `period` is one of:

    - "mtd" — current = first-of-current-month → now; previous = previous
      full calendar month.
    - "qtd" — current = first-of-current-quarter → now; previous = previous
      full calendar quarter.

    Quarters are anchored on Jan/Apr/Jul/Oct.
    """
    if now is None:
        now = utcnow()

    if period == "mtd":
        cur_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_of_prev = cur_start - timedelta(seconds=1)
        prev_start = last_of_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (cur_start, now), (prev_start, cur_start)

    if period == "qtd":
        q_start_month = ((now.month - 1) // 3) * 3 + 1
        cur_start = now.replace(
            month=q_start_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        last_of_prev = cur_start - timedelta(seconds=1)
        prev_q_start_month = ((last_of_prev.month - 1) // 3) * 3 + 1
        prev_start = last_of_prev.replace(
            month=prev_q_start_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return (cur_start, now), (prev_start, cur_start)

    raise ValueError(f"unknown period: {period!r}")
