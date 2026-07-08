"""ADR-0045 — status-ID-aware aggregation across renames.

Locks in the documented property ("status renames don't
break aggregates"). Each test exercises one slice of the contract:

- ingestion reads `from`/`to` ids from the Jira changelog payload
- slicing propagates `to_status_id` to the emitted slice
- `discover_status_groups` collapses renamed name variants under one
  display group keyed by `status_id`
- legacy NULL-status_id rows continue to work via name-based fallback
  (Path A mixed-mode)
- the rename property holds end-to-end: an aggregate query for the
  current name returns slices that USED to live under the pre-rename
  name, not orphaned in a separate group.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.tenant_context import TenantContext
from app.db.models import Issue, TimeSlice, Transition
from app.services.metrics_service import (
    StatusWindowResult,
    _compute_status_window_for_variants,
    discover_status_groups,
)
from app.services.slicing_service import build_time_slices, replace_time_slices
from app.services.transition_service import (
    extract_transitions_from_payload,
    replace_transitions,
)
from tests.conftest import make_tenant

FIXED_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


# --- Ingestion / parsing ----------------------------------------------------


def test_extract_transitions_reads_status_ids_from_changelog() -> None:
    """The Jira changelog item shape carries `from` and `to` as status IDs."""
    payload = {
        "id": "10001",
        "key": "DEMO-1",
        "changelog": {
            "histories": [
                {
                    "created": "2026-05-08T16:00:00Z",
                    "items": [
                        {
                            "field": "status",
                            "fromString": "To Do",
                            "toString": "In Progress",
                            "from": "10000",
                            "to": "10001",
                        },
                    ],
                },
            ],
        },
    }
    parsed = extract_transitions_from_payload("t1", "10001", payload)
    assert len(parsed) == 1
    assert parsed[0].from_status == "To Do"
    assert parsed[0].to_status == "In Progress"
    assert parsed[0].from_status_id == "10000"
    assert parsed[0].to_status_id == "10001"


def test_extract_transitions_handles_missing_status_ids() -> None:
    """Legacy or sparse payloads that omit `from`/`to` get NULL ids — both
    the new column persistence AND the test for that path."""
    payload = {
        "id": "10002",
        "key": "DEMO-OLD",
        "changelog": {
            "histories": [
                {
                    "created": "2025-12-01T09:00:00Z",
                    "items": [
                        {
                            "field": "status",
                            "fromString": "To Do",
                            "toString": "In Progress",
                            # No `from`/`to` — pre-fix or sparse payload.
                        },
                    ],
                },
            ],
        },
    }
    parsed = extract_transitions_from_payload("t1", "10002", payload)
    assert parsed[0].from_status_id is None
    assert parsed[0].to_status_id is None


def test_extract_transitions_coerces_int_status_ids_to_str() -> None:
    """Jira sometimes returns the status id as a JSON int, sometimes string.
    Persist as str so downstream IN-clauses are stable."""
    payload = {
        "id": "10003",
        "key": "DEMO-INT",
        "changelog": {
            "histories": [
                {
                    "created": "2026-05-08T16:00:00Z",
                    "items": [
                        {
                            "field": "status",
                            "fromString": "To Do",
                            "toString": "In Progress",
                            "from": 10000,
                            "to": 10001,
                        },
                    ],
                },
            ],
        },
    }
    parsed = extract_transitions_from_payload("t1", "10003", payload)
    assert parsed[0].from_status_id == "10000"
    assert parsed[0].to_status_id == "10001"


# --- Slice construction -----------------------------------------------------


def test_build_time_slices_propagates_status_id(session: Session) -> None:
    tenant = make_tenant(session, client_key="rename-test")
    issue = Issue(
        tenant_id=tenant.client_key,
        id="10100",
        key="DEMO-100",
        project_key="DEMO",
        current_status="Code Review",
        created_at=FIXED_NOW - timedelta(days=10),
        updated_at=FIXED_NOW,
    )
    session.add(issue)
    session.flush()

    t1 = Transition(
        tenant_id=tenant.client_key,
        issue_id=issue.id,
        from_status="To Do",
        to_status="In Progress",
        from_status_id="10000",
        to_status_id="10001",
        transitioned_at=FIXED_NOW - timedelta(days=8),
    )
    t2 = Transition(
        tenant_id=tenant.client_key,
        issue_id=issue.id,
        from_status="In Progress",
        to_status="Code Review",
        from_status_id="10001",
        to_status_id="10042",
        transitioned_at=FIXED_NOW - timedelta(days=2),
    )
    session.add_all([t1, t2])
    session.flush()

    slices = build_time_slices(issue, [t1, t2], now=FIXED_NOW)

    # Initial slice (To Do) gets t1's from_status_id; subsequent slices
    # carry the from-id of the transition that started them.
    assert slices[0].status == "To Do"
    assert slices[0].status_id == "10000"
    assert slices[1].status == "In Progress"
    assert slices[1].status_id == "10001"
    assert slices[2].status == "Code Review"
    assert slices[2].status_id == "10042"


def test_build_time_slices_legacy_transitions_have_null_status_id(
    session: Session,
) -> None:
    """Transitions persisted before ADR-0045 (no id columns) keep NULL ids;
    slices emitted from them carry NULL too."""
    tenant = make_tenant(session, client_key="rename-test")
    issue = Issue(
        tenant_id=tenant.client_key,
        id="10200",
        key="LEGACY-1",
        project_key="DEMO",
        current_status="In Progress",
        created_at=FIXED_NOW - timedelta(days=10),
        updated_at=FIXED_NOW,
    )
    session.add(issue)
    session.flush()

    t1 = Transition(
        tenant_id=tenant.client_key,
        issue_id=issue.id,
        from_status="To Do",
        to_status="In Progress",
        # No status_ids — legacy row.
        from_status_id=None,
        to_status_id=None,
        transitioned_at=FIXED_NOW - timedelta(days=5),
    )
    session.add(t1)
    session.flush()

    slices = build_time_slices(issue, [t1], now=FIXED_NOW)
    for s in slices:
        assert s.status_id is None


# --- Aggregation: the headline property -------------------------------------


def _persist_slice(
    session: Session,
    tenant_id: str,
    issue_id: str,
    status: str,
    status_id: str | None,
    start: datetime,
    end: datetime,
) -> None:
    """Minimal slice-row writer for aggregation tests."""
    session.add(
        TimeSlice(
            tenant_id=tenant_id,
            issue_id=issue_id,
            status=status,
            status_id=status_id,
            start_at=start,
            end_at=end,
            duration_seconds=int((end - start).total_seconds()),
            is_open=False,
        )
    )


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


def test_discover_status_groups_collapses_renamed_names_under_status_id(
    session: Session,
) -> None:
    """The headline test for the documented claim. A status renamed in Jira
    (same status_id, different name across time) must show up as ONE group
    in `discover_status_groups`, with the current name as display."""
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10300")

    # Pre-rename slice (older end_at).
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="In Review",
        status_id="10042",
        start=FIXED_NOW - timedelta(days=80),
        end=FIXED_NOW - timedelta(days=75),
    )
    # Post-rename slice (newer end_at) — same status_id, new name.
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="Code Review",
        status_id="10042",
        start=FIXED_NOW - timedelta(days=10),
        end=FIXED_NOW - timedelta(days=8),
    )
    session.flush()

    groups = discover_status_groups(session, tenant.client_key)
    code_review_group = next((g for g in groups if g[0] == "Code Review"), None)
    assert code_review_group is not None, (
        f"expected one group named 'Code Review' (the current name); got {groups}"
    )
    display, variants = code_review_group
    assert display == "Code Review"
    # Both name variants must be in `variants` so the downstream
    # `WHERE status IN (variants)` query picks up both eras of slices.
    assert set(variants) == {"In Review", "Code Review"}
    # No separate orphan group under the pre-rename name.
    assert all(g[0] != "In Review" for g in groups)


def test_aggregate_query_returns_both_eras_under_renamed_status_id(
    session: Session,
) -> None:
    """End-to-end: the specific documented claim. A `WHERE status IN
    (variants)` query for the renamed status returns slices from BOTH the
    pre-rename and post-rename periods. This is what the dashboard sees."""
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10301")

    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="In Review",
        status_id="10042",
        start=FIXED_NOW - timedelta(days=80),
        end=FIXED_NOW - timedelta(days=77),  # 3-day slice
    )
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="Code Review",
        status_id="10042",
        start=FIXED_NOW - timedelta(days=10),
        end=FIXED_NOW - timedelta(days=8),  # 2-day slice
    )
    session.flush()

    # Query the window that includes both eras.
    result: StatusWindowResult = _compute_status_window_for_variants(
        session,
        tenant.client_key,
        display_name="Code Review",
        variants=["In Review", "Code Review"],
        window_start=FIXED_NOW - timedelta(days=120),
        window_end=FIXED_NOW,
    )

    # Sample size counts BOTH closed slices — the pre-rename "In Review"
    # ticket's 3 days and the post-rename "Code Review" ticket's 2 days
    # both end inside the window.
    assert result.sample_size == 2, (
        "expected pre-rename + post-rename slices to both appear in the "
        "Code Review aggregate; got sample_size != 2"
    )


def test_discover_status_groups_falls_back_to_name_for_legacy_null_id_rows(
    session: Session,
) -> None:
    """Path A — mixed-mode aggregation. Legacy rows with NULL status_id fall
    through to the pre-ADR-0045 case-folded grouping (no regression for
    tenants whose data was written before the column existed)."""
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10400")

    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="In Review",
        status_id=None,
        start=FIXED_NOW - timedelta(days=80),
        end=FIXED_NOW - timedelta(days=75),
    )
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="IN REVIEW",  # case variant — pre-fix tenant casefold path.
        status_id=None,
        start=FIXED_NOW - timedelta(days=20),
        end=FIXED_NOW - timedelta(days=18),
    )
    session.flush()

    groups = discover_status_groups(session, tenant.client_key)
    in_review_group = next(
        (g for g in groups if g[0].casefold() == "in review"),
        None,
    )
    assert in_review_group is not None
    _, variants = in_review_group
    # Both case variants under one group (legacy casefold behavior preserved).
    assert set(variants) == {"In Review", "IN REVIEW"}


def test_discover_status_groups_legacy_orphaned_from_renamed_id_group(
    session: Session,
) -> None:
    """The bound the legacy data sets: a tenant whose pre-fix history has
    NULL-id `In Review` rows AND post-fix `Code Review` rows under id 10042
    sees TWO groups — the orphaned legacy "In Review" name group and the
    new ID-keyed "Code Review" group. Path B (historical backfill) is what
    merges them; this test pins the Path A boundary."""
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10500")

    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="In Review",
        status_id=None,  # legacy
        start=FIXED_NOW - timedelta(days=80),
        end=FIXED_NOW - timedelta(days=75),
    )
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="Code Review",
        status_id="10042",  # post-fix
        start=FIXED_NOW - timedelta(days=10),
        end=FIXED_NOW - timedelta(days=8),
    )
    session.flush()

    groups = discover_status_groups(session, tenant.client_key)
    group_names = {g[0] for g in groups}
    # Two distinct groups — this IS the limitation Path B closes.
    assert "Code Review" in group_names
    assert "In Review" in group_names


def test_discover_status_groups_merges_same_name_across_id_and_null(
    session: Session,
) -> None:
    """When a tenant has NULL-id rows under name X and id-populated rows
    under the SAME name X, the NULL rows merge into the id-keyed group via
    the downstream `WHERE status IN (variants)` filter — the test verifies
    discover_status_groups doesn't create a duplicate group for the name."""
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10600")

    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="Code Review",
        status_id=None,  # legacy
        start=FIXED_NOW - timedelta(days=80),
        end=FIXED_NOW - timedelta(days=75),
    )
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="Code Review",
        status_id="10042",  # post-fix
        start=FIXED_NOW - timedelta(days=10),
        end=FIXED_NOW - timedelta(days=8),
    )
    session.flush()

    groups = discover_status_groups(session, tenant.client_key)
    code_review_groups = [g for g in groups if g[0] == "Code Review"]
    assert len(code_review_groups) == 1, (
        "name 'Code Review' appears in both id-keyed and NULL-id rows — "
        "discover_status_groups should produce exactly ONE group, not two"
    )


