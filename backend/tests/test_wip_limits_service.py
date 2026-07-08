"""Tests for the WIP limits service.

Covers ADR-0022's resolution semantics: project row beats tenant-wide,
case-folded status matching, upsert idempotency, delete behavior.
"""

from __future__ import annotations

from app.core.tenant_context import TenantContext
from app.services.wip_limits_service import (
    delete_wip_limit,
    get_wip_limit,
    list_wip_limits,
    upsert_wip_limit,
)


def test_get_returns_none_when_no_row(session, ctx: TenantContext) -> None:
    res = get_wip_limit(session, ctx.tenant_id, "PROJA", "Code Review")
    assert res.max_in_progress is None
    assert res.scope == "none"


def test_tenant_wide_row_falls_back_to_any_project(session, ctx: TenantContext) -> None:
    upsert_wip_limit(session, ctx.tenant_id, None, "Code Review", max_in_progress=3)
    session.commit()
    res = get_wip_limit(session, ctx.tenant_id, "PROJA", "Code Review")
    assert res.max_in_progress == 3
    assert res.scope == "tenant"
    res_no_project = get_wip_limit(session, ctx.tenant_id, None, "Code Review")
    assert res_no_project.max_in_progress == 3
    assert res_no_project.scope == "tenant"


def test_project_row_wins_over_tenant_wide(session, ctx: TenantContext) -> None:
    upsert_wip_limit(session, ctx.tenant_id, None, "Code Review", max_in_progress=3)
    upsert_wip_limit(session, ctx.tenant_id, "PROJA", "Code Review", max_in_progress=5)
    session.commit()
    res = get_wip_limit(session, ctx.tenant_id, "PROJA", "Code Review")
    assert res.max_in_progress == 5
    assert res.scope == "project"
    # PROJB falls through to the tenant-wide row.
    res_b = get_wip_limit(session, ctx.tenant_id, "PROJB", "Code Review")
    assert res_b.max_in_progress == 3
    assert res_b.scope == "tenant"


def test_status_match_is_case_folded(session, ctx: TenantContext) -> None:
    """A limit set on 'Code Review' applies to slices stored as 'CODE REVIEW'
    or 'code review' so it lines up with the dashboard's case-folded grouping."""
    upsert_wip_limit(session, ctx.tenant_id, None, "Code Review", max_in_progress=4)
    session.commit()
    assert get_wip_limit(session, ctx.tenant_id, None, "CODE REVIEW").max_in_progress == 4
    assert get_wip_limit(session, ctx.tenant_id, None, "code review").max_in_progress == 4
    # Different status — no match.
    assert get_wip_limit(session, ctx.tenant_id, None, "QA").max_in_progress is None


def test_upsert_is_idempotent(session, ctx: TenantContext) -> None:
    upsert_wip_limit(session, ctx.tenant_id, "PROJA", "QA", max_in_progress=2)
    upsert_wip_limit(session, ctx.tenant_id, "PROJA", "QA", max_in_progress=4)
    session.commit()
    res = get_wip_limit(session, ctx.tenant_id, "PROJA", "QA")
    assert res.max_in_progress == 4
    # And only one row exists.
    rows = list_wip_limits(session, ctx.tenant_id, project_key="PROJA")
    qa_rows = [r for r in rows if r.status == "QA"]
    assert len(qa_rows) == 1


def test_breach_minutes_round_trips(session, ctx: TenantContext) -> None:
    upsert_wip_limit(
        session, ctx.tenant_id, "PROJA", "Code Review", max_in_progress=3, breach_minutes=120
    )
    session.commit()
    res = get_wip_limit(session, ctx.tenant_id, "PROJA", "Code Review")
    assert res.max_in_progress == 3
    assert res.breach_minutes == 120


def test_negative_values_rejected(session, ctx: TenantContext) -> None:
    import pytest

    with pytest.raises(ValueError):
        upsert_wip_limit(session, ctx.tenant_id, None, "QA", max_in_progress=-1)
    with pytest.raises(ValueError):
        upsert_wip_limit(session, ctx.tenant_id, None, "QA", max_in_progress=3, breach_minutes=-5)


def test_delete_returns_true_when_present(session, ctx: TenantContext) -> None:
    upsert_wip_limit(session, ctx.tenant_id, "PROJA", "QA", max_in_progress=3)
    session.commit()
    deleted = delete_wip_limit(session, ctx.tenant_id, "PROJA", "QA")
    session.commit()
    assert deleted is True
    assert get_wip_limit(session, ctx.tenant_id, "PROJA", "QA").max_in_progress is None


def test_delete_returns_false_when_absent(session, ctx: TenantContext) -> None:
    deleted = delete_wip_limit(session, ctx.tenant_id, "PROJA", "QA")
    assert deleted is False


def test_list_with_project_returns_project_plus_tenant_wide(session, ctx: TenantContext) -> None:
    upsert_wip_limit(session, ctx.tenant_id, None, "Code Review", max_in_progress=3)
    upsert_wip_limit(session, ctx.tenant_id, "PROJA", "QA", max_in_progress=2)
    upsert_wip_limit(session, ctx.tenant_id, "PROJB", "QA", max_in_progress=4)
    session.commit()
    rows = list_wip_limits(session, ctx.tenant_id, project_key="PROJA")
    statuses = {(r.project_key, r.status) for r in rows}
    # Tenant-wide Code Review + PROJA QA. PROJB QA hidden.
    assert (None, "Code Review") in statuses
    assert ("PROJA", "QA") in statuses
    assert ("PROJB", "QA") not in statuses
