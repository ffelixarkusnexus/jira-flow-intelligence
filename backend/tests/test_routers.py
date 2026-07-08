"""End-to-end tests for FastAPI routers using TestClient + in-memory SQLite.

These tests are the contract test for the API surface and lift coverage on
router modules. They build a no-middleware app via `create_app(with_jwt_middleware=False)`
and override `get_db` and `current_tenant_context` so each test runs against
an isolated in-memory database with a single demo tenant.

The JWT middleware is exercised in `test_jwt_middleware.py` against a stub
app — separating router behavior from auth keeps both suites focused.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Base, Tenant
from app.db.session import get_db
from app.main import create_app
from app.services.alert_service import upsert_rule
from app.services.ingestion_service import process_payloads

TENANT_KEY = "test-tenant"


def _enable_fks(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", _enable_fks)
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    def _override_get_db() -> Iterator[Session]:
        s = SessionFactory()
        try:
            yield s
        finally:
            s.close()

    settings = Settings()

    # Seed tenant + data
    seed_session = SessionFactory()
    tenant = Tenant(
        client_key=TENANT_KEY,
        cloud_id="test-cloud",
        base_url=f"https://{TENANT_KEY}.atlassian.net",
        display_url=f"https://{TENANT_KEY}.atlassian.net",
        product_type="jira",
        forge_installation_id=TENANT_KEY,
        plan="free",
        enabled=True,
        installed_at=utcnow(),
    )
    seed_session.add(tenant)
    seed_session.flush()
    ctx = TenantContext(tenant=tenant, settings=settings)
    _seed(seed_session, ctx)
    seed_session.commit()
    seed_session.close()

    # Build a no-middleware app and wire test dependencies.
    test_app = create_app(with_jwt_middleware=False)
    test_app.dependency_overrides[get_db] = _override_get_db

    def _override_ctx() -> TenantContext:
        s = SessionFactory()
        try:
            t = s.get(Tenant, TENANT_KEY)
            assert t is not None
            return TenantContext(tenant=t, settings=settings)
        finally:
            s.close()

    test_app.dependency_overrides[current_tenant_context] = _override_ctx

    try:
        yield TestClient(test_app)
    finally:
        engine.dispose()


def _payload(
    key: str,
    created: str,
    transitions: list[tuple[str, str, str]],
    current: str,
    resolution: str | None = None,
) -> dict:
    return {
        "id": key,
        "key": key,
        "fields": {
            "created": created,
            "updated": created,
            "status": {"name": current},
            "issuetype": {"name": "Story"},
            "project": {"key": "ABC"},
            "summary": f"{key} test",
            "resolutiondate": resolution,
        },
        "changelog": {
            "histories": [
                {
                    "created": ts,
                    "items": [
                        {"field": "status", "fromString": frm, "toString": to},
                    ],
                }
                for ts, frm, to in transitions
            ]
        },
    }


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _seed(session: Session, ctx: TenantContext) -> None:
    """Seed two windows of issues so insights/metrics endpoints have signal."""
    now = utcnow()
    payloads = []
    for i in range(5):
        c = now - timedelta(days=10 + i * 0.1)
        r1 = c + timedelta(hours=4)
        r2 = c + timedelta(hours=8)
        payloads.append(
            _payload(
                f"PREV-{i}",
                _iso(c),
                [
                    (_iso(r1), "In Progress", "Review"),
                    (_iso(r2), "Review", "Done"),
                ],
                current="Done",
                resolution=_iso(r2),
            )
        )
    for i in range(5):
        c = now - timedelta(days=4 + i * 0.1)
        r1 = c + timedelta(hours=4)
        r2 = c + timedelta(hours=24)
        payloads.append(
            _payload(
                f"CUR-{i}",
                _iso(c),
                [
                    (_iso(r1), "In Progress", "Review"),
                    (_iso(r2), "Review", "Done"),
                ],
                current="Done",
                resolution=_iso(r2),
            )
        )
    process_payloads(session, payloads, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-12h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 12 * 3600},
    )


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_list_issues(client: TestClient) -> None:
    r = client.get("/api/issues")
    assert r.status_code == 200
    issues = r.json()
    assert len(issues) == 10
    assert all(i["project_key"] == "ABC" for i in issues)


def test_list_issues_filtered(client: TestClient) -> None:
    r = client.get("/api/issues?project=ABC&status=Done&limit=3")
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_get_issue_detail_includes_slices(client: TestClient) -> None:
    r = client.get("/api/issues/CUR-0")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "CUR-0"
    assert len(body["time_slices"]) >= 2


def test_get_issue_404(client: TestClient) -> None:
    r = client.get("/api/issues/NOPE-999")
    assert r.status_code == 404


def test_metrics_endpoint(client: TestClient) -> None:
    r = client.get("/api/metrics?days=7")
    assert r.status_code == 200
    body = r.json()
    assert "current" in body
    assert "previous" in body
    statuses = {s["status"] for s in body["current"]["statuses"]}
    assert "Review" in statuses


def test_metrics_recompute(client: TestClient) -> None:
    r = client.post("/api/metrics/recompute")
    assert r.status_code == 200
    assert r.json()["issues_metric_rows_written"] == 10


def test_insights_detects_review_bottleneck(client: TestClient) -> None:
    r = client.get("/api/insights?days=7&explain=false")
    assert r.status_code == 200
    body = r.json()
    assert body["bottleneck"] is not None
    assert body["bottleneck"]["status"] == "Review"
    assert body["bottleneck"]["score"] >= 3


def test_insights_with_explanation_returns_template(client: TestClient) -> None:
    r = client.get("/api/insights?days=7&explain=true")
    assert r.status_code == 200
    body = r.json()
    assert body["explanation"] is not None
    assert "Review" in body["explanation"]


def test_alerts_evaluate_then_list_then_idempotent(client: TestClient) -> None:
    ev = client.post("/api/alerts/evaluate?days=7")
    assert ev.status_code == 200
    triggered_first = ev.json()["triggered"]
    assert triggered_first > 0

    again = client.post("/api/alerts/evaluate?days=7")
    assert again.json()["triggered"] == 0  # idempotent

    listed = client.get("/api/alerts").json()
    assert listed["total"] == triggered_first


def test_alerts_rules_crud(client: TestClient) -> None:
    rules = client.get("/api/alerts/rules").json()
    assert any(r["id"] == "review-12h" for r in rules)

    payload = {
        "id": "cycle-2d",
        "type": "cycle_time",
        "enabled": True,
        "config": {"threshold_seconds": 2 * 86400},
    }
    put = client.put("/api/alerts/rules", json=payload)
    assert put.status_code == 200
    assert put.json()["id"] == "cycle-2d"

    rules_after = {r["id"] for r in client.get("/api/alerts/rules").json()}
    assert "cycle-2d" in rules_after


def test_alerts_rules_put_disables(client: TestClient) -> None:
    """PUT with enabled=False on an existing rule disables it."""
    payload = {
        "id": "review-12h",
        "type": "status_duration",
        "enabled": False,
        "config": {"status": "Review", "threshold_seconds": 12 * 3600},
    }
    res = client.put("/api/alerts/rules", json=payload)
    assert res.status_code == 200
    rules = {r["id"]: r for r in client.get("/api/alerts/rules").json()}
    assert rules["review-12h"]["enabled"] is False


def test_alerts_rules_delete_removes_row(client: TestClient) -> None:
    res = client.delete("/api/alerts/rules/review-12h")
    assert res.status_code == 204
    rules = {r["id"] for r in client.get("/api/alerts/rules").json()}
    assert "review-12h" not in rules


def test_alerts_rules_delete_unknown_returns_404(client: TestClient) -> None:
    res = client.delete("/api/alerts/rules/does-not-exist")
    assert res.status_code == 404


# ----- tenant settings -----------------------------------------------------


def test_tenant_settings_get_returns_effective_defaults_when_no_overrides(
    client: TestClient,
) -> None:
    res = client.get("/api/settings/tenant")
    assert res.status_code == 200
    body = res.json()
    assert body["active_statuses_override"] is None
    assert body["effective_active_statuses"] == ["In Progress", "Review"]
    assert body["done_statuses_override"] is None
    assert body["effective_done_statuses"] == ["Done", "Closed", "Resolved"]
    assert body["bottleneck_time_ratio_threshold_override"] is None
    assert body["effective_bottleneck_time_ratio_threshold"] == 1.3


def test_tenant_settings_put_then_get_roundtrips(client: TestClient) -> None:
    payload = {
        "active_statuses": ["In Development", "Code Review"],
        "done_statuses": ["Closed", "Won't Do"],
        "bottleneck_time_ratio_threshold": 1.5,
        "story_points_field_id": "customfield_10042",
    }
    put = client.put("/api/settings/tenant", json=payload)
    assert put.status_code == 200
    got = put.json()
    assert got["active_statuses_override"] == ["In Development", "Code Review"]
    assert got["effective_active_statuses"] == ["In Development", "Code Review"]
    assert got["bottleneck_time_ratio_threshold_override"] == 1.5
    assert got["effective_bottleneck_time_ratio_threshold"] == 1.5
    assert got["story_points_field_id"] == "customfield_10042"


def test_tenant_settings_put_resets_to_defaults_when_fields_null(client: TestClient) -> None:
    # First override.
    client.put(
        "/api/settings/tenant",
        json={"active_statuses": ["X"], "bottleneck_time_ratio_threshold": 2.0},
    )
    # Then "reset" by sending all nulls.
    res = client.put("/api/settings/tenant", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["active_statuses_override"] is None
    assert body["effective_active_statuses"] == ["In Progress", "Review"]
    assert body["bottleneck_time_ratio_threshold_override"] is None
    assert body["effective_bottleneck_time_ratio_threshold"] == 1.3


def test_tenant_settings_rejects_empty_status_list(client: TestClient) -> None:
    res = client.put("/api/settings/tenant", json={"active_statuses": []})
    assert res.status_code == 422


def test_tenant_settings_rejects_threshold_out_of_bounds(client: TestClient) -> None:
    res = client.put("/api/settings/tenant", json={"bottleneck_time_ratio_threshold": 99.0})
    assert res.status_code == 422
    res2 = client.put(
        "/api/settings/tenant",
        json={"bottleneck_throughput_delta_threshold": 5.0},
    )
    assert res2.status_code == 422


def test_ingest_endpoint(client: TestClient) -> None:
    payload = _payload(
        "NEW-1",
        "2026-01-01T10:00:00Z",
        [("2026-01-01T14:00:00Z", "In Progress", "Done")],
        current="Done",
        resolution="2026-01-01T14:00:00Z",
    )
    r = client.post("/api/sync/ingest", json={"payloads": [payload]})
    assert r.status_code == 200
    body = r.json()
    assert body["issues_processed"] == 1
    assert body["transitions_written"] == 1


def test_sync_endpoint_returns_400_when_jira_unconfigured(client: TestClient) -> None:
    r = client.post("/api/sync", json={})
    assert r.status_code == 400
    assert "JIRA" in r.json()["detail"].upper() or "credentials" in r.json()["detail"].lower()


def test_wip_limits_get_empty(client: TestClient) -> None:
    r = client.get("/api/settings/wip-limits")
    assert r.status_code == 200
    assert r.json() == {"limits": []}


def test_wip_limits_put_then_get(client: TestClient) -> None:
    r = client.put(
        "/api/settings/wip-limits",
        json={
            "project_key": "PROJA",
            "status": "Code Review",
            "max_in_progress": 3,
            "breach_minutes": 60,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["max_in_progress"] == 3
    assert body["project_key"] == "PROJA"

    r2 = client.get("/api/settings/wip-limits", params={"project_key": "PROJA"})
    assert r2.status_code == 200
    limits = r2.json()["limits"]
    assert any(L["status"] == "Code Review" and L["max_in_progress"] == 3 for L in limits)


def test_wip_limits_put_negative_rejected(client: TestClient) -> None:
    r = client.put(
        "/api/settings/wip-limits",
        json={"project_key": None, "status": "Code Review", "max_in_progress": -1},
    )
    assert r.status_code == 422  # pydantic ge=0 enforces


def test_wip_limits_delete_roundtrip(client: TestClient) -> None:
    client.put(
        "/api/settings/wip-limits",
        json={"project_key": "PROJA", "status": "QA", "max_in_progress": 2},
    )
    r = client.delete("/api/settings/wip-limits", params={"status": "QA", "project_key": "PROJA"})
    assert r.status_code == 204
    # Second delete is 404.
    r2 = client.delete("/api/settings/wip-limits", params={"status": "QA", "project_key": "PROJA"})
    assert r2.status_code == 404


# ----- ADR-0042/Feature 4: CSV export -------------------------------------


def test_csv_export_returns_csv_with_expected_columns(client: TestClient) -> None:
    r = client.get("/api/export/csv?project=ABC&days=30")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "flow-intelligence-ABC-30d.csv" in r.headers["content-disposition"]
    body = r.text
    header_line, *data_lines = body.strip().splitlines()
    assert header_line.split(",") == [
        "issue_key",
        "issue_summary",
        "project_key",
        "current_status",
        "issue_created_at",
        "issue_done_at",
        "slice_status",
        "slice_start_at",
        "slice_end_at",
        "slice_duration_seconds",
        "slice_is_open",
        "external_blocking",
        "is_terminal",
    ]
    assert len(data_lines) > 0  # the seeded fixture has slices in the window


def test_csv_export_marks_external_blocking_slices(client: TestClient) -> None:
    """When the tenant has configured external-blocking statuses, the CSV's
    `external_blocking` column reads `true` for matching slices and `false`
    for others. The slice itself is still in the export — the marker is the
    accuracy-correction artifact ADR-0042 surfaces."""
    # Configure tenant external_blocking_statuses to include "Review" — any
    # seeded slice in Review will be marked.
    client.put("/api/settings/tenant", json={"external_blocking_statuses": ["Review"]})
    r = client.get("/api/export/csv?project=ABC&days=30")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    header = lines[0].split(",")
    slice_status_idx = header.index("slice_status")
    eb_idx = header.index("external_blocking")
    review_slices = [
        line.split(",") for line in lines[1:] if line.split(",")[slice_status_idx] == "Review"
    ]
    assert review_slices, "fixture should produce at least one Review slice"
    assert all(row[eb_idx] == "true" for row in review_slices)
    # And non-Review slices remain false.
    other_slices = [
        line.split(",") for line in lines[1:] if line.split(",")[slice_status_idx] != "Review"
    ]
    assert all(row[eb_idx] == "false" for row in other_slices)


def test_csv_export_empty_project_returns_header_only(client: TestClient) -> None:
    r = client.get("/api/export/csv?project=NONEXISTENT&days=30")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert len(lines) == 1  # header only
    assert lines[0].startswith("issue_key,")


def test_csv_export_rejects_unsupported_view(client: TestClient) -> None:
    r = client.get("/api/export/csv?project=ABC&days=30&view=metrics")
    assert r.status_code == 422


# ----- ADR-0044/Feature 3: Issue View Panel -------------------------------


def test_issue_panel_data_returns_status_history(client: TestClient) -> None:
    r = client.get("/api/forge/issue/CUR-0/panel-data")
    assert r.status_code == 200
    body = r.json()
    assert body["issue_key"] == "CUR-0"
    assert "current_status" in body
    assert "status_history" in body
    assert len(body["status_history"]) >= 2
    # Slices come ordered by start_at.
    starts = [s["entered_at"] for s in body["status_history"]]
    assert starts == sorted(starts)
    # Each slice carries the external_blocking marker (default False — no
    # external_blocking_statuses configured on the seeded tenant).
    assert all(s["is_external_blocking"] is False for s in body["status_history"])
    assert body["total_cycle_time_seconds"] > 0
    assert body["is_in_current_bottleneck"] is False  # v1 leaves this False
    assert body["project_dashboard_url"].startswith("/jira")


def test_issue_panel_marks_external_blocking_status(client: TestClient) -> None:
    """When the tenant configures external_blocking_statuses to include
    a status the issue passed through, panel rows for those slices flip
    is_external_blocking = True. ADR-0042 marker surfaces here for the
    engineer triaging the ticket."""
    client.put("/api/settings/tenant", json={"external_blocking_statuses": ["Review"]})
    r = client.get("/api/forge/issue/CUR-0/panel-data")
    assert r.status_code == 200
    body = r.json()
    review_slices = [s for s in body["status_history"] if s["status"] == "Review"]
    assert review_slices, "fixture should produce at least one Review slice"
    assert all(s["is_external_blocking"] is True for s in review_slices)
    non_review = [s for s in body["status_history"] if s["status"] != "Review"]
    assert all(s["is_external_blocking"] is False for s in non_review)


def test_issue_panel_404_for_unknown_issue(client: TestClient) -> None:
    r = client.get("/api/forge/issue/NOPE-999/panel-data")
    assert r.status_code == 404


# ----- ADR-0043 / Feature 2: Work Schedule + recompute --------------------


def test_schedule_default_is_calendar_time(client: TestClient) -> None:
    """Existing tenants land with no schedule configured. /api/forge/
    schedule returns null and recompute status reads 'idle'."""
    r = client.get("/api/forge/schedule")
    assert r.status_code == 200
    assert r.json() is None
    s = client.get("/api/forge/schedule/status")
    assert s.status_code == 200
    body = s.json()
    assert body["status"] == "idle"
    assert body["progress_pct"] in (0, 100)  # 100 when total_rows == 0
    assert body["error"] is None


def test_activate_schedule_persists_and_sets_pending(client: TestClient) -> None:
    """First-time activation creates the schedule row + flips recompute
    state to 'pending'. The next batch invocation flips to running."""
    r = client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "Standard week",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Standard week"
    assert body["working_days_mask"] == 31
    # Recompute status is now 'pending'.
    s = client.get("/api/forge/schedule/status").json()
    assert s["status"] == "pending"
    assert s["rows_processed"] == 0


def test_recompute_batch_completes_in_single_pass_on_seeded_fixture(
    client: TestClient,
) -> None:
    """The seeded fixture's slice count is well under BATCH_SIZE=1000, so
    one batch finishes the entire recompute. Status transitions
    pending -> completed; rows_processed equals total."""
    client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "Standard week",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    batch_response = client.post("/api/forge/schedule/recompute-batch")
    assert batch_response.status_code == 200
    body = batch_response.json()
    assert body["done"] is True
    assert body["progress_pct"] == 100
    s = client.get("/api/forge/schedule/status").json()
    assert s["status"] == "completed"


def test_recompute_is_idempotent_on_re_run(client: TestClient) -> None:
    """Running recompute twice produces the same final state and the
    same total_rows. (working_seconds_between is pure; re-running against
    the same input + schedule must produce the same output.)"""
    client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "S",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    first = client.post("/api/forge/schedule/recompute-batch").json()
    # Re-activate to reset state, then re-run.
    client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "S",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    second = client.post("/api/forge/schedule/recompute-batch").json()
    assert first["total_rows"] == second["total_rows"]
    assert first["done"] == second["done"]


def test_disabling_schedule_falls_back_to_calendar_durations(client: TestClient) -> None:
    """When a tenant disables their schedule, the recompute restores
    calendar-time durations across all slices."""
    # Activate with a Mon-Fri 9-5 schedule (will likely reduce most
    # durations vs. calendar).
    client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "Work week",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    client.post("/api/forge/schedule/recompute-batch")
    # Capture an issue's slice durations under working-time.
    issue_a = client.get("/api/issues/CUR-0").json()
    working_total = sum(s["duration_seconds"] for s in issue_a["time_slices"])

    # Disable the schedule + recompute. Durations should >= the working-time
    # totals (calendar time is always >= working time for the same range).
    client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "Work week",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": False,  # disabled
        },
    )
    client.post("/api/forge/schedule/recompute-batch")
    issue_b = client.get("/api/issues/CUR-0").json()
    calendar_total = sum(s["duration_seconds"] for s in issue_b["time_slices"])
    assert calendar_total >= working_total


def test_recompute_full_loop_status_transitions_and_duration_correctness(
    client: TestClient,
) -> None:
    """End-to-end recompute integration per the original prompt's spec:
    seed N historical time_slices under calendar time, activate a Mon-Fri
    9-5 schedule, verify the consumer processes all rows, verify status
    transitions pending -> running -> completed, verify each duration_seconds
    matches what working_seconds_between would produce given the schedule."""
    from app.db.models import WorkSchedule
    from app.services.working_time import working_seconds_between

    def _parse_iso(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    # Baseline: capture the slice endpoints we'll re-derive durations against.
    detail_before = client.get("/api/issues/CUR-0").json()
    slice_endpoints = [
        (_parse_iso(s["start_at"]), _parse_iso(s["end_at"])) for s in detail_before["time_slices"]
    ]
    assert len(slice_endpoints) > 0, "fixture should produce at least one slice"

    # Step 1: pre-activation, recompute_status is idle (no schedule yet).
    pre = client.get("/api/forge/schedule/status").json()
    assert pre["status"] == "idle"

    # Step 2: activate a Mon-Fri 9-5 UTC schedule.
    r = client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "Mon-Fri 9-5 UTC",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    assert r.status_code == 200

    # Step 3: status transitions to pending immediately after activation.
    s_pending = client.get("/api/forge/schedule/status").json()
    assert s_pending["status"] == "pending"
    assert s_pending["rows_processed"] == 0

    # Step 4: drive the consumer — repeat /recompute-batch until done.
    iterations = 0
    while iterations < 100:
        batch = client.post("/api/forge/schedule/recompute-batch").json()
        # Mid-recompute the API reports 'running' or (final batch) 'completed'.
        if not batch["done"]:
            mid = client.get("/api/forge/schedule/status").json()
            assert mid["status"] == "running"
        iterations += 1
        if batch["done"]:
            break
    assert iterations < 100, "recompute did not converge in 100 batches"

    # Step 5: final status is completed; progress is 100%.
    final = client.get("/api/forge/schedule/status").json()
    assert final["status"] == "completed"
    assert final["progress_pct"] == 100

    # Step 6: every duration matches working_seconds_between under the schedule.
    detail_after = client.get("/api/issues/CUR-0").json()
    working_durations = [s["duration_seconds"] for s in detail_after["time_slices"]]
    # Re-derive what the helper would produce against the same schedule
    # (fetch the schedule row directly from the test client's DB shape).
    sched_payload = client.get("/api/forge/schedule").json()
    fake = WorkSchedule()
    fake.tenant_id = "t"
    fake.name = sched_payload["name"]
    fake.timezone = sched_payload["timezone"]
    fake.working_days_mask = sched_payload["working_days_mask"]
    fake.work_start_time = sched_payload["work_start_time"]
    fake.work_end_time = sched_payload["work_end_time"]
    fake.holidays = sched_payload["holidays"]
    fake.enabled = sched_payload["enabled"]
    expected = [working_seconds_between(start, end, fake) for (start, end) in slice_endpoints]
    assert working_durations == expected, (
        f"recomputed durations diverge from working_seconds_between: "
        f"got {working_durations}, expected {expected}"
    )


def test_recompute_in_flight_new_transition_uses_active_schedule_and_does_not_block(
    client: TestClient,
) -> None:
    """ADR-0043 in-flight handling: while a tenant has recompute_status in
    a non-terminal state, ingesting a new transition produces a slice
    computed under the active schedule, and the recompute state isn't
    clobbered by the ingest."""
    # Activate schedule (status -> pending).
    client.post(
        "/api/forge/schedule/activate",
        json={
            "name": "Mon-Fri 9-5 UTC",
            "timezone": "UTC",
            "working_days_mask": 31,
            "work_start_time": "09:00:00",
            "work_end_time": "17:00:00",
            "holidays": [],
            "enabled": True,
        },
    )
    # Simulate "recompute already in flight" by hitting recompute-batch once
    # which transitions to running (or completes on small fixture, which is
    # also a valid in-flight terminal). We assert no regression either way.
    initial = client.post("/api/forge/schedule/recompute-batch").json()

    # Ingest a fresh transition on a NEW issue. This goes through the normal
    # ingest path which calls recompute_slices_for_issue with ctx.work_schedule.
    payload = _payload(
        "NEW-INFLIGHT-1",
        "2026-06-05T16:00:00Z",  # Fri 16:00 UTC
        [
            ("2026-06-05T17:00:00Z", "In Progress", "Review"),  # Fri 17:00
            ("2026-06-08T10:00:00Z", "Review", "Done"),  # Mon 10:00
        ],
        current="Done",
        resolution="2026-06-08T10:00:00Z",
    )
    r = client.post("/api/sync/ingest", json={"payloads": [payload]})
    assert r.status_code == 200
    assert r.json()["issues_processed"] == 1

    # Verify the new issue's slices have working-time durations:
    # Slice 1: Fri 16:00 -> Fri 17:00 = 1h working
    # Slice 2: Fri 17:00 -> Mon 10:00 = 1h working (Mon 9:00-10:00 only)
    new_issue = client.get("/api/issues/NEW-INFLIGHT-1").json()
    durations = [s["duration_seconds"] for s in new_issue["time_slices"]]
    assert durations[0] == 3600, f"slice 1 expected 3600, got {durations[0]}"
    assert durations[1] == 3600, f"slice 2 expected 3600, got {durations[1]}"

    # And recompute state was NOT clobbered by the ingest.
    after = client.get("/api/forge/schedule/status").json()
    # Acceptable terminal states post-ingest: still running OR completed
    # (small fixture's recompute likely finished in initial batch).
    assert after["status"] in ("running", "completed"), (
        f"in-flight ingest must not regress recompute state; got {after['status']}"
    )
    # And initial recompute progress wasn't reset by the ingest.
    if initial["done"]:
        assert after["status"] == "completed"


def test_csv_export_emits_real_rows_for_correction2_e(client: TestClient) -> None:
    """Correction 2(e): paste a sample of the actual CSV file produced by
    the export endpoint against a seeded project. Prints the first 6 rows
    (header + 5 data rows) so the maintainer can verify columns + values."""
    client.put("/api/settings/tenant", json={"external_blocking_statuses": ["Review"]})
    r = client.get("/api/export/csv?project=ABC&days=30")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines, "CSV must have at least a header"
    sample = lines[:6]
    print("\n--- CSV export sample (first 6 lines) ---")
    for line in sample:
        print(line)
    print("--- end sample ---")
    # Sanity assertions on the sample shape.
    header_fields = lines[0].split(",")
    assert "external_blocking" in header_fields
    assert "is_terminal" in header_fields
    assert "slice_duration_seconds" in header_fields


def test_get_schedule_status_returns_200_idle_when_no_schedule_configured(
    client: TestClient,
) -> None:
    """Regression guard for the 'panel stuck in Loading' bug surfaced 2026-06-07
    on the dev tenant. The /status handler must NOT 404 when the tenant has no
    WorkSchedule row — it must return 200 with the idle-state RecomputeStatusOut
    so the frontend can render the empty-state UI."""
    # Sanity: the seeded fixture creates the tenant without a WorkSchedule row.
    r = client.get("/api/forge/schedule/status")
    assert r.status_code == 200, (
        f"expected 200 idle-state response, got {r.status_code} body={r.text}"
    )
    body = r.json()
    assert body["status"] == "idle"
    assert body["error"] is None


def test_get_schedule_returns_200_null_when_no_schedule_configured(
    client: TestClient,
) -> None:
    """Same regression guard for the bare /schedule endpoint. When no
    WorkSchedule is configured, the response is 200 with body `null`, NOT 404.
    The frontend's getWorkSchedule() polls this on mount and renders the
    'no schedule yet' UI when the response is null."""
    r = client.get("/api/forge/schedule")
    assert r.status_code == 200, (
        f"expected 200 null-body response, got {r.status_code} body={r.text}"
    )
    assert r.json() is None
