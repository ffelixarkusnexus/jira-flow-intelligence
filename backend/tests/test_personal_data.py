"""Personal Data Reporting endpoints — Marketplace compliance.

Two endpoints:
- `GET  /api/forge/personal-data/accounts` — paginated distinct
  accountIds the tenant has stored, with each account's most-recent
  issue.updated_at.
- `POST /api/forge/personal-data/erase` — null out
  assignee + assignee_account_id for the given accountIds, scoped to
  the calling tenant.

These tests cover the contract the Forge weekly poller depends on:
deterministic ordering for cursor paging, idempotent erasure, and
strict tenant scoping (one tenant cannot wipe another's data even if
accountIds collide).
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
from app.db.models import Issue, Tenant
from app.db.session import get_db
from app.main import create_app
from tests.conftest import make_tenant


def _make_issue(
    *,
    tenant_id: str,
    issue_id: str,
    assignee: str | None,
    assignee_account_id: str | None,
    updated_at: datetime,
) -> Issue:
    return Issue(
        tenant_id=tenant_id,
        id=issue_id,
        key=issue_id,
        project_key="DEMO",
        created_at=updated_at - timedelta(days=1),
        updated_at=updated_at,
        current_status="In Progress",
        assignee=assignee,
        assignee_account_id=assignee_account_id,
    )


@pytest.fixture
def client(session: Session, tenant: Tenant) -> TestClient:
    app = create_app(with_jwt_middleware=False)

    def _override_db() -> Iterator[Session]:
        yield session

    def _override_ctx() -> TenantContext:
        return TenantContext(tenant=tenant, settings=Settings())

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_tenant_context] = _override_ctx
    return TestClient(app)


def test_accounts_returns_distinct_ids_with_max_updated_at(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """Two issues for one account → one entry, with the LATER timestamp.
    The poller uses updated_at to decide whether the data has changed
    since last cycle; we must report the freshest snapshot."""
    older = datetime(2026, 4, 1, tzinfo=UTC)
    newer = datetime(2026, 5, 1, tzinfo=UTC)
    session.add_all(
        [
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-1",
                assignee="Alice",
                assignee_account_id="acct-alice",
                updated_at=older,
            ),
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-2",
                assignee="Alice",
                assignee_account_id="acct-alice",
                updated_at=newer,
            ),
        ]
    )
    session.commit()

    res = client.get("/api/forge/personal-data/accounts")
    assert res.status_code == 200
    body = res.json()
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["account_id"] == "acct-alice"
    assert body["accounts"][0]["updated_at"].startswith("2026-05-01")
    assert body["next_cursor"] is None


def test_accounts_excludes_unassigned(client: TestClient, session: Session, tenant: Tenant) -> None:
    """Issues with no assignee_account_id contain no personal data —
    the API skips them entirely."""
    session.add_all(
        [
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-A",
                assignee="Alice",
                assignee_account_id="acct-alice",
                updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-NULL",
                assignee=None,
                assignee_account_id=None,
                updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    res = client.get("/api/forge/personal-data/accounts")
    assert res.status_code == 200
    body = res.json()
    assert {a["account_id"] for a in body["accounts"]} == {"acct-alice"}


def test_accounts_paginates_with_cursor(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """Cursor-based paging: first call returns N, second call with the
    cursor returns the next N. The poller's Forge invocation has a 25s
    cap, so paging matters for large tenants."""
    base = datetime(2026, 5, 1, tzinfo=UTC)
    session.add_all(
        [
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id=f"I-{i}",
                assignee=f"User {i}",
                assignee_account_id=f"acct-{i:03d}",
                updated_at=base,
            )
            for i in range(5)
        ]
    )
    session.commit()

    # First page: limit=2, expect 2 + a cursor pointing at the 2nd id
    res = client.get("/api/forge/personal-data/accounts?limit=2")
    assert res.status_code == 200
    body = res.json()
    assert [a["account_id"] for a in body["accounts"]] == ["acct-000", "acct-001"]
    assert body["next_cursor"] == "acct-001"

    # Second page using that cursor
    res = client.get(f"/api/forge/personal-data/accounts?limit=2&cursor={body['next_cursor']}")
    body = res.json()
    assert [a["account_id"] for a in body["accounts"]] == ["acct-002", "acct-003"]
    assert body["next_cursor"] == "acct-003"

    # Third page — last record, no more pages
    res = client.get(f"/api/forge/personal-data/accounts?limit=2&cursor={body['next_cursor']}")
    body = res.json()
    assert [a["account_id"] for a in body["accounts"]] == ["acct-004"]
    assert body["next_cursor"] is None


def test_erase_nulls_assignee_fields(client: TestClient, session: Session, tenant: Tenant) -> None:
    """Closed-account erasure path: both assignee (display name) and
    assignee_account_id are nulled out for every matching issue."""
    session.add_all(
        [
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-A",
                assignee="Alice",
                assignee_account_id="acct-alice",
                updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-B",
                assignee="Bob",
                assignee_account_id="acct-bob",
                updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    res = client.post(
        "/api/forge/personal-data/erase",
        json={"account_ids": ["acct-alice"]},
    )
    assert res.status_code == 200
    assert res.json()["issues_updated"] == 1

    session.expire_all()
    issues_by_id = {i.id: i for i in session.query(Issue).all()}
    assert issues_by_id["I-A"].assignee is None
    assert issues_by_id["I-A"].assignee_account_id is None
    assert issues_by_id["I-B"].assignee == "Bob"
    assert issues_by_id["I-B"].assignee_account_id == "acct-bob"


def test_erase_is_idempotent(client: TestClient, session: Session, tenant: Tenant) -> None:
    """Running erase twice with the same accountId is safe — second
    run finds no matching rows and returns issues_updated=0. Matters
    because Atlassian's protocol may report the same closed account
    in successive cycles until our refresh propagates."""
    session.add(
        _make_issue(
            tenant_id=tenant.client_key,
            issue_id="I-A",
            assignee="Alice",
            assignee_account_id="acct-alice",
            updated_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    session.commit()

    first = client.post(
        "/api/forge/personal-data/erase",
        json={"account_ids": ["acct-alice"]},
    )
    second = client.post(
        "/api/forge/personal-data/erase",
        json={"account_ids": ["acct-alice"]},
    )
    assert first.json()["issues_updated"] == 1
    assert second.json()["issues_updated"] == 0


def test_erase_does_not_cross_tenant_boundary(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """A and B are different tenants. Tenant A asks to erase
    `acct-shared`; Tenant B's identical accountId data is unchanged.
    Critical: the erase router accepts a list of accountIds from the
    caller, but the WHERE clause MUST always include tenant_id, or
    one tenant could wipe another's data."""
    other_tenant = make_tenant(session, client_key="other-tenant")
    base = datetime(2026, 5, 1, tzinfo=UTC)
    session.add_all(
        [
            _make_issue(
                tenant_id=tenant.client_key,
                issue_id="I-CALLER",
                assignee="Shared",
                assignee_account_id="acct-shared",
                updated_at=base,
            ),
            _make_issue(
                tenant_id=other_tenant.client_key,
                issue_id="I-OTHER",
                assignee="Shared",
                assignee_account_id="acct-shared",
                updated_at=base,
            ),
        ]
    )
    session.commit()

    res = client.post(
        "/api/forge/personal-data/erase",
        json={"account_ids": ["acct-shared"]},
    )
    assert res.status_code == 200
    assert res.json()["issues_updated"] == 1  # only the caller's row

    session.expire_all()
    caller_issue = session.get(Issue, (tenant.client_key, "I-CALLER"))
    other_issue = session.get(Issue, (other_tenant.client_key, "I-OTHER"))
    assert caller_issue is not None
    assert other_issue is not None
    assert caller_issue.assignee is None
    # Other tenant's data must still be intact
    assert other_issue.assignee == "Shared"
    assert other_issue.assignee_account_id == "acct-shared"


def test_erase_with_empty_list_is_noop(client: TestClient) -> None:
    """Sanity: empty input = empty output, no crash."""
    res = client.post(
        "/api/forge/personal-data/erase",
        json={"account_ids": []},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["erased_account_ids"] == []
    assert body["issues_updated"] == 0