# --- Round-trip via persistence layer ---------------------------------------


def test_replace_transitions_persists_status_ids(session: Session) -> None:
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10700")
    parsed = extract_transitions_from_payload(
        tenant.client_key,
        issue.id,
        {
            "id": issue.id,
            "key": issue.key,
            "changelog": {
                "histories": [
                    {
                        "created": "2026-05-08T16:00:00Z",
                        "items": [
                            {
                                "field": "status",
                                "fromString": "To Do",
                                "toString": "In Progress",
                                "from": "10000",
                                "to": "10001",
                            },
                        ],
                    }
                ],
            },
        },
    )
    replace_transitions(session, tenant.client_key, issue.id, parsed)
    session.flush()

    row = session.query(Transition).filter(Transition.issue_id == issue.id).one()
    assert row.from_status_id == "10000"
    assert row.to_status_id == "10001"


def test_replace_time_slices_persists_status_ids(session: Session) -> None:
    tenant = make_tenant(session, client_key="rename-test")
    issue = _make_issue(session, tenant.client_key, "10800")
    t = Transition(
        tenant_id=tenant.client_key,
        issue_id=issue.id,
        from_status="To Do",
        to_status="In Progress",
        from_status_id="10000",
        to_status_id="10001",
        transitioned_at=FIXED_NOW - timedelta(days=5),
    )
    session.add(t)
    session.flush()

    slices = build_time_slices(issue, [t], now=FIXED_NOW)
    replace_time_slices(session, tenant.client_key, issue.id, slices)
    session.flush()

    rows = (
        session.query(TimeSlice)
        .filter(TimeSlice.issue_id == issue.id)
        .order_by(TimeSlice.start_at)
        .all()
    )
    assert rows[0].status_id == "10000"
    assert rows[1].status_id == "10001"


