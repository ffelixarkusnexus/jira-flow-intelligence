from datetime import datetime

from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice
from app.services.metrics_service import (
    compute_issue_metrics,
    compute_status_window,
)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_cycle_time_active_wait_breakdown(ctx: TenantContext):
    # active_statuses defaults to ["In Progress", "Review"]
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="X",
        key="X-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-04T00:00:00Z"),
        done_at=_dt("2026-01-04T00:00:00Z"),
        current_status="Done",
    )
    slices = [
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="X",
            status="Todo",
            start_at=_dt("2026-01-01T00:00:00Z"),
            end_at=_dt("2026-01-02T00:00:00Z"),
            duration_seconds=86400,
            is_open=False,
        ),
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="X",
            status="In Progress",
            start_at=_dt("2026-01-02T00:00:00Z"),
            end_at=_dt("2026-01-03T00:00:00Z"),
            duration_seconds=86400,
            is_open=False,
        ),
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="X",
            status="Review",
            start_at=_dt("2026-01-03T00:00:00Z"),
            end_at=_dt("2026-01-04T00:00:00Z"),
            duration_seconds=86400,
            is_open=False,
        ),
    ]
    result = compute_issue_metrics(issue, slices, ctx)
    assert result.cycle_seconds == 3 * 86400
    assert result.active_seconds == 2 * 86400  # In Progress + Review
    assert result.wait_seconds == 86400  # Todo
    assert result.is_done is True


def test_cycle_time_open_issue_uses_now(ctx: TenantContext):
    now = _dt("2026-01-05T00:00:00Z")
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="X",
        key="X-2",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=now,
        current_status="In Progress",
    )
    slices = [
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="X",
            status="In Progress",
            start_at=_dt("2026-01-01T00:00:00Z"),
            end_at=now,
            duration_seconds=4 * 86400,
            is_open=True,
        ),
    ]
    r = compute_issue_metrics(issue, slices, ctx, now=now)
    assert r.cycle_seconds == 4 * 86400
    assert r.is_done is False


def test_status_window_avg_and_throughput(session, ctx: TenantContext):
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="A",
        key="A-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-08T00:00:00Z"),
        current_status="Done",
        done_at=_dt("2026-01-04T00:00:00Z"),
    )
    issue2 = Issue(
        tenant_id=ctx.tenant_id,
        id="B",
        key="B-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-08T00:00:00Z"),
        current_status="Done",
        done_at=_dt("2026-01-05T00:00:00Z"),
    )
    session.add_all([issue, issue2])
    session.flush()
    slices = [
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="A",
            status="Review",
            start_at=_dt("2026-01-02T00:00:00Z"),
            end_at=_dt("2026-01-04T00:00:00Z"),
            duration_seconds=2 * 86400,
            is_open=False,
        ),
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="B",
            status="Review",
            start_at=_dt("2026-01-03T00:00:00Z"),
            end_at=_dt("2026-01-05T00:00:00Z"),
            duration_seconds=2 * 86400,
            is_open=False,
        ),
    ]
    session.add_all(slices)
    session.commit()

    win_start = _dt("2026-01-01T00:00:00Z")
    win_end = _dt("2026-01-08T00:00:00Z")
    res = compute_status_window(session, ctx.tenant_id, "Review", win_start, win_end)
    assert res.sample_size == 2
    assert res.avg_seconds == 2 * 86400
    assert res.throughput == 2  # both completed Review during the window
    assert res.wip_avg > 0
