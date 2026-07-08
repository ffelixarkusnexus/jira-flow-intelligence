"""Smoke test for /api/forge/sync/ingest.

End-to-end: POST a small Jira-shaped payload, confirm the engine writes
issues + transitions + slices, attributed to the seeded tenant.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Issue, Tenant, TimeSlice, Transition
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def client(session: Session, tenant: Tenant) -> Iterator[TestClient]:
    app = create_app(with_jwt_middleware=False)

    def _override_db() -> Iterator[Session]:
        yield session

    def _override_ctx() -> TenantContext:
        from app.core.config import Settings

        return TenantContext(tenant=tenant, settings=Settings())

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_tenant_context] = _override_ctx
    return TestClient(app)


def _issue_payload(key: str) -> dict[str, Any]:
    return {
        "id": key,
        "key": key,
        "fields": {
            "created": "2026-04-30T10:00:00.000Z",
            "updated": "2026-05-01T10:00:00.000Z",
            "status": {"name": "Done"},
            "issuetype": {"name": "Story"},
            "project": {"key": "RM"},
            "summary": f"Test {key}",
            "resolutiondate": "2026-05-01T10:00:00.000Z",
        },
        "changelog": {
            "histories": [
                {
                    "created": "2026-04-30T11:00:00.000Z",
                    "items": [{"field": "status", "fromString": "Todo", "toString": "In Progress"}],
                },
                {
                    "created": "2026-04-30T20:00:00.000Z",
                    "items": [
                        {"field": "status", "fromString": "In Progress", "toString": "Review"}
                    ],
                },
                {
                    "created": "2026-05-01T10:00:00.000Z",
                    "items": [{"field": "status", "fromString": "Review", "toString": "Done"}],
                },
            ]
        },
    }


def test_ingest_writes_issues_transitions_slices(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    payloads = [_issue_payload("RM-1"), _issue_payload("RM-2")]
    res = client.post("/api/forge/sync/ingest", json={"payloads": payloads})
    assert res.status_code == 200
    body = res.json()
    assert body["issues"] == 2
    assert body["transitions"] >= 4  # at least two per issue
    assert body["slices"] >= 4

    issues = session.execute(select(Issue).where(Issue.tenant_id == tenant.client_key)).all()
    assert len(issues) == 2
    transitions = session.execute(
        select(Transition).where(Transition.tenant_id == tenant.client_key)
    ).all()
    assert len(transitions) >= 4
    slices = session.execute(
        select(TimeSlice).where(TimeSlice.tenant_id == tenant.client_key)
    ).all()
    assert len(slices) >= 4


def test_ingest_empty_payloads_is_noop(client: TestClient) -> None:
    res = client.post("/api/forge/sync/ingest", json={"payloads": []})
    assert res.status_code == 200
    assert res.json() == {"issues": 0, "transitions": 0, "slices": 0, "errors": 0}


def test_ingest_skip_if_stale_skips_unchanged_issues(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """Webhook idempotency: when skip_if_stale=True and the existing row's
    updated_at is >= incoming payload, no transitions are rewritten."""
    payload = _issue_payload("RM-1")
    # First ingest writes the issue.
    client.post("/api/forge/sync/ingest", json={"payloads": [payload]})

    # Second ingest with same payload + skip_if_stale=True is a no-op.
    res = client.post(
        "/api/forge/sync/ingest",
        json={"payloads": [payload], "skip_if_stale": True},
    )
    assert res.status_code == 200
    assert res.json()["issues"] == 0  # nothing processed


def test_ingest_skip_if_stale_processes_when_payload_is_newer(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """A payload with a newer `updated` timestamp does get processed even
    when skip_if_stale is set."""
    payload = _issue_payload("RM-1")
    client.post("/api/forge/sync/ingest", json={"payloads": [payload]})

    newer = dict(payload)
    newer_fields = dict(payload["fields"])
    newer_fields["updated"] = "2026-05-02T10:00:00.000Z"  # later than first
    newer["fields"] = newer_fields

    res = client.post(
        "/api/forge/sync/ingest",
        json={"payloads": [newer], "skip_if_stale": True},
    )
    assert res.status_code == 200
    assert res.json()["issues"] == 1


def test_delete_issue_removes_row_and_cascades(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """`avi:jira:deleted:issue` path: the issue + its transitions and slices
    drop via FK CASCADE."""
    payload = _issue_payload("RM-1")
    client.post("/api/forge/sync/ingest", json={"payloads": [payload]})
    assert session.get(Issue, (tenant.client_key, "RM-1")) is not None

    res = client.delete("/api/forge/sync/issues/RM-1")
    assert res.status_code == 204
    assert session.get(Issue, (tenant.client_key, "RM-1")) is None
    # FK cascade drops descendants.
    assert (
        session.execute(select(Transition).where(Transition.tenant_id == tenant.client_key)).first()
        is None
    )
    assert (
        session.execute(select(TimeSlice).where(TimeSlice.tenant_id == tenant.client_key)).first()
        is None
    )


def test_delete_issue_unknown_id_is_noop(client: TestClient) -> None:
    """Webhooks fire for issues we never synced (out-of-window). 204 is OK."""
    res = client.delete("/api/forge/sync/issues/NOPE-9999")
    assert res.status_code == 204


# ----- backfill state machine ---------------------------------------------


def test_state_returns_backfill_block_unset_initially(client: TestClient) -> None:
    res = client.get("/api/forge/sync/state")
    assert res.status_code == 200
    body = res.json()
    assert "backfill" in body
    assert body["backfill"]["status"] is None
    assert body["backfill"]["processedIssues"] is None


def test_backfill_start_then_progress_through_completion(
    client: TestClient, session: Session, tenant: Tenant
) -> None:
    """Consumer flow: start → progress (multi-batch) → done."""
    start = client.post(
        "/api/forge/sync/backfill/start",
        json={"total_estimate": 250},
    )
    assert start.status_code == 200
    state = client.get("/api/forge/sync/state").json()
    assert state["backfill"]["status"] == "running"
    assert state["backfill"]["totalIssues"] == 250
    assert state["backfill"]["processedIssues"] == 0
    assert state["backfill"]["startedAt"] is not None

    # First batch: 100 processed, more to go.
    p1 = client.post(
        "/api/forge/sync/backfill/progress",
        json={"processed_delta": 100, "next_page_token": "tok-2", "done": False},
    )
    assert p1.status_code == 200
    state = client.get("/api/forge/sync/state").json()
    assert state["backfill"]["processedIssues"] == 100
    assert state["backfill"]["status"] == "running"

    # Second batch: another 100, still running.
    client.post(
        "/api/forge/sync/backfill/progress",
        json={"processed_delta": 100, "next_page_token": "tok-3", "done": False},
    )
    # Third batch: last 50, done.
    client.post(
        "/api/forge/sync/backfill/progress",
        json={"processed_delta": 50, "next_page_token": None, "done": True},
    )
    state = client.get("/api/forge/sync/state").json()
    assert state["backfill"]["processedIssues"] == 250
    assert state["backfill"]["status"] == "completed"
    assert state["backfill"]["completedAt"] is not None


def test_backfill_progress_with_error_flips_status_failed(client: TestClient) -> None:
    client.post("/api/forge/sync/backfill/start", json={"total_estimate": 100})
    res = client.post(
        "/api/forge/sync/backfill/progress",
        json={"processed_delta": 30, "error": "Jira 5xx after 3 retries"},
    )
    assert res.status_code == 200
    state = client.get("/api/forge/sync/state").json()
    assert state["backfill"]["status"] == "failed"
    assert state["backfill"]["error"] == "Jira 5xx after 3 retries"
    assert state["backfill"]["processedIssues"] == 30
    assert state["backfill"]["completedAt"] is not None


def test_backfill_start_is_idempotent_and_resets_progress(client: TestClient) -> None:
    """Re-starting a backfill (e.g. user retries after a failure) resets
    counters and clears the prior error."""
    client.post("/api/forge/sync/backfill/start", json={"total_estimate": 100})
    client.post(
        "/api/forge/sync/backfill/progress",
        json={"processed_delta": 50, "error": "transient"},
    )
    state = client.get("/api/forge/sync/state").json()
    assert state["backfill"]["status"] == "failed"

    client.post("/api/forge/sync/backfill/start", json={"total_estimate": 200})
    state = client.get("/api/forge/sync/state").json()
    assert state["backfill"]["status"] == "running"
    assert state["backfill"]["processedIssues"] == 0
    assert state["backfill"]["totalIssues"] == 200
    assert state["backfill"]["error"] is None