def test_recompute_pipeline_handles_rename_end_to_end(session: Session) -> None:
    """The headline property end-to-end, exercising parse → persist →
    aggregate. Simulates: ticket transits 'In Review' (status_id 10042),
    later the workflow rename happens, a new ticket transits 'Code Review'
    (same id 10042). Aggregating 'Code Review' returns BOTH tickets'
    review time."""
    tenant = make_tenant(session, client_key="rename-test")
    settings = Settings()

    # Pre-rename ticket: In Review (10042)
    pre = _make_issue(session, tenant.client_key, "11001")
    pre_t1 = Transition(
        tenant_id=tenant.client_key,
        issue_id=pre.id,
        from_status="To Do",
        to_status="In Review",
        from_status_id="10000",
        to_status_id="10042",
        transitioned_at=FIXED_NOW - timedelta(days=85),
    )
    pre_t2 = Transition(
        tenant_id=tenant.client_key,
        issue_id=pre.id,
        from_status="In Review",
        to_status="Done",
        from_status_id="10042",
        to_status_id="20000",
        transitioned_at=FIXED_NOW - timedelta(days=80),
    )
    session.add_all([pre_t1, pre_t2])
    pre.done_at = pre_t2.transitioned_at
    session.flush()

    # Post-rename ticket: Code Review (same id 10042)
    post = _make_issue(session, tenant.client_key, "11002")
    post.created_at = FIXED_NOW - timedelta(days=20)
    post_t1 = Transition(
        tenant_id=tenant.client_key,
        issue_id=post.id,
        from_status="To Do",
        to_status="Code Review",
        from_status_id="10000",
        to_status_id="10042",
        transitioned_at=FIXED_NOW - timedelta(days=15),
    )
    post_t2 = Transition(
        tenant_id=tenant.client_key,
        issue_id=post.id,
        from_status="Code Review",
        to_status="Done",
        from_status_id="10042",
        to_status_id="20000",
        transitioned_at=FIXED_NOW - timedelta(days=10),
    )
    session.add_all([post_t1, post_t2])
    post.done_at = post_t2.transitioned_at
    session.flush()

    # Materialize slices.
    ctx = TenantContext(tenant=tenant, settings=settings)
    from app.services.slicing_service import recompute_slices_for_issue

    recompute_slices_for_issue(session, pre, ctx, now=FIXED_NOW)
    recompute_slices_for_issue(session, post, ctx, now=FIXED_NOW)
    session.flush()

    # Aggregate: discover_status_groups should return ONE group for status_id
    # 10042, with display "Code Review" (the more recent name).
    groups = discover_status_groups(session, tenant.client_key)
    review_group = next(g for g in groups if set(g[1]) == {"In Review", "Code Review"})
    assert review_group[0] == "Code Review"

    # Query the renamed status for the full 90-day window: BOTH tickets'
    # review time appears.
    result = _compute_status_window_for_variants(
        session,
        tenant.client_key,
        display_name="Code Review",
        variants=review_group[1],
        window_start=FIXED_NOW - timedelta(days=120),
        window_end=FIXED_NOW,
    )
    assert result.sample_size == 2, (
        f"expected 2 closed review slices (pre + post rename); got "
        f"sample_size={result.sample_size}. This is the documented claim: "
        f"'status renames don't break aggregates'."
    )


