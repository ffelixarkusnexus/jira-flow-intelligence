"""Coverage for ingestion service edge cases (done_at resolution, error handling)."""

from __future__ import annotations

from app.core.tenant_context import TenantContext
from app.services.ingestion_service import (
    SyncReport,
    _resolve_done_at,
    process_payloads,
)
from app.services.transition_service import load_transitions


def test_resolve_done_at_prefers_resolutiondate():
    payload = {
        "fields": {
            "resolutiondate": "2026-01-05T12:00:00Z",
            "status": {"name": "Done"},
        },
    }
    result = _resolve_done_at(payload, {"Done"})
    assert result is not None
    assert result.year == 2026
    assert result.day == 5


def test_resolve_done_at_walks_changelog_when_no_resolutiondate():
    payload = {
        "fields": {"resolutiondate": None, "status": {"name": "Done"}},
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-03T00:00:00Z",
                    "items": [{"field": "status", "fromString": "Review", "toString": "Done"}],
                },
                {
                    "created": "2026-01-04T00:00:00Z",
                    "items": [{"field": "status", "fromString": "Done", "toString": "Reopen"}],
                },
                {
                    "created": "2026-01-05T00:00:00Z",
                    "items": [{"field": "status", "fromString": "Reopen", "toString": "Done"}],
                },
            ]
        },
    }
    result = _resolve_done_at(payload, {"Done"})
    assert result is not None
    assert result.day == 5


def test_resolve_done_at_returns_none_when_not_in_done_status():
    payload = {
        "fields": {"resolutiondate": None, "status": {"name": "Review"}},
        "changelog": {"histories": []},
    }
    assert _resolve_done_at(payload, {"Done"}) is None


def test_process_payloads_records_errors_and_continues(session, ctx: TenantContext):
    bad = {"id": "BAD-1", "key": "BAD-1", "fields": None}  # malformed
    good = {
        "id": "GOOD-1",
        "key": "GOOD-1",
        "fields": {
            "created": "2026-01-01T10:00:00Z",
            "updated": "2026-01-01T14:00:00Z",
            "status": {"name": "Done"},
            "issuetype": {"name": "Story"},
            "project": {"key": "ABC"},
            "summary": "ok",
            "resolutiondate": "2026-01-01T14:00:00Z",
        },
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T14:00:00Z",
                    "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
                }
            ]
        },
    }
    report = process_payloads(session, [bad, good], ctx)
    session.commit()
    assert report.issues_processed == 1
    assert len(report.errors) == 1
    assert "BAD-1" in report.errors[0]


def test_load_transitions_returns_in_order(session, ctx: TenantContext):
    payload = {
        "id": "X",
        "key": "X-1",
        "fields": {
            "created": "2026-01-01T10:00:00Z",
            "updated": "2026-01-01T14:00:00Z",
            "status": {"name": "Done"},
            "issuetype": {"name": "Story"},
            "project": {"key": "ABC"},
            "summary": "x",
            "resolutiondate": "2026-01-01T14:00:00Z",
        },
        "changelog": {
            "histories": [
                {
                    "created": "2026-01-01T13:00:00Z",
                    "items": [{"field": "status", "fromString": "Review", "toString": "Done"}],
                },
                {
                    "created": "2026-01-01T12:00:00Z",
                    "items": [
                        {"field": "status", "fromString": "In Progress", "toString": "Review"}
                    ],
                },
            ]
        },
    }
    process_payloads(session, [payload], ctx)
    session.commit()
    rows = load_transitions(session, ctx.tenant_id, "X")
    timestamps = [r.transitioned_at for r in rows]
    assert timestamps == sorted(timestamps)


def test_sync_report_default_errors_is_empty_list():
    report = SyncReport()
    assert report.errors == []
    report.errors.append("oops")
    assert SyncReport().errors == []  # not shared via mutable default
