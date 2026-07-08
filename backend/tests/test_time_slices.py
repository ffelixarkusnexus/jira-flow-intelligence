from datetime import datetime
from itertools import pairwise

from app.db.models import Issue, Transition
from app.services.slicing_service import build_time_slices


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _make_issue(**overrides) -> Issue:
    base = {
        "tenant_id": "t1",
        "id": "X",
        "key": "X-1",
        "created_at": _dt("2026-01-01T10:00:00Z"),
        "updated_at": _dt("2026-01-01T14:00:00Z"),
        "current_status": "Done",
    }
    base.update(overrides)
    return Issue(**base)


def _make_transition(**overrides) -> Transition:
    base = {"tenant_id": "t1", "issue_id": "X"}
    base.update(overrides)
    return Transition(**base)


def test_canonical_doc_example_in_progress_review_done():
    """From 10_CLAUDE_CODE_MASTER/05_data_processing_logic.md validation:
    created 10:00, Review at 12:00, Done at 14:00 -> 2h In Progress, 2h Review."""
    issue = _make_issue(done_at=_dt("2026-01-01T14:00:00Z"))
    transitions = [
        _make_transition(
            id=1,
            from_status="In Progress",
            to_status="Review",
            transitioned_at=_dt("2026-01-01T12:00:00Z"),
        ),
        _make_transition(
            id=2,
            from_status="Review",
            to_status="Done",
            transitioned_at=_dt("2026-01-01T14:00:00Z"),
        ),
    ]
    slices = build_time_slices(issue, transitions, done_statuses=["Done"])
    durations = {s.status: s.duration_seconds for s in slices if s.status != "Done"}
    assert durations["In Progress"] == 7200
    assert durations["Review"] == 7200
    assert all(s.tenant_id == "t1" for s in slices)


def test_no_transitions_uses_current_status_until_now():
    now = _dt("2026-01-02T10:00:00Z")
    issue = _make_issue(current_status="Todo", done_at=None)
    slices = build_time_slices(issue, [], now=now, done_statuses=["Done"])
    assert len(slices) == 1
    assert slices[0].status == "Todo"
    assert slices[0].duration_seconds == 86400
    assert slices[0].is_open is True


def test_open_issue_final_slice_extends_to_now():
    now = _dt("2026-01-02T00:00:00Z")
    issue = _make_issue(
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-01T06:00:00Z"),
        current_status="In Progress",
        done_at=None,
    )
    transitions = [
        _make_transition(
            id=1,
            from_status="Todo",
            to_status="In Progress",
            transitioned_at=_dt("2026-01-01T06:00:00Z"),
        )
    ]
    slices = build_time_slices(issue, transitions, now=now, done_statuses=["Done"])
    assert len(slices) == 2
    assert slices[0].status == "Todo"
    assert slices[0].duration_seconds == 6 * 3600
    assert slices[1].status == "In Progress"
    assert slices[1].duration_seconds == 18 * 3600
    assert slices[1].is_open is True


def test_no_gaps_or_overlaps():
    now = _dt("2026-01-10T00:00:00Z")
    issue = _make_issue(
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=now,
        done_at=_dt("2026-01-05T00:00:00Z"),
    )
    transitions = [
        _make_transition(
            id=1,
            from_status="Todo",
            to_status="In Progress",
            transitioned_at=_dt("2026-01-02T00:00:00Z"),
        ),
        _make_transition(
            id=2,
            from_status="In Progress",
            to_status="Review",
            transitioned_at=_dt("2026-01-03T00:00:00Z"),
        ),
        _make_transition(
            id=3,
            from_status="Review",
            to_status="Done",
            transitioned_at=_dt("2026-01-05T00:00:00Z"),
        ),
    ]
    slices = build_time_slices(issue, transitions, now=now, done_statuses=["Done"])
    for prev, nxt in pairwise(slices):
        assert prev.end_at == nxt.start_at, "no gaps, no overlaps"
    total = sum(s.duration_seconds for s in slices)
    assert total == int((issue.done_at - issue.created_at).total_seconds())


def test_status_loops_treated_as_separate_segments():
    now = _dt("2026-01-10T00:00:00Z")
    issue = _make_issue(
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=now,
        done_at=_dt("2026-01-04T00:00:00Z"),
    )
    transitions = [
        _make_transition(
            id=1,
            from_status="In Progress",
            to_status="Review",
            transitioned_at=_dt("2026-01-02T00:00:00Z"),
        ),
        _make_transition(
            id=2,
            from_status="Review",
            to_status="In Progress",
            transitioned_at=_dt("2026-01-03T00:00:00Z"),
        ),
        _make_transition(
            id=3,
            from_status="In Progress",
            to_status="Done",
            transitioned_at=_dt("2026-01-04T00:00:00Z"),
        ),
    ]
    slices = build_time_slices(issue, transitions, now=now, done_statuses=["Done"])
    in_progress_slices = [s for s in slices if s.status == "In Progress"]
    assert len(in_progress_slices) == 2  # two distinct loop occurrences


def test_idempotency_same_input_same_output():
    now = _dt("2026-01-05T00:00:00Z")
    issue = _make_issue(
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=now,
        done_at=_dt("2026-01-04T00:00:00Z"),
    )
    transitions = [
        _make_transition(
            id=1,
            from_status="In Progress",
            to_status="Done",
            transitioned_at=_dt("2026-01-04T00:00:00Z"),
        ),
    ]
    a = build_time_slices(issue, transitions, now=now, done_statuses=["Done"])
    b = build_time_slices(issue, transitions, now=now, done_statuses=["Done"])
    assert [(s.status, s.start_at, s.end_at, s.duration_seconds) for s in a] == [
        (s.status, s.start_at, s.end_at, s.duration_seconds) for s in b
    ]