# --- Regression guards ------------------------------------------------------


def test_legacy_only_tenant_aggregate_unchanged_by_adr_0045(session: Session) -> None:
    """Regression guard: a tenant whose entire dataset has NULL status_id
    sees IDENTICAL behavior to pre-ADR-0045. discover_status_groups
    case-folds same as before; the aggregate sample-size math is unchanged."""
    tenant = make_tenant(session, client_key="legacy-only")
    issue = _make_issue(session, tenant.client_key, "12000")

    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="Review",
        status_id=None,
        start=FIXED_NOW - timedelta(days=10),
        end=FIXED_NOW - timedelta(days=8),
    )
    _persist_slice(
        session,
        tenant.client_key,
        issue.id,
        status="REVIEW",  # case variant
        status_id=None,
        start=FIXED_NOW - timedelta(days=5),
        end=FIXED_NOW - timedelta(days=4),
    )
    session.flush()

    groups = discover_status_groups(session, tenant.client_key)
    review_groups = [g for g in groups if g[0].casefold() == "review"]
    assert len(review_groups) == 1
    assert set(review_groups[0][1]) == {"Review", "REVIEW"}


@pytest.mark.parametrize(
    "history_items",
    [
        # No status item at all.
        [{"field": "assignee", "fromString": "alice", "toString": "bob"}],
        # Status item missing from/to ids — defensive.
        [{"field": "status", "fromString": "A", "toString": "B"}],
    ],
)
def test_extract_transitions_robust_to_partial_changelog_items(
    history_items: list[dict],
) -> None:
    """No crash, no errant status_id population for unusual payload shapes."""
    parsed = extract_transitions_from_payload(
        "t1",
        "10999",
        {
            "id": "10999",
            "key": "K",
            "changelog": {
                "histories": [{"created": "2026-05-08T16:00:00Z", "items": history_items}],
            },
        },
    )
    for p in parsed:
        # If anything was parsed, ids must be NULL (no `from`/`to` in input).
        assert p.from_status_id is None
        assert p.to_status_id is None
