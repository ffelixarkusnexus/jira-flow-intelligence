"""ADR-0046 — Path B retroactive backfill.

Two layers of coverage:

1. Unit on `backfill_legacy_status_ids` — full backfill, idempotency,
   partial backfill with unresolved names, empty lookup.
2. Integration on `POST /api/forge/backfill/status-ids` — auth required
   (the router lives under the JWT-middleware-protected api/forge prefix
   in production; here we override the tenant-context dependency), result
   JSON shape, mocked Jira success path.
3. End-to-end coverage of the Path-A + Path-B completeness contract:
   discover_status_groups, after backfill, collapses the renamed
   historical data under the current name (no separate orphan group
   under the pre-rename name).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Issue, Tenant, TimeSlice, Transition
from app.db.session import get_db
from app.main import create_app
from app.services.metrics_service import discover_status_groups
from app.services.status_id_backfill import backfill_legacy_status_ids
from tests.conftest import make_tenant

FIXED_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


# --- Unit: backfill_legacy_status_ids ---------------------------------------


def _make_issue(session: Session, tenant_id: str, issue_id: str) -> Issue:
    issue = Issue(
        tenant_id=tenant_id,
        id=issue_id,
        key=f"K-{issue_id}",
        project_key="DEMO",
        current_status="Done",
        created_at=FIXED_NOW - timedelta(days=100),
        updated_at=FIXED_NOW,
    )
    session.add(issue)
    session.flush()
    return issue


def _legacy_transition(
    session: Session,
    tenant_id: str,
    issue_id: str,
    from_name: str,
    to_name: str,
    days_ago: int,
) -> Transition:
    t = Transition(
        tenant_id=tenant_id,
        issue_id=issue_id,
        from_status=from_name,
        to_status=to_name,
        from_status_id=None,
        to_status_id=None,
        transitioned_at=FIXED_NOW - timedelta(days=days_ago),
    )
    session.add(t)
    session.flush()
    return t


def _legacy_slice(
    session: Session,
    tenant_id: str,
    issue_id: str,
    status: str,
    days_ago_start: int,
    days_ago_end: int,
) -> TimeSlice:
    s = TimeSlice(
        tenant_id=tenant_id,
        issue_id=issue_id,
        status=status,
        status_id=None,
        start_at=FIXED_NOW - timedelta(days=days_ago_start),
        end_at=FIXED_NOW - timedelta(days=days_ago_end),
        duration_seconds=int(timedelta(days=days_ago_start - days_ago_end).total_seconds()),
        is_open=False,
    )
    session.add(s)
    session.flush()
    return s


def test_backfill_populates_all_null_rows(session: Session) -> None:
    """Headline test: every legacy NULL-id row gets `status_id` set when
    its `name` is in the lookup."""
    tenant = make_tenant(session, client_key="backfill-test")
    issue = _make_issue(session, tenant.client_key, "10001")

    _legacy_transition(session, tenant.client_key, issue.id, "To Do", "In Review", 80)
    _legacy_transition(session, tenant.client_key, issue.id, "In Review", "Done", 75)
    _legacy_slice(session, tenant.client_key, issue.id, "In Review", 80, 75)
    _legacy_slice(session, tenant.client_key, issue.id, "Done", 75, 74)

    result = backfill_legacy_status_ids(
        session,
        tenant.client_key,
        {"To Do": "10000", "In Review": "10042", "Done": "20000"},
    )

    # 2 transitions x 2 columns each (from+to) = up to 4 column-updates;
    # the actual rowcount sums all UPDATE statements that matched.
    assert result.updated_transitions >= 3, (
        f"expected at least 3 transition column-updates; got {result.updated_transitions}"
    )
    assert result.updated_slices == 2
    assert result.unresolved_names == []

    # Every persisted row now has status_id set.
    transitions = session.query(Transition).filter(Transition.tenant_id == tenant.client_key).all()
    for t in transitions:
        if t.to_status:
            assert t.to_status_id is not None, f"to row {t.id} still NULL"
        if t.from_status:
            assert t.from_status_id is not None, f"from row {t.id} still NULL"
    slices = session.query(TimeSlice).filter(TimeSlice.tenant_id == tenant.client_key).all()
    for s in slices:
        assert s.status_id is not None, f"slice {s.id} still NULL"


def test_backfill_is_idempotent(session: Session) -> None:
    """Second invocation with the same lookup is a no-op — the WHERE
    clauses scope to NULL-id rows only, which are gone after pass 1."""
    tenant = make_tenant(session, client_key="backfill-test")
    issue = _make_issue(session, tenant.client_key, "10002")
    _legacy_transition(session, tenant.client_key, issue.id, "To Do", "In Review", 50)
    _legacy_slice(session, tenant.client_key, issue.id, "In Review", 50, 45)

    lookup = {"To Do": "10000", "In Review": "10042"}
    first = backfill_legacy_status_ids(session, tenant.client_key, lookup)
    second = backfill_legacy_status_ids(session, tenant.client_key, lookup)

    assert first.updated_transitions > 0
    assert first.updated_slices > 0
    assert second.updated_transitions == 0
    assert second.updated_slices == 0
    assert second.unresolved_names == []


def test_backfill_reports_unresolved_names(session: Session) -> None:
    """Names present in NULL rows but absent from the Jira lookup —
    typically a status renamed AND then deleted — show up in
    `unresolved_names` for tenant-admin attention."""
    tenant = make_tenant(session, client_key="backfill-test")
    issue = _make_issue(session, tenant.client_key, "10003")

    _legacy_transition(session, tenant.client_key, issue.id, "To Do", "Deprecated Status", 60)
    _legacy_slice(session, tenant.client_key, issue.id, "Deprecated Status", 60, 55)

    # "Deprecated Status" is missing from the lookup.
    result = backfill_legacy_status_ids(
        session,
        tenant.client_key,
        {"To Do": "10000", "In Review": "10042"},
    )

    assert "Deprecated Status" in result.unresolved_names
    # The "To Do" row that DID resolve still gets updated.
    assert result.updated_transitions >= 1


def test_backfill_partial_resolution_progresses(session: Session) -> None:
    """Mixed resolved + unresolved in the same run: resolved rows are
    updated; unresolved are reported. A subsequent run with the deleted
    status restored picks up the remaining rows."""
    tenant = make_tenant(session, client_key="backfill-test")
    issue = _make_issue(session, tenant.client_key, "10004")
    _legacy_slice(session, tenant.client_key, issue.id, "To Do", 70, 68)
    _legacy_slice(session, tenant.client_key, issue.id, "Renamed Away", 68, 65)

    first = backfill_legacy_status_ids(session, tenant.client_key, {"To Do": "10000"})
    assert first.updated_slices == 1
    assert "Renamed Away" in first.unresolved_names

    second = backfill_legacy_status_ids(
        session,
        tenant.client_key,
        {"To Do": "10000", "Renamed Away": "10099"},
    )
    assert second.updated_slices == 1
    assert second.unresolved_names == []


def test_backfill_empty_lookup_only_surfaces_unresolved(session: Session) -> None:
    """Empty Jira lookup: every legacy name is unresolved, nothing
    gets updated. Defensive — protects against a Jira API failure
    returning an empty array."""
    tenant = make_tenant(session, client_key="backfill-test")
    issue = _make_issue(session, tenant.client_key, "10005")
    _legacy_slice(session, tenant.client_key, issue.id, "In Review", 80, 75)
    _legacy_transition(session, tenant.client_key, issue.id, "To Do", "In Review", 80)

    result = backfill_legacy_status_ids(session, tenant.client_key, {})
    assert result.updated_transitions == 0
    assert result.updated_slices == 0
    assert set(result.unresolved_names) == {"To Do", "In Review"}


def test_backfill_scoped_to_tenant(session: Session) -> None:
    """Tenant isolation: a tenant's backfill must not touch another
    tenant's rows. Pinned because the WHERE clause includes tenant_id."""
    tenant_a = make_tenant(session, client_key="tenant-a")
    tenant_b = make_tenant(session, client_key="tenant-b")
    issue_a = _make_issue(session, tenant_a.client_key, "20001")
    issue_b = _make_issue(session, tenant_b.client_key, "20002")
    _legacy_slice(session, tenant_a.client_key, issue_a.id, "In Review", 80, 75)
    _legacy_slice(session, tenant_b.client_key, issue_b.id, "In Review", 80, 75)

    backfill_legacy_status_ids(session, tenant_a.client_key, {"In Review": "10042"})

    slices_a = session.query(TimeSlice).filter(TimeSlice.tenant_id == tenant_a.client_key).all()
    slices_b = session.query(TimeSlice).filter(TimeSlice.tenant_id == tenant_b.client_key).all()
    assert all(s.status_id == "10042" for s in slices_a)
    assert all(s.status_id is None for s in slices_b)


