"""Coverage for the metrics persistence helpers and discovery functions."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Issue, IssueMetric, StatusWindowMetric, TimeSlice
from app.services.metrics_service import (
    StatusWindowResult,
    compute_window_snapshot,
    cycle_time_throughput,
    default_windows,
    discover_statuses,
    persist_status_window,
    recompute_all_issue_metrics,
)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_default_windows_split_evenly():
    now = _dt("2026-01-15T00:00:00Z")
    (cur_s, cur_e), (prev_s, prev_e) = default_windows(now=now, days=7)
    assert cur_e == now
    assert (cur_e - cur_s) == timedelta(days=7)
    assert prev_e == cur_s
    assert (prev_e - prev_s) == timedelta(days=7)


def test_default_windows_uses_utcnow_when_none():
    (_cur_s, cur_e), _ = default_windows(days=1)
    assert (utcnow() - cur_e).total_seconds() < 5


def test_discover_statuses_returns_sorted_unique(session, ctx: TenantContext):
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="X",
        key="X-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-02T00:00:00Z"),
        current_status="Done",
    )
    session.add(issue)
    session.flush()
    session.add_all(
        [
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="X",
                status="Review",
                start_at=_dt("2026-01-01T00:00:00Z"),
                end_at=_dt("2026-01-01T01:00:00Z"),
                duration_seconds=3600,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="X",
                status="Done",
                start_at=_dt("2026-01-01T01:00:00Z"),
                end_at=_dt("2026-01-02T00:00:00Z"),
                duration_seconds=23 * 3600,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="X",
                status="Review",  # dup
                start_at=_dt("2026-01-02T00:00:00Z"),
                end_at=_dt("2026-01-02T01:00:00Z"),
                duration_seconds=3600,
                is_open=False,
            ),
        ]
    )
    session.commit()
    statuses = discover_statuses(session, ctx.tenant_id)
    assert statuses == ["Done", "Review"]


def test_window_snapshot_groups_case_equivalent_status_variants(session, ctx: TenantContext):
    """A real Jira workflow that has both 'Code Review' and 'CODE REVIEW'
    must render as ONE row in the dashboard, not two split-signal rows.
    Display name = the variant with the most slices (alphabetical tiebreak)."""
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="X",
        key="X-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-08T00:00:00Z"),
        current_status="Done",
    )
    session.add(issue)
    session.flush()
    session.add_all(
        [
            # Two slices in "Code Review" (more populous, should win display)
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="X",
                status="Code Review",
                start_at=_dt("2026-01-02T00:00:00Z"),
                end_at=_dt("2026-01-02T04:00:00Z"),
                duration_seconds=4 * 3600,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="X",
                status="Code Review",
                start_at=_dt("2026-01-03T00:00:00Z"),
                end_at=_dt("2026-01-03T05:00:00Z"),
                duration_seconds=5 * 3600,
                is_open=False,
            ),
            # One in the upper-case variant
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="X",
                status="CODE REVIEW",
                start_at=_dt("2026-01-04T00:00:00Z"),
                end_at=_dt("2026-01-04T03:00:00Z"),
                duration_seconds=3 * 3600,
                is_open=False,
            ),
        ]
    )
    session.commit()
    snap = compute_window_snapshot(
        session, ctx.tenant_id, _dt("2026-01-01T00:00:00Z"), _dt("2026-01-08T00:00:00Z")
    )
    # One consolidated row, not two.
    assert list(snap.statuses.keys()) == ["Code Review"]
    row = snap.statuses["Code Review"]
    assert row.sample_size == 3  # all three slices counted
    # Avg time = (4 + 5 + 3) hours / 3 = 4 hours.
    assert abs(row.avg_seconds - 4 * 3600) < 1


def test_compute_window_snapshot_auto_discovers_when_none(session, ctx: TenantContext):
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="X",
        key="X-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-08T00:00:00Z"),
        current_status="Done",
        done_at=_dt("2026-01-04T00:00:00Z"),
    )
    session.add(issue)
    session.flush()
    session.add(
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="X",
            status="Review",
            start_at=_dt("2026-01-02T00:00:00Z"),
            end_at=_dt("2026-01-04T00:00:00Z"),
            duration_seconds=2 * 86400,
            is_open=False,
        )
    )
    session.commit()
    snap = compute_window_snapshot(
        session, ctx.tenant_id, _dt("2026-01-01T00:00:00Z"), _dt("2026-01-08T00:00:00Z")
    )
    assert "Review" in snap.statuses


def test_cycle_time_throughput_window(session, ctx: TenantContext):
    session.add_all(
        [
            Issue(
                tenant_id=ctx.tenant_id,
                id="A",
                key="A-1",
                created_at=_dt("2026-01-01T00:00:00Z"),
                updated_at=_dt("2026-01-02T00:00:00Z"),
                current_status="Done",
                done_at=_dt("2026-01-02T00:00:00Z"),
            ),
            Issue(
                tenant_id=ctx.tenant_id,
                id="B",
                key="B-1",
                created_at=_dt("2026-01-01T00:00:00Z"),
                updated_at=_dt("2026-01-05T00:00:00Z"),
                current_status="In Progress",
            ),
        ]
    )
    session.commit()
    count, cycles = cycle_time_throughput(
        session, ctx.tenant_id, _dt("2026-01-01T00:00:00Z"), _dt("2026-01-03T00:00:00Z")
    )
    assert count == 1
    assert cycles == [86400]


def test_persist_status_window_inserts_and_updates(session, ctx: TenantContext):
    res = StatusWindowResult(
        status="Review",
        window_start=_dt("2026-01-01T00:00:00Z"),
        window_end=_dt("2026-01-08T00:00:00Z"),
        avg_seconds=1000.0,
        p50_seconds=900.0,
        p90_seconds=2000.0,
        wip_avg=1.5,
        throughput=3,
        sample_size=4,
    )
    persist_status_window(session, ctx.tenant_id, [res])
    session.commit()
    rows = list(session.query(StatusWindowMetric).all())
    assert len(rows) == 1
    assert rows[0].avg_seconds == 1000.0

    res.avg_seconds = 1500.0
    persist_status_window(session, ctx.tenant_id, [res])
    session.commit()
    rows = list(session.query(StatusWindowMetric).all())
    assert len(rows) == 1
    assert rows[0].avg_seconds == 1500.0


def test_recompute_all_issue_metrics_writes_rows(session, ctx: TenantContext):
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="X",
        key="X-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-02T00:00:00Z"),
        current_status="Done",
        done_at=_dt("2026-01-02T00:00:00Z"),
    )
    session.add(issue)
    session.flush()
    session.add(
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="X",
            status="In Progress",
            start_at=_dt("2026-01-01T00:00:00Z"),
            end_at=_dt("2026-01-02T00:00:00Z"),
            duration_seconds=86400,
            is_open=False,
        )
    )
    session.commit()
    n = recompute_all_issue_metrics(session, ctx)
    session.commit()

    # Re-running the per-id variant against a single issue shouldn't touch
    # the rest. Scales with delta size, not tenant size.
    from app.services.metrics_service import recompute_issue_metrics_for as _rifor

    n_one = _rifor(session, ctx, ["X"])
    session.commit()
    assert n == 1
    assert n_one == 1
    # Empty list = no work.
    assert _rifor(session, ctx, []) == 0
    rows = list(session.query(IssueMetric).all())
    assert rows[0].cycle_seconds == 86400
    assert rows[0].active_seconds == 86400
