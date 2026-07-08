"""Tests for the sprint service. Covers ADR-0023 semantics."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.tenant_context import TenantContext
from app.db.models import Issue, IssueSprint, Sprint
from app.services.sprint_service import (
    get_active_sprint,
    list_sprints,
    set_issue_sprints,
    sprint_windows,
    upsert_sprint,
)


def _dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def test_upsert_sprint_inserts_then_updates(session, ctx: TenantContext) -> None:
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=42,
        name="Sprint 42",
        state="active",
        board_id=10,
        start_at=_dt(2026, 5, 1),
    )
    session.commit()
    rows = list_sprints(session, ctx.tenant_id)
    assert len(rows) == 1
    assert rows[0].name == "Sprint 42"

    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=42,
        name="Sprint 42 (renamed)",
        state="closed",
        board_id=10,
        start_at=_dt(2026, 5, 1),
        end_at=_dt(2026, 5, 14),
        complete_at=_dt(2026, 5, 14),
    )
    session.commit()
    rows = list_sprints(session, ctx.tenant_id)
    assert len(rows) == 1
    assert rows[0].name == "Sprint 42 (renamed)"
    assert rows[0].state == "closed"


def test_set_issue_sprints_replaces_membership(session, ctx: TenantContext) -> None:
    session.add(
        Issue(
            tenant_id=ctx.tenant_id,
            id="I1",
            key="A-1",
            created_at=_dt(2026, 5, 1),
            updated_at=_dt(2026, 5, 5),
        )
    )
    upsert_sprint(session, ctx.tenant_id, sprint_id=41, name="S41", state="closed", board_id=10)
    upsert_sprint(session, ctx.tenant_id, sprint_id=42, name="S42", state="active", board_id=10)
    upsert_sprint(session, ctx.tenant_id, sprint_id=43, name="S43", state="future", board_id=10)
    session.commit()

    set_issue_sprints(session, ctx.tenant_id, "I1", [41, 42])
    session.commit()
    members = {r.sprint_id for r in session.scalars(select(IssueSprint))}
    assert members == {41, 42}

    # Replace: drop 41, keep 42, add 43
    set_issue_sprints(session, ctx.tenant_id, "I1", [42, 43])
    session.commit()
    members = {r.sprint_id for r in session.scalars(select(IssueSprint))}
    assert members == {42, 43}


def test_get_active_sprint_picks_state_active(session, ctx: TenantContext) -> None:
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=41,
        name="S41",
        state="closed",
        board_id=10,
        start_at=_dt(2026, 4, 1),
    )
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=42,
        name="S42",
        state="active",
        board_id=10,
        start_at=_dt(2026, 5, 1),
    )
    session.commit()
    active = get_active_sprint(session, ctx.tenant_id, project_key=None)
    assert active is not None
    assert active.id == 42


def test_sprint_windows_returns_none_when_no_sprints(session, ctx: TenantContext) -> None:
    assert sprint_windows(session, ctx.tenant_id, project_key="VPST") is None


def test_sprint_windows_active_sprint_compares_against_last_closed(
    session, ctx: TenantContext
) -> None:
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=41,
        name="S41",
        state="closed",
        board_id=10,
        start_at=_dt(2026, 4, 1),
        end_at=_dt(2026, 4, 14),
        complete_at=_dt(2026, 4, 14),
    )
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=42,
        name="S42",
        state="active",
        board_id=10,
        start_at=_dt(2026, 5, 1),
    )
    session.commit()
    res = sprint_windows(session, ctx.tenant_id, project_key=None, now=_dt(2026, 5, 10))
    assert res is not None
    (cur_s, cur_e), (prev_s, prev_e) = res
    assert cur_s == _dt(2026, 5, 1)
    assert cur_e == _dt(2026, 5, 10)
    assert prev_s == _dt(2026, 4, 1)
    assert prev_e == _dt(2026, 4, 14)


def test_sprint_windows_no_active_uses_most_recent_closed(session, ctx: TenantContext) -> None:
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=41,
        name="S41",
        state="closed",
        board_id=10,
        start_at=_dt(2026, 4, 1),
        end_at=_dt(2026, 4, 14),
        complete_at=_dt(2026, 4, 14),
    )
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=40,
        name="S40",
        state="closed",
        board_id=10,
        start_at=_dt(2026, 3, 14),
        end_at=_dt(2026, 3, 28),
        complete_at=_dt(2026, 3, 28),
    )
    session.commit()
    res = sprint_windows(session, ctx.tenant_id, project_key=None, now=_dt(2026, 4, 20))
    assert res is not None
    (cur_s, cur_e), (prev_s, _prev_e) = res
    assert cur_s == _dt(2026, 4, 1)
    assert cur_e == _dt(2026, 4, 14)
    assert prev_s == _dt(2026, 3, 14)


def test_sprint_windows_explicit_id_uses_that_sprint(session, ctx: TenantContext) -> None:
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=41,
        name="S41",
        state="closed",
        board_id=10,
        start_at=_dt(2026, 4, 1),
        end_at=_dt(2026, 4, 14),
        complete_at=_dt(2026, 4, 14),
    )
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=42,
        name="S42",
        state="active",
        board_id=10,
        start_at=_dt(2026, 5, 1),
    )
    session.commit()
    # Explicitly request S41 — current = S41 bounds, previous = empty (no S40).
    res = sprint_windows(
        session, ctx.tenant_id, project_key=None, sprint_id=41, now=_dt(2026, 5, 10)
    )
    assert res is not None
    (cur_s, cur_e), _ = res
    assert cur_s == _dt(2026, 4, 1)
    assert cur_e == _dt(2026, 4, 14)


def test_sprint_windows_project_scoped(session, ctx: TenantContext) -> None:
    """Sprint windows for one project don't see another project's sprints."""
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=41,
        name="S41 VPST",
        state="active",
        board_id=10,
        project_key="VPST",
        start_at=_dt(2026, 5, 1),
    )
    upsert_sprint(
        session,
        ctx.tenant_id,
        sprint_id=99,
        name="S99 OTHER",
        state="active",
        board_id=11,
        project_key="OTHER",
        start_at=_dt(2026, 5, 1),
    )
    session.commit()
    res_vpst = sprint_windows(session, ctx.tenant_id, project_key="VPST")
    assert res_vpst is not None
    res_other = sprint_windows(session, ctx.tenant_id, project_key="OTHER")
    assert res_other is not None
    res_unknown = sprint_windows(session, ctx.tenant_id, project_key="DOESNT_EXIST")
    assert res_unknown is None


def test_sprint_ingested_from_issue_payload(session, ctx: TenantContext) -> None:
    """Path 3 ingestion: sprint metadata + membership read straight from
    the issue payload's Sprint custom field. Verifies the default
    customfield_10020 is recognized and the union-merge works across two
    issues sharing a sprint."""
    from app.services.ingestion_service import process_payloads

    payload_a = {
        "id": "10001",
        "key": "VPST-1",
        "fields": {
            "created": "2026-04-01T00:00:00Z",
            "updated": "2026-05-05T00:00:00Z",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "project": {"key": "VPST"},
            "summary": "first",
            "customfield_10020": [
                {
                    "id": 42,
                    "name": "Sprint 42",
                    "state": "active",
                    "boardId": 10,
                    "startDate": "2026-05-01T00:00:00Z",
                    "endDate": "2026-05-14T00:00:00Z",
                }
            ],
        },
        "changelog": {"histories": []},
    }
    payload_b = {
        "id": "10002",
        "key": "VPST-2",
        "fields": {
            "created": "2026-04-01T00:00:00Z",
            "updated": "2026-05-05T00:00:00Z",
            "status": {"name": "Review"},
            "issuetype": {"name": "Bug"},
            "project": {"key": "VPST"},
            "summary": "second",
            "customfield_10020": [
                {
                    "id": 41,
                    "name": "Sprint 41",
                    "state": "closed",
                    "boardId": 10,
                    "startDate": "2026-04-15T00:00:00Z",
                    "endDate": "2026-04-30T00:00:00Z",
                    "completeDate": "2026-04-30T18:00:00Z",
                },
                {
                    "id": 42,
                    "name": "Sprint 42",
                    "state": "active",
                    "boardId": 10,
                    "startDate": "2026-05-01T00:00:00Z",
                    "endDate": "2026-05-14T00:00:00Z",
                },
            ],
        },
        "changelog": {"histories": []},
    }
    process_payloads(session, [payload_a, payload_b], ctx)
    session.commit()

    sprints = list_sprints(session, ctx.tenant_id, project_key="VPST")
    assert {s.id for s in sprints} == {41, 42}
    assert next(s for s in sprints if s.id == 42).state == "active"
    assert next(s for s in sprints if s.id == 41).state == "closed"

    members = {(r.issue_id, r.sprint_id) for r in session.scalars(select(IssueSprint))}
    assert members == {("10001", 42), ("10002", 41), ("10002", 42)}


def test_sprint_field_heuristic_detects_non_standard_id(session, ctx: TenantContext) -> None:
    """example-tenant uses customfield_10007 for Sprint — outside our static
    fallback list. The heuristic probe should find it by shape match
    (list of dicts with int id + str state)."""
    from app.services.ingestion_service import process_payloads

    payload = {
        "id": "30001",
        "key": "RM-1",
        "fields": {
            "created": "2026-04-01T00:00:00Z",
            "updated": "2026-05-05T00:00:00Z",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "project": {"key": "RM"},
            "summary": "non-standard sprint field id",
            # Site uses customfield_10007, not in any static fallback.
            "customfield_10007": [
                {
                    "id": 99,
                    "name": "Sprint 99",
                    "state": "active",
                    "boardId": 5,
                    "startDate": "2026-05-01T00:00:00Z",
                }
            ],
        },
        "changelog": {"histories": []},
    }
    process_payloads(session, [payload], ctx)
    session.commit()
    sprints = list_sprints(session, ctx.tenant_id, project_key="RM")
    assert {s.id for s in sprints} == {99}


def test_sprint_field_heuristic_does_not_match_lookalike_fields(
    session, ctx: TenantContext
) -> None:
    """Components / Versions / Labels are arrays of dicts too. The heuristic
    must not false-positive on them — Sprint is unique in carrying both
    int `id` and str `state`."""
    from app.core.tenant_context import TenantContext as TCtx
    from app.services.ingestion_service import _extract_sprints

    fields = {
        # Components — has id+name but no state.
        "components": [{"id": 1, "name": "auth"}],
        # Fix versions — has id+name+released:bool, no state.
        "fixVersions": [{"id": 2, "name": "v1.0", "released": True}],
        # Custom field with id+state strings (not int) — also rejected.
        "customfield_99999": [{"id": "abc", "state": "active"}],
        # And one well-formed Sprint.
        "customfield_10007": [{"id": 99, "name": "S99", "state": "active"}],
    }
    fake_ctx = TCtx(tenant=ctx.tenant, settings=ctx.settings)
    result = _extract_sprints(fields, fake_ctx)
    assert len(result) == 1
    assert result[0]["id"] == 99


def test_sprint_membership_clears_when_issue_removed_from_sprint(
    session, ctx: TenantContext
) -> None:
    """Re-syncing an issue with no sprint field drops its membership."""
    from app.services.ingestion_service import process_payloads

    with_sprint = {
        "id": "10001",
        "key": "VPST-1",
        "fields": {
            "created": "2026-04-01T00:00:00Z",
            "updated": "2026-05-05T00:00:00Z",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "project": {"key": "VPST"},
            "summary": "first",
            "customfield_10020": [
                {"id": 42, "name": "Sprint 42", "state": "active", "boardId": 10}
            ],
        },
        "changelog": {"histories": []},
    }
    process_payloads(session, [with_sprint], ctx)
    session.commit()
    assert session.scalar(select(IssueSprint).where(IssueSprint.issue_id == "10001"))

    # Re-sync with sprint field cleared
    no_sprint = dict(with_sprint)
    no_sprint["fields"] = {**with_sprint["fields"], "customfield_10020": None}
    process_payloads(session, [no_sprint], ctx)
    session.commit()
    assert session.scalar(select(IssueSprint).where(IssueSprint.issue_id == "10001")) is None


def test_sprint_drop_cascades_membership(session, ctx: TenantContext) -> None:
    """Deleting a sprint drops issue_sprints rows for it via FK CASCADE."""
    session.add(
        Issue(
            tenant_id=ctx.tenant_id,
            id="I1",
            key="A-1",
            created_at=_dt(2026, 5, 1),
            updated_at=_dt(2026, 5, 5),
        )
    )
    upsert_sprint(session, ctx.tenant_id, sprint_id=42, name="S42", state="active", board_id=10)
    session.commit()
    set_issue_sprints(session, ctx.tenant_id, "I1", [42])
    session.commit()
    sprint = session.get(Sprint, (ctx.tenant_id, 42))
    session.delete(sprint)
    session.commit()
    members = list(session.scalars(IssueSprint.__table__.select()))
    assert members == []