# --- End-to-end: Path A + B completeness ------------------------------------


def test_e2e_legacy_renamed_history_merges_after_backfill(session: Session) -> None:
    """The Path A + Path B completeness contract.

    Setup: a tenant has BOTH legacy NULL-id rows (pre-fix state) under
    "In Review" AND post-fix rows (id "10042") under "Code Review". Per
    `test_status_rename_aggregation.test_discover_status_groups_legacy_
    orphaned_from_renamed_id_group`, this state produces TWO groups —
    the orphaned legacy "In Review" group and the new ID-keyed
    "Code Review" group.

    Path B closes that gap: after backfill (with "In Review" mapping to
    the same id "10042" the post-fix rows use), the legacy rows acquire
    `status_id=10042` and collapse into the ID-keyed group. The chart
    shows ONE row under the current name.
    """
    tenant = make_tenant(session, client_key="backfill-test")
    issue = _make_issue(session, tenant.client_key, "10006")
    # Legacy era — pre-fix (NULL status_id under the old name).
    _legacy_slice(session, tenant.client_key, issue.id, "In Review", 80, 75)
    # Post-fix era — status_id populated under the current name.
    session.add(
        TimeSlice(
            tenant_id=tenant.client_key,
            issue_id=issue.id,
            status="Code Review",
            status_id="10042",
            start_at=FIXED_NOW - timedelta(days=10),
            end_at=FIXED_NOW - timedelta(days=8),
            duration_seconds=int(timedelta(days=2).total_seconds()),
            is_open=False,
        )
    )
    session.flush()

    # Before backfill: two groups — orphaned + id-keyed.
    groups_before = discover_status_groups(session, tenant.client_key)
    names_before = {g[0] for g in groups_before}
    assert "In Review" in names_before
    assert "Code Review" in names_before

    backfill_legacy_status_ids(
        session, tenant.client_key, {"In Review": "10042", "Code Review": "10042"}
    )

    # After backfill: one group under the current name.
    groups_after = discover_status_groups(session, tenant.client_key)
    names_after = {g[0] for g in groups_after}
    assert "Code Review" in names_after
    assert "In Review" not in names_after, (
        f"after Path B backfill, 'In Review' should not appear as a "
        f"separate display group — got groups {groups_after}"
    )
    # The merged group includes both name variants in its variants list.
    code_review_group = next(g for g in groups_after if g[0] == "Code Review")
    assert set(code_review_group[1]) >= {"In Review", "Code Review"}


