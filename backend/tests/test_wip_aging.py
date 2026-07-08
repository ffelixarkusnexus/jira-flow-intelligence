"""Tests for the WIP Aging chart data service."""

from __future__ import annotations

from datetime import datetime

from app.core.tenant_context import TenantContext
from app.db.models import Issue, Transition
from app.services.wip_aging import compute_wip_aging


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_in_flight_only(session, ctx: TenantContext) -> None:
    """`done_at IS NOT NULL` issues must not appear in the WIP aging set."""
    session.add_all(
        [
            Issue(
                tenant_id=ctx.tenant_id,
                id="A",
                key="A-1",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-05-01T00:00:00Z"),
                current_status="In Progress",
            ),
            Issue(
                tenant_id=ctx.tenant_id,
                id="B",
                key="B-1",
                created_at=_dt("2026-04-01T00:00:00Z"),
                updated_at=_dt("2026-04-15T00:00:00Z"),
                current_status="Done",
                done_at=_dt("2026-04-15T00:00:00Z"),
            ),
        ]
    )
    session.commit()
    result = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"))
    keys = {t.key for t in result.tickets}
    assert keys == {"A-1"}


def test_days_in_status_uses_latest_transition_to_current(session, ctx: TenantContext) -> None:
    """`days_in_status` = now - the most recent transitioned_at where
    to_status equals the issue's current_status."""
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
    session.add_all(
        [
            Transition(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                from_status="Todo",
                to_status="In Progress",
                transitioned_at=_dt("2026-04-05T00:00:00Z"),
            ),
            Transition(
                tenant_id=ctx.tenant_id,
                issue_id="A",
                from_status="In Progress",
                to_status="Review",
                transitioned_at=_dt("2026-04-20T00:00:00Z"),
            ),
        ]
    )
    session.commit()
    result = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"))
    assert len(result.tickets) == 1
    # 14 days from 2026-04-20 to 2026-05-04.
    assert abs(result.tickets[0].days_in_status - 14.0) < 0.01


def test_days_in_status_falls_back_to_created_at_with_no_transition(
    session, ctx: TenantContext
) -> None:
    """A brand-new issue with no recorded transitions should age from its
    creation date — the issue has been in its initial status the whole
    time."""
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="A",
        key="A-1",
        created_at=_dt("2026-05-01T00:00:00Z"),
        updated_at=_dt("2026-05-01T00:00:00Z"),
        current_status="Backlog",
    )
    session.add(issue)
    session.commit()
    result = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"))
    assert result.tickets[0].days_in_status == 3.0


def test_p95_cycle_days_overlay(session, ctx: TenantContext) -> None:
    """The chart overlays a P95 line; it comes from the last 90d of done
    issues."""
    # Five completed issues with cycle times 1, 2, 3, 4, 5 days.
    for i, days in enumerate([1, 2, 3, 4, 5]):
        session.add(
            Issue(
                tenant_id=ctx.tenant_id,
                id=f"C{i}",
                key=f"C-{i}",
                created_at=_dt("2026-04-15T00:00:00Z"),
                updated_at=_dt("2026-04-30T00:00:00Z"),
                current_status="Done",
                done_at=_dt(f"2026-04-{15 + days:02d}T00:00:00Z"),
            )
        )
    session.commit()
    result = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"))
    # P95 of [1, 2, 3, 4, 5] (linear interpolation) = 4.8.
    assert result.p95_cycle_days is not None
    assert abs(result.p95_cycle_days - 4.8) < 0.01
    assert result.sample_size == 5


def test_tickets_sorted_most_aged_first(session, ctx: TenantContext) -> None:
    session.add_all(
        [
            Issue(
                tenant_id=ctx.tenant_id,
                id="OLD",
                key="OLD-1",
                created_at=_dt("2026-03-01T00:00:00Z"),
                updated_at=_dt("2026-05-01T00:00:00Z"),
                current_status="In Progress",
            ),
            Issue(
                tenant_id=ctx.tenant_id,
                id="NEW",
                key="NEW-1",
                created_at=_dt("2026-05-02T00:00:00Z"),
                updated_at=_dt("2026-05-02T00:00:00Z"),
                current_status="In Progress",
            ),
        ]
    )
    session.commit()
    result = compute_wip_aging(session, ctx, now=_dt("2026-05-04T00:00:00Z"))
    assert [t.key for t in result.tickets] == ["OLD-1", "NEW-1"]
