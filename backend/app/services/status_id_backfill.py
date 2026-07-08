"""ADR-0046 — Path B retroactive backfill of legacy NULL `status_id` rows.

The companion to ADR-0045 (status-ID-aware aggregation across renames).

Background. ADR-0045 added nullable `status_id` columns to `transitions`
(from + to) and `time_slices`, and rewrote `discover_status_groups` as
a two-pass grouping that joins on ID when populated and falls back to
name when NULL. That ships *Path A* — new transitions get the fix
immediately. Legacy data persisted before the columns existed has
`status_id = NULL` and continues to exhibit the rename drift (the
documented claim *"status renames don't break aggregates"*
is partial-true under Path A alone).

This module closes the historical half: a one-time per-tenant pass
that fetches the current Jira status list, builds a `name -> id`
lookup, and updates every NULL row whose `name` matches.

Trigger choice. Per reviewer recommendation: per-tenant
manual trigger via a Settings UI button (option 1). ~2 tenants today;
manual is observable, debuggable, lower engineering surface. A future
paying-customer cycle can wire the same function to a lifecycle webhook
without API changes — the function takes `(session, tenant_id,
jira_status_lookup)` and returns a `BackfillResult`; the trigger is
upstream.

Idempotency. The WHERE clauses scope to `status_id IS NULL` only —
re-running after a partial backfill picks up only the still-NULL rows.
The function is safe to interrupt and resume.

Unresolved-name handling. A status renamed AND then deleted between
legacy write and backfill won't appear in the current statuses lookup
— those names are returned in `BackfillResult.unresolved_names` for
the tenant admin to inspect. They can opt to restore the status in
Jira and re-run, or accept that those slices remain orphaned under
the (now-stale) name in their dashboard.

Source-list source. The /rest/api/3/status endpoint returns
[{"id": "10042", "name": "Code Review", ...}, ...]. The resolver
fetches it via `api.asApp().requestJira(...)` and passes the array
to the backend endpoint — the backend doesn't make outbound Jira
calls directly (Connect-era auth path; Forge installs use the
resolver-side `requestJira`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import TimeSlice, Transition


@dataclass(frozen=True)
class BackfillResult:
    """Counts + diagnostics from one backfill invocation.

    `unresolved_names` lists distinct status names that appeared in
    NULL-id rows but were not present in the supplied `jira_status_lookup`.
    These are typically statuses that were renamed AND deleted between
    the legacy write and this backfill — current Jira state can't resolve
    them. Tenant admin can restore the status and re-run, or accept the
    orphan.
    """

    updated_transitions: int
    updated_slices: int
    unresolved_names: list[str]


# Batch size for the bulk UPDATE — chunk to keep memory bounded on
# large tenants (hundreds of thousands of slices). 1000 matches the
# recompute pipeline's BATCH_SIZE so dialect-specific UPDATE planning
# stays consistent.
_BATCH_SIZE = 1000


def _distinct_null_status_names(session: Session, tenant_id: str) -> set[str]:
    """Pull every distinct status name that appears on ANY legacy NULL-id
    row for the tenant — `transitions.to_status` (to_status_id NULL),
    `transitions.from_status` (from_status_id NULL), or `time_slices.status`
    (status_id NULL).

    All three column sources contribute because a name might appear in
    just one of them (e.g., "To Do" usually shows up as a from_status
    on the issue's first transition but never as a to_status — without
    it in this set, the from_status_id update never runs for that name).
    """
    to_rows = session.execute(
        Transition.__table__.select()
        .with_only_columns(Transition.to_status)
        .where(
            Transition.tenant_id == tenant_id,
            Transition.to_status_id.is_(None),
            Transition.to_status.is_not(None),
        )
        .distinct()
    ).all()
    from_rows = session.execute(
        Transition.__table__.select()
        .with_only_columns(Transition.from_status)
        .where(
            Transition.tenant_id == tenant_id,
            Transition.from_status_id.is_(None),
            Transition.from_status.is_not(None),
        )
        .distinct()
    ).all()
    slice_rows = session.execute(
        TimeSlice.__table__.select()
        .with_only_columns(TimeSlice.status)
        .where(
            TimeSlice.tenant_id == tenant_id,
            TimeSlice.status_id.is_(None),
            TimeSlice.status.is_not(None),
        )
        .distinct()
    ).all()
    return {row[0] for source in (to_rows, from_rows, slice_rows) for row in source if row[0]}


def backfill_legacy_status_ids(
    session: Session,
    tenant_id: str,
    jira_status_lookup: dict[str, str],
) -> BackfillResult:
    """Populate `status_id` on every legacy NULL-id row for the tenant.

    The lookup keys are case-sensitive Jira status names; values are the
    stable status IDs. Names not present in the lookup are surfaced in
    `BackfillResult.unresolved_names`.

    Updates three columns across two tables:
    - `transitions.from_status_id` where `from_status` matches the lookup
    - `transitions.to_status_id`   where `to_status`   matches the lookup
    - `time_slices.status_id`      where `status`      matches the lookup

    Idempotent — the WHERE clauses scope to NULL only, so re-running
    after a partial backfill picks up the still-NULL rows.
    """
    updated_transitions = 0
    updated_slices = 0
    unresolved: set[str] = set()

    all_names = _distinct_null_status_names(session, tenant_id)
    for name in all_names:
        if name not in jira_status_lookup:
            unresolved.add(name)

    # Per-name UPDATE: chunk the resolved names; each chunk runs three
    # statements (to_status_id, from_status_id, time_slices.status_id).
    # SQLite + Postgres both handle 1000-row WHERE-IN efficiently with
    # an index on (tenant_id, *_status); the existing
    # `ix_transitions_issue_ts` + `ix_slices_status_start` cover this.
    resolved_names = [n for n in all_names if n in jira_status_lookup]
    for i in range(0, len(resolved_names), _BATCH_SIZE):
        chunk = resolved_names[i : i + _BATCH_SIZE]
        for name in chunk:
            status_id = jira_status_lookup[name]
            r1 = session.execute(
                update(Transition)
                .where(
                    Transition.tenant_id == tenant_id,
                    Transition.to_status == name,
                    Transition.to_status_id.is_(None),
                )
                .values(to_status_id=status_id)
            )
            updated_transitions += r1.rowcount or 0  # type: ignore[attr-defined]

            r2 = session.execute(
                update(Transition)
                .where(
                    Transition.tenant_id == tenant_id,
                    Transition.from_status == name,
                    Transition.from_status_id.is_(None),
                )
                .values(from_status_id=status_id)
            )
            updated_transitions += r2.rowcount or 0  # type: ignore[attr-defined]

            r3 = session.execute(
                update(TimeSlice)
                .where(
                    TimeSlice.tenant_id == tenant_id,
                    TimeSlice.status == name,
                    TimeSlice.status_id.is_(None),
                )
                .values(status_id=status_id)
            )
            updated_slices += r3.rowcount or 0  # type: ignore[attr-defined]

    session.flush()
    # `update(...)` statements bypass the ORM identity map — any
    # Transition/TimeSlice already loaded into the session still
    # carries the pre-update column values. Expire so subsequent
    # reads pull the fresh state from the DB. Without this, the
    # caller's `.query().all()` returns stale objects.
    session.expire_all()
    return BackfillResult(
        updated_transitions=updated_transitions,
        updated_slices=updated_slices,
        unresolved_names=sorted(unresolved),
    )
