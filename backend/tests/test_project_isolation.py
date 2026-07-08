"""project_key filter isolation.

Two issues live under the same tenant but in different Jira projects
(`PROJ-A` and `PROJ-B`). Every metric service must, when handed a
`project_key`, return data from only that project's issues — never
leak the other project's slices, transitions, or completions.

This is the load-bearing guard for the `jira:projectPage` contract:
when a user opens the plugin from PROJ-A's nav, they should not see
PROJ-B's bottlenecks or P95 line.
"""

from __future__ import annotations

from datetime import datetime

from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice
from app.services.cfd import compute_cfd
from app.services.cycle_scatter import compute_cycle_scatter
from app.services.metrics_service import (
    compute_status_window,
    compute_window_snapshot,
    cycle_time_throughput,
    discover_status_groups,
    discover_statuses,
)
from app.services.wip_aging import compute_wip_aging


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _seed_two_projects(session, tenant_id: str) -> None:
    """One in-flight + one done issue under PROJ-A, same shape under PROJ-B
    but with distinct status names so we can tell them apart."""
    # PROJ-A: in-flight in "Review", completed in "Done"
    session.add_all(
        [
            Issue(
                tenant_id=tenant_id,
                id="A1",
                key="PROJA-1",
                project_key="PROJA",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-05-01T00:00:00Z"),
                current_status="Review",
            ),
            Issue(
                tenant_id=tenant_id,
                id="A2",
                key="PROJA-2",
                project_key="PROJA",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-04-10T00:00:00Z"),
                current_status="Done",
                done_at=_dt("2026-04-10T00:00:00Z"),
            ),
            Issue(
                tenant_id=tenant_id,
                id="B1",
                key="PROJB-1",
                project_key="PROJB",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-05-01T00:00:00Z"),
                current_status="QA",  # PROJB-only status
            ),
            Issue(
                tenant_id=tenant_id,
                id="B2",
                key="PROJB-2",
                project_key="PROJB",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-04-20T00:00:00Z"),
                current_status="Shipped",  # PROJB-only status
                done_at=_dt("2026-04-20T00:00:00Z"),
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            TimeSlice(
                tenant_id=tenant_id,
                issue_id="A1",
                status="Review",
                start_at=_dt("2026-04-25T00:00:00Z"),
                end_at=_dt("2026-05-01T00:00:00Z"),
                duration_seconds=6 * 86400,
                is_open=True,
            ),
            TimeSlice(
                tenant_id=tenant_id,
                issue_id="A2",
                status="Done",
                start_at=_dt("2026-04-10T00:00:00Z"),
                end_at=_dt("2026-04-11T00:00:00Z"),
                duration_seconds=86400,
                is_open=False,
            ),
            TimeSlice(
                tenant_id=tenant_id,
                issue_id="B1",
                status="QA",
                start_at=_dt("2026-04-25T00:00:00Z"),
                end_at=_dt("2026-05-01T00:00:00Z"),
                duration_seconds=6 * 86400,
                is_open=True,
            ),
            TimeSlice(
                tenant_id=tenant_id,
                issue_id="B2",
                status="Shipped",
                start_at=_dt("2026-04-20T00:00:00Z"),
                end_at=_dt("2026-04-21T00:00:00Z"),
                duration_seconds=86400,
                is_open=False,
            ),
        ]
    )
    session.commit()


def test_discover_statuses_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    a = discover_statuses(session, ctx.tenant_id, project_key="PROJA")
    b = discover_statuses(session, ctx.tenant_id, project_key="PROJB")
    assert set(a) == {"Review", "Done"}
    assert set(b) == {"QA", "Shipped"}
    # Tenant-wide call still sees everything.
    all_t = discover_statuses(session, ctx.tenant_id)
    assert set(all_t) == {"Review", "Done", "QA", "Shipped"}


def test_discover_status_groups_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    a_groups = discover_status_groups(session, ctx.tenant_id, project_key="PROJA")
    a_displays = {g[0] for g in a_groups}
    assert a_displays == {"Review", "Done"}


def test_compute_status_window_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    win_s, win_e = _dt("2026-04-01T00:00:00Z"), _dt("2026-05-04T00:00:00Z")
    # PROJA Review window only counts PROJA's open Review slice — never PROJB's.
    res_a = compute_status_window(
        session, ctx.tenant_id, "Review", win_s, win_e, project_key="PROJA"
    )
    assert res_a.wip_avg > 0
    res_b = compute_status_window(
        session, ctx.tenant_id, "Review", win_s, win_e, project_key="PROJB"
    )
    assert res_b.wip_avg == 0
    assert res_b.sample_size == 0


def test_window_snapshot_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    win_s, win_e = _dt("2026-04-01T00:00:00Z"), _dt("2026-05-04T00:00:00Z")
    snap_a = compute_window_snapshot(session, ctx.tenant_id, win_s, win_e, project_key="PROJA")
    assert set(snap_a.statuses.keys()) == {"Review", "Done"}
    snap_b = compute_window_snapshot(session, ctx.tenant_id, win_s, win_e, project_key="PROJB")
    assert set(snap_b.statuses.keys()) == {"QA", "Shipped"}


def test_cycle_time_throughput_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    win_s, win_e = _dt("2026-04-01T00:00:00Z"), _dt("2026-05-04T00:00:00Z")
    count_a, _ = cycle_time_throughput(session, ctx.tenant_id, win_s, win_e, project_key="PROJA")
    count_b, _ = cycle_time_throughput(session, ctx.tenant_id, win_s, win_e, project_key="PROJB")
    assert count_a == 1
    assert count_b == 1
    count_all, _ = cycle_time_throughput(session, ctx.tenant_id, win_s, win_e)
    assert count_all == 2


def test_wip_aging_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    res_a = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"), project_key="PROJA")
    assert {t.key for t in res_a.tickets} == {"PROJA-1"}
    res_b = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"), project_key="PROJB")
    assert {t.key for t in res_b.tickets} == {"PROJB-1"}


def test_cfd_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    res_a = compute_cfd(session, ctx, days=14, now=_dt("2026-05-04T00:00:00Z"), project_key="PROJA")
    # PROJA only carries Review/Done — PROJB's QA and Shipped never bleed in.
    assert set(res_a.statuses).issubset({"Review", "Done"})
    assert "QA" not in res_a.statuses
    assert "Shipped" not in res_a.statuses


def test_cycle_scatter_scoped_to_project(session, ctx: TenantContext) -> None:
    _seed_two_projects(session, ctx.tenant_id)
    res_a = compute_cycle_scatter(
        session, ctx, days=60, now=_dt("2026-05-04T00:00:00Z"), project_key="PROJA"
    )
    assert {p.key for p in res_a.points} == {"PROJA-2"}
    res_b = compute_cycle_scatter(
        session, ctx, days=60, now=_dt("2026-05-04T00:00:00Z"), project_key="PROJB"
    )
    assert {p.key for p in res_b.points} == {"PROJB-2"}
