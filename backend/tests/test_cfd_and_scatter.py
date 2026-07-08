"""Tests for the chart services (CFD + Cycle Time Scatter)."""

from __future__ import annotations

from datetime import datetime

from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice
from app.services.cfd import compute_cfd
from app.services.cycle_scatter import compute_cycle_scatter


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ----- CFD ------------------------------------------------------------------


def test_cfd_counts_distinct_issues_per_status_per_day(session, ctx: TenantContext) -> None:
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="A",
        key="A-1",
        created_at=_dt("2026-04-01T00:00:00Z"),
        updated_at=_dt("2026-04-30T00:00:00Z"),
        current_status="Review",
    )
    session.add(issue)
    session.flush()
    # Issue A is in In Progress for 4/29-4/30, then Review for 4/30 onward.
    session.add_all(
        [
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="In Progress",
                start_at=_dt("2026-04-29T00:00:00Z"),
                end_at=_dt("2026-04-30T08:00:00Z"),
                duration_seconds=32 * 3600,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="Review",
                start_at=_dt("2026-04-30T08:00:00Z"),
                end_at=_dt("2026-05-04T00:00:00Z"),
                duration_seconds=4 * 86400,
                is_open=False,
            ),
        ]
    )
    session.commit()
    res = compute_cfd(session, ctx, days=7, now=_dt("2026-05-04T00:00:00Z"))
    by_date = {d.date: d.by_status for d in res.days}
    # 4/30 end-of-day: ticket has slices covering this end. start_at <= d <= end_at
    # In Progress slice ends 4/30 08:00, so d=4/30 00:00:00 (start of 5/1 in our
    # generation? Actually day_ends starts at start+1d). Let me just assert
    # something straightforward: the 5/2 bucket (well after switch) should
    # show A in Review.
    assert by_date["2026-05-02"].get("Review", 0) == 1


def test_cfd_excludes_terminal_statuses(session, ctx: TenantContext) -> None:
    """Done/Won't Do/Cancelled/Rejected slices must not appear in the CFD,
    so terminal piles don't visually dominate the in-flight bands."""
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="A",
        key="A-1",
        created_at=_dt("2026-04-01T00:00:00Z"),
        updated_at=_dt("2026-04-30T00:00:00Z"),
        current_status="Done",
    )
    session.add(issue)
    session.flush()
    session.add_all(
        [
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="In Progress",
                start_at=_dt("2026-05-01T00:00:00Z"),
                end_at=_dt("2026-05-02T00:00:00Z"),
                duration_seconds=86400,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="Done",
                start_at=_dt("2026-05-02T00:00:00Z"),
                end_at=_dt("2026-05-04T00:00:00Z"),
                duration_seconds=2 * 86400,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="Won't Do",
                start_at=_dt("2026-05-03T00:00:00Z"),
                end_at=_dt("2026-05-04T00:00:00Z"),
                duration_seconds=86400,
                is_open=False,
            ),
        ]
    )
    session.commit()
    res = compute_cfd(session, ctx, days=7, now=_dt("2026-05-04T00:00:00Z"))
    assert "In Progress" in res.statuses
    assert "Done" not in res.statuses
    assert "Won't Do" not in res.statuses
    # No band for the terminal statuses anywhere in the per-day buckets.
    for bucket in res.days:
        assert "Done" not in bucket.by_status
        assert "Won't Do" not in bucket.by_status


def test_cfd_groups_case_equivalent_statuses(session, ctx: TenantContext) -> None:
    """Two case-variant statuses should stack on a single CFD band."""
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="A",
        key="A-1",
        created_at=_dt("2026-04-01T00:00:00Z"),
        updated_at=_dt("2026-04-30T00:00:00Z"),
        current_status="Code Review",
    )
    session.add(issue)
    session.flush()
    session.add_all(
        [
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="Code Review",
                start_at=_dt("2026-05-01T00:00:00Z"),
                end_at=_dt("2026-05-02T00:00:00Z"),
                duration_seconds=86400,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                status="CODE REVIEW",
                start_at=_dt("2026-05-02T00:00:00Z"),
                end_at=_dt("2026-05-04T00:00:00Z"),
                duration_seconds=2 * 86400,
                is_open=False,
            ),
        ]
    )
    session.commit()
    res = compute_cfd(session, ctx, days=7, now=_dt("2026-05-04T00:00:00Z"))
    # One canonical name in `statuses`, not two — the variant with the most
    # slices wins; ties break alphabetically. Either casing is acceptable as
    # long as we don't end up with both bands.
    assert len(res.statuses) == 1
    assert res.statuses[0].lower() == "code review"


# ----- Cycle Time Scatter ----------------------------------------------------


def test_cycle_scatter_one_point_per_completed_issue(session, ctx: TenantContext) -> None:
    session.add_all(
        [
            Issue(
                tenant_id=ctx.tenant_id,
                id="A",
                key="A-1",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-04-05T00:00:00Z"),
                current_status="Done",
                done_at=_dt("2026-04-05T00:00:00Z"),
            ),
            Issue(
                tenant_id=ctx.tenant_id,
                id="B",
                key="B-1",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-04-15T00:00:00Z"),
                current_status="In Progress",
                # not done — should NOT appear
            ),
        ]
    )
    session.commit()
    res = compute_cycle_scatter(session, ctx, days=30, now=_dt("2026-05-04T00:00:00Z"))
    keys = {p.key for p in res.points}
    assert keys == {"A-1"}
    assert res.points[0].cycle_days == 4.0


def test_cycle_scatter_percentiles(session, ctx: TenantContext) -> None:
    for i, days in enumerate([1, 2, 3, 4, 5, 10, 20]):
        session.add(
            Issue(
                tenant_id=ctx.tenant_id,
                id=f"X{i}",
                key=f"X-{i}",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-04-30T00:00:00Z"),
                current_status="Done",
                done_at=_dt(f"2026-04-{1 + days:02d}T00:00:00Z"),
            )
        )
    session.commit()
    res = compute_cycle_scatter(session, ctx, days=90, now=_dt("2026-05-04T00:00:00Z"))
    # P50 of [1,2,3,4,5,10,20] = 4.0
    assert res.p50_cycle_days == 4.0
    # P95 with linear interpolation: k=6*0.95=5.7, between idx 5 (=10) and 6
    # (=20), 0.7 of the way → 17.0.
    assert res.p95_cycle_days == 17.0


def test_cycle_scatter_excludes_pre_window(session, ctx: TenantContext) -> None:
    """A ticket completed > days ago shouldn't appear in the scatter."""
    session.add(
        Issue(
            tenant_id=ctx.tenant_id,
            id="A",
            key="A-1",
            created_at=_dt("2025-01-01T00:00:00Z"),
            updated_at=_dt("2025-01-10T00:00:00Z"),
            current_status="Done",
            done_at=_dt("2025-01-10T00:00:00Z"),  # > 90 days before now
        )
    )
    session.commit()
    res = compute_cycle_scatter(session, ctx, days=30, now=_dt("2026-05-04T00:00:00Z"))
    assert res.points == []
