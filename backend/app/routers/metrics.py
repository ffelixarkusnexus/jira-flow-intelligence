from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.session import get_db
from app.schemas.api import MetricsResponse, MetricsWindowOut, StatusMetricOut
from app.services.metrics_service import (
    _percentile,
    calendar_windows,
    compute_window_snapshot,
    cycle_time_throughput,
    default_windows,
    discover_statuses,
    recompute_all_issue_metrics,
)
from app.services.sprint_service import sprint_windows
from app.services.wip_limits_service import get_wip_limit

router = APIRouter(prefix="/metrics", tags=["metrics"])


def _window_to_out(
    snapshot,
    cycle_count: int,
    cycles: list[int],
    *,
    db: Session,
    tenant_id: str,
    project_key: str | None,
) -> MetricsWindowOut:
    avg = float(sum(cycles) / len(cycles)) if cycles else 0.0
    p50 = _percentile([float(c) for c in cycles], 0.5) if cycles else 0.0
    p90 = _percentile([float(c) for c in cycles], 0.9) if cycles else 0.0
    return MetricsWindowOut(
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        statuses=[
            StatusMetricOut(
                status=s.status,
                avg_seconds=s.avg_seconds,
                p50_seconds=s.p50_seconds,
                p90_seconds=s.p90_seconds,
                wip_avg=s.wip_avg,
                throughput=s.throughput,
                sample_size=s.sample_size,
                wip_limit=get_wip_limit(db, tenant_id, project_key, s.status).max_in_progress,
            )
            for s in snapshot.statuses.values()
        ],
        cycle_time_count=cycle_count,
        cycle_time_avg_seconds=avg,
        cycle_time_p50_seconds=p50,
        cycle_time_p90_seconds=p90,
    )


@router.get("", response_model=MetricsResponse)
def get_metrics(
    days: int = Query(default=7, ge=1, le=365),
    end: datetime | None = Query(default=None),
    project_key: str | None = Query(default=None),
    period: str | None = Query(default=None, regex="^(mtd|qtd)$"),
    sprint_id: int | None = Query(default=None),
    sprint_span: int = Query(default=1, ge=1, le=10),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> MetricsResponse:
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
    cur_count, cur_cycles = cycle_time_throughput(
        db, ctx.tenant_id, cur_s, cur_e, project_key=project_key
    )
    prev_count, prev_cycles = cycle_time_throughput(
        db, ctx.tenant_id, prev_s, prev_e, project_key=project_key
    )

    return MetricsResponse(
        current=_window_to_out(
            cur_snap, cur_count, cur_cycles, db=db, tenant_id=ctx.tenant_id, project_key=project_key
        ),
        previous=_window_to_out(
            prev_snap,
            prev_count,
            prev_cycles,
            db=db,
            tenant_id=ctx.tenant_id,
            project_key=project_key,
        ),
    )


@router.post("/recompute")
def recompute_metrics(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> dict:
    count = recompute_all_issue_metrics(db, ctx)
    db.commit()
    return {"issues_metric_rows_written": count}
