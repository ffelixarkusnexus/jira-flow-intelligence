"""Cross-tenant isolation tests — proves the multi-tenant refactor doesn't leak data.

These tests are the load-bearing guard for ADR-0011: every tenanted query must
filter by `tenant_id`, and ingesting the same Jira issue ID under two tenants
produces two distinct rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.core.config import Settings
from app.core.tenant_context import TenantContext
from app.db.models import Alert, Issue, TimeSlice, Transition
from app.services.alert_service import evaluate_alerts, upsert_rule
from app.services.ingestion_service import process_payloads
from app.services.metrics_service import (
    compute_status_window,
    discover_statuses,
    recompute_all_issue_metrics,
)
from tests.conftest import make_tenant


def _payload(key: str) -> dict:
    return {
        "id": "10001",  # SAME jira id across both tenants
        "key": key,
        "fields": {
            "created": "2026-01-01T10:00:00Z",
            "updated": "2026-01-01T14:00:00Z",
            "status": {"name": "Done"},
            "issuetype": {"name": "Story"},
            "project": {"key": "ABC"},
            "summary": "shared id",
            "resolutiondate": "2026-01-01T14:00:00Z",
        },
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T12:00:00Z",
                    "items": [
                        {"field": "status", "fromString": "In Progress", "toString": "Review"}
                    ],
                },
                {
                    "created": "2026-01-01T14:00:00Z",
                    "items": [{"field": "status", "fromString": "Review", "toString": "Done"}],
                },
            ]
        },
    }


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_same_jira_issue_id_under_two_tenants_is_two_rows(session):
    settings = Settings()
    t_a = make_tenant(session, "tenant-a")
    t_b = make_tenant(session, "tenant-b")
    ctx_a = TenantContext(tenant=t_a, settings=settings)
    ctx_b = TenantContext(tenant=t_b, settings=settings)

    process_payloads(session, [_payload("A-1")], ctx_a)
    process_payloads(session, [_payload("B-1")], ctx_b)
    session.commit()

    rows = list(session.scalars(select(Issue).where(Issue.id == "10001")))
    assert len(rows) == 2
    assert {r.tenant_id for r in rows} == {"tenant-a", "tenant-b"}
    assert {r.key for r in rows} == {"A-1", "B-1"}


def test_tenant_a_queries_never_see_tenant_b_data(session):
    settings = Settings()
    t_a = make_tenant(session, "tenant-a")
    t_b = make_tenant(session, "tenant-b")
    ctx_a = TenantContext(tenant=t_a, settings=settings)
    ctx_b = TenantContext(tenant=t_b, settings=settings)

    process_payloads(session, [_payload("A-1")], ctx_a)
    process_payloads(session, [_payload("B-1")], ctx_b)
    session.commit()

    # discover_statuses scoped to a tenant should not leak the other's statuses.
    a_statuses = discover_statuses(session, "tenant-a")
    b_statuses = discover_statuses(session, "tenant-b")
    assert a_statuses == b_statuses  # data is identical, expected
    # But the slices behind them are isolated:
    a_slices = list(session.scalars(select(TimeSlice).where(TimeSlice.tenant_id == "tenant-a")))
    b_slices = list(session.scalars(select(TimeSlice).where(TimeSlice.tenant_id == "tenant-b")))
    assert len(a_slices) > 0
    assert len(b_slices) > 0
    assert all(s.tenant_id == "tenant-a" for s in a_slices)
    assert all(s.tenant_id == "tenant-b" for s in b_slices)

    # compute_status_window for tenant-a sees only tenant-a's slices.
    win_start = _dt("2026-01-01T00:00:00Z")
    win_end = _dt("2026-01-02T00:00:00Z")
    res_a = compute_status_window(session, "tenant-a", "Review", win_start, win_end)
    res_b = compute_status_window(session, "tenant-b", "Review", win_start, win_end)
    assert res_a.sample_size == 1
    assert res_b.sample_size == 1


def test_alert_rules_and_alerts_are_tenant_scoped(session):
    settings = Settings()
    t_a = make_tenant(session, "tenant-a")
    t_b = make_tenant(session, "tenant-b")
    ctx_a = TenantContext(tenant=t_a, settings=settings)
    ctx_b = TenantContext(tenant=t_b, settings=settings)

    # Same rule_id "shared-rule" exists for both tenants but with different config.
    upsert_rule(
        session,
        ctx_a.tenant_id,
        "shared-rule",
        "status_duration",
        {"status": "Review", "threshold_seconds": 1},  # very low - will fire
    )
    upsert_rule(
        session,
        ctx_b.tenant_id,
        "shared-rule",
        "status_duration",
        {"status": "Review", "threshold_seconds": 99999999},  # very high - won't fire
    )

    # Seed an issue with a long Review slice in tenant A.
    issue = Issue(
        tenant_id="tenant-a",
        id="X",
        key="X-1",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-02T00:00:00Z"),
        current_status="Review",
    )
    session.add(issue)
    session.flush()
    session.add(
        TimeSlice(
            tenant_id="tenant-a",
            issue_id="X",
            status="Review",
            start_at=_dt("2026-01-01T00:00:00Z"),
            end_at=_dt("2026-01-02T00:00:00Z"),
            duration_seconds=86400,
            is_open=False,
        )
    )
    session.commit()

    triggered_a = evaluate_alerts(session, ctx_a, now=_dt("2026-01-02T00:00:00Z"))
    triggered_b = evaluate_alerts(session, ctx_b, now=_dt("2026-01-02T00:00:00Z"))

    assert len(triggered_a) == 1
    assert len(triggered_b) == 0
    # The persisted alert is tagged with the right tenant.
    all_alerts = list(session.scalars(select(Alert)))
    assert all(a.tenant_id == "tenant-a" for a in all_alerts)


def test_recompute_metrics_only_touches_target_tenant(session):
    settings = Settings()
    t_a = make_tenant(session, "tenant-a")
    t_b = make_tenant(session, "tenant-b")
    ctx_a = TenantContext(tenant=t_a, settings=settings)
    ctx_b = TenantContext(tenant=t_b, settings=settings)

    process_payloads(session, [_payload("A-1")], ctx_a)
    process_payloads(session, [_payload("B-1")], ctx_b)
    session.commit()

    n_a = recompute_all_issue_metrics(session, ctx_a)
    session.commit()
    assert n_a == 1  # only tenant-a's one issue


def test_dropping_a_tenant_cascades_its_data(session):
    """Deleting a tenant row removes their issues, transitions, slices, alerts —
    proves CASCADE FKs are wired correctly. Used by the `uninstalled` lifecycle."""
    settings = Settings()
    t_a = make_tenant(session, "tenant-a")
    ctx_a = TenantContext(tenant=t_a, settings=settings)

    process_payloads(session, [_payload("A-1")], ctx_a)
    session.commit()

    # Before delete: tenant has issues, transitions, slices.
    assert len(list(session.scalars(select(Issue).where(Issue.tenant_id == "tenant-a")))) == 1
    assert (
        len(list(session.scalars(select(Transition).where(Transition.tenant_id == "tenant-a")))) > 0
    )

    session.delete(t_a)
    session.commit()

    # After delete: cascaded clean.
    assert list(session.scalars(select(Issue).where(Issue.tenant_id == "tenant-a"))) == []
    assert list(session.scalars(select(Transition).where(Transition.tenant_id == "tenant-a"))) == []
    assert list(session.scalars(select(TimeSlice).where(TimeSlice.tenant_id == "tenant-a"))) == []