# --- Integration: POST /api/forge/backfill/status-ids -----------------------


@pytest.fixture
def client(session: Session, tenant: Tenant) -> Iterator[TestClient]:
    app = create_app(with_jwt_middleware=False)

    def _override_db() -> Iterator[Session]:
        yield session

    def _override_ctx() -> TenantContext:
        return TenantContext(tenant=tenant, settings=Settings())

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_tenant_context] = _override_ctx
    return TestClient(app)


def test_endpoint_returns_result_json(client: TestClient, session: Session, tenant: Tenant) -> None:
    issue = _make_issue(session, tenant.client_key, "10100")
    _legacy_slice(session, tenant.client_key, issue.id, "In Review", 80, 75)
    _legacy_transition(session, tenant.client_key, issue.id, "To Do", "In Review", 80)

    response = client.post(
        "/api/forge/backfill/status-ids",
        json={
            "statuses": [
                {"id": "10000", "name": "To Do"},
                {"id": "10042", "name": "In Review"},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "updated_transitions" in body
    assert "updated_slices" in body
    assert "unresolved_names" in body
    assert body["updated_slices"] >= 1
    assert body["unresolved_names"] == []


def test_endpoint_returns_unresolved_names(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    issue = _make_issue(session, tenant.client_key, "10101")
    _legacy_slice(session, tenant.client_key, issue.id, "Deleted Status", 80, 75)

    response = client.post(
        "/api/forge/backfill/status-ids",
        json={"statuses": [{"id": "10000", "name": "To Do"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Deleted Status" in body["unresolved_names"]


def test_endpoint_rejects_malformed_payload(client: TestClient) -> None:
    """Pydantic validation surface — missing required field returns 422,
    not 500. Belt-and-suspenders for the resolver passing bad shape."""
    response = client.post("/api/forge/backfill/status-ids", json={"not_statuses": []})
    assert response.status_code == 422


def test_endpoint_idempotent_via_repeat_call(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    issue = _make_issue(session, tenant.client_key, "10102")
    _legacy_slice(session, tenant.client_key, issue.id, "In Review", 80, 75)
    payload = {"statuses": [{"id": "10042", "name": "In Review"}]}

    r1 = client.post("/api/forge/backfill/status-ids", json=payload)
    r2 = client.post("/api/forge/backfill/status-ids", json=payload)
    assert r1.json()["updated_slices"] == 1
    assert r2.json()["updated_slices"] == 0
