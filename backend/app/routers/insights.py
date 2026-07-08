from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.session import get_db
from app.schemas.api import (
    BottleneckOut,
    CfdDay,
    CfdResponse,
    CycleScatterResponse,
    InsightResponse,
    ScatterPointOut,
    StatusSignalOut,
    TrendOut,
    WipAgingResponse,
    WipAgingTicket,
)
from app.services.ai_explanation import explain_insight
from app.services.cfd import compute_cfd
from app.services.cycle_scatter import compute_cycle_scatter
from app.services.insight_service import generate_insight_report
from app.services.metrics_service import (
    calendar_windows,
    compute_window_snapshot,
    cycle_time_throughput,
    default_windows,
    discover_statuses,
)
from app.services.sprint_service import sprint_windows
from app.services.wip_aging import compute_wip_aging
from app.services.wip_limits_service import get_wip_limit

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("", response_model=InsightResponse)
async def get_insights(
    days: int = Query(default=7, ge=1, le=365),
    end: datetime | None = Query(default=None),
    explain: bool = Query(default=True),
    project_key: str | None = Query(default=None),
    period: str | None = Query(default=None, regex="^(mtd|qtd)$"),
    sprint_id: int | None = Query(default=None),
    sprint_span: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> InsightResponse:
    now = end or utcnow()
    sprint_win = None
    if sprint_id is not None or sprint_span > 1:
        sprint_win = sprint_windows(
            db,
            ctx.tenant_id,
            project_key=project_key,
            sprint_id=sprint_id,
            span=sprint_span,
            now=now,
        )
    if sprint_win is not None:
        (cur_s, cur_e), (prev_s, prev_e) = sprint_win
    elif period:
        (cur_s, cur_e), (prev_s, prev_e) = calendar_windows(period, now=now)
    else:
        (cur_s, cur_e), (prev_s, prev_e) = default_windows(now=now, days=days)

    statuses = discover_statuses(db, ctx.tenant_id, project_key=project_key)
    cur_snap = compute_window_snapshot(
        db, ctx.tenant_id, cur_s, cur_e, statuses=statuses, project_key=project_key
    )
    prev_snap = compute_window_snapshot(
        db, ctx.tenant_id, prev_s, prev_e, statuses=statuses, project_key=project_key
    )
    _, cur_cycles = cycle_time_throughput(db, ctx.tenant_id, cur_s, cur_e, project_key=project_key)
    _, prev_cycles = cycle_time_throughput(
        db, ctx.tenant_id, prev_s, prev_e, project_key=project_key
    )
    cur_avg_cycle = float(sum(cur_cycles) / len(cur_cycles)) if cur_cycles else None
    prev_avg_cycle = float(sum(prev_cycles) / len(prev_cycles)) if prev_cycles else None

    report = generate_insight_report(
        cur_snap,
        prev_snap,
        ctx,
        cycle_time_current=cur_avg_cycle,
        cycle_time_previous=prev_avg_cycle,
    )

    bottleneck_out = (
        BottleneckOut(
            status=report.bottleneck.status,
            score=report.bottleneck.score,
            confidence=report.bottleneck.confidence,
            reasons=report.bottleneck.reasons,
            time_ratio=report.bottleneck.time_ratio,
            wip_ratio=report.bottleneck.wip_ratio,
            throughput_delta=report.bottleneck.throughput_delta,
            current_avg_seconds=report.bottleneck.current_avg_seconds,
            previous_avg_seconds=report.bottleneck.previous_avg_seconds,
            current_wip=report.bottleneck.current_wip,
            previous_wip=report.bottleneck.previous_wip,
            current_throughput=report.bottleneck.current_throughput,
            previous_throughput=report.bottleneck.previous_throughput,
            wip_limit=get_wip_limit(
                db, ctx.tenant_id, project_key, report.bottleneck.status
            ).max_in_progress,
        )
        if report.bottleneck
        else None
    )

    explanation = None
    if explain and report.bottleneck:
        explanation = await explain_insight(report.bottleneck, ctx.settings)

    return InsightResponse(
        window_start=report.window_start,
        window_end=report.window_end,
        previous_window_start=report.previous_window_start,
        previous_window_end=report.previous_window_end,
        bottleneck=bottleneck_out,
        candidates=[
            StatusSignalOut(
                status=c.status,
                score=c.score,
                time_ratio=c.time_ratio,
                wip_ratio=c.wip_ratio,
                throughput_delta=c.throughput_delta,
                reasons=c.reasons,
            )
            for c in report.candidates
        ],
        trends=[
            TrendOut(
                metric=t.metric,
                status=t.status,
                current_value=t.current_value,
                previous_value=t.previous_value,
                ratio=t.ratio,
                change_pct=t.change_pct,
                direction=t.direction,
            )
            for t in report.trends
        ],
        explanation=explanation,
    )


@router.get("/wip-aging", response_model=WipAgingResponse)
def get_wip_aging(
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> WipAgingResponse:
    """In-flight tickets with `days_in_status` for the WIP Aging bubble chart.
    P95 of last-90d cycle times comes back as `p95_cycle_days` so the UI
    can overlay the "this is when work starts being late" line."""
    result = compute_wip_aging(db, ctx, project_key=project_key)
    return WipAgingResponse(
        tickets=[
            WipAgingTicket(
                key=t.key,
                summary=t.summary,
                status=t.status,
                days_in_status=t.days_in_status,
                cycle_days=t.cycle_days,
                assignee=t.assignee,
                priority=t.priority,
                story_points=t.story_points,
                issue_type=t.issue_type,
            )
            for t in result.tickets
        ],
        p95_cycle_days=result.p95_cycle_days,
        sample_size=result.sample_size,
    )


@router.get("/cfd", response_model=CfdResponse)
def get_cfd(
    days: int = Query(default=30, ge=7, le=180),
    project_key: str | None = Query(default=None),
    period: str | None = Query(default=None, regex="^(mtd|qtd)$"),
    sprint_id: int | None = Query(default=None),
    sprint_span: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> CfdResponse:
    """Cumulative Flow Diagram data: per-day, per-status ticket counts.

    When `period` or `sprint_id`/`sprint_span` is set, the CFD spans those
    bounds instead of the rolling `days` window — same window semantics as
    the Overview metrics endpoints.
    """
    now = utcnow()
    start: datetime | None = None
    end: datetime | None = None
    if sprint_id is not None or sprint_span > 1:
        sw = sprint_windows(
            db,
            ctx.tenant_id,
            project_key=project_key,
            sprint_id=sprint_id,
            span=sprint_span,
            now=now,
        )
        if sw is not None:
            (start, end), _ = sw
    if start is None and period:
        (start, end), _ = calendar_windows(period, now=now)
    result = compute_cfd(db, ctx, days=days, project_key=project_key, start=start, end=end, now=now)
    return CfdResponse(
        window_start=result.window_start,
        window_end=result.window_end,
        statuses=result.statuses,
        days=[CfdDay(date=d.date, by_status=d.by_status) for d in result.days],
    )


@router.get("/cycle-scatter", response_model=CycleScatterResponse)
def get_cycle_scatter(
    days: int = Query(default=90, ge=7, le=365),
    project_key: str | None = Query(default=None),
    period: str | None = Query(default=None, regex="^(mtd|qtd)$"),
    sprint_id: int | None = Query(default=None),
    sprint_span: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> CycleScatterResponse:
    """Cycle Time Scatter chart data: dot per completed ticket + percentile
    overlays. Honors `period` / `sprint_id` like the metrics
    endpoints."""
    now = utcnow()
    start: datetime | None = None
    end: datetime | None = None
    if sprint_id is not None or sprint_span > 1:
        sw = sprint_windows(
            db,
            ctx.tenant_id,
            project_key=project_key,
            sprint_id=sprint_id,
            span=sprint_span,
            now=now,
        )
        if sw is not None:
            (start, end), _ = sw
    if start is None and period:
        (start, end), _ = calendar_windows(period, now=now)
    result = compute_cycle_scatter(
        db, ctx, days=days, project_key=project_key, start=start, end=end, now=now
    )
    return CycleScatterResponse(
        window_start=result.window_start,
        window_end=result.window_end,
        points=[
            ScatterPointOut(
                key=p.key,
                summary=p.summary,
                completed_at=p.completed_at,
                cycle_days=p.cycle_days,
                issue_type=p.issue_type,
                priority=p.priority,
                assignee=p.assignee,
            )
            for p in result.points
        ],
        p50_cycle_days=result.p50_cycle_days,
        p85_cycle_days=result.p85_cycle_days,
        p95_cycle_days=result.p95_cycle_days,
    )
