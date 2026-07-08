# 0046 — Status ID historical backfill (Path B)

- Status: Accepted
- Date: 2026-06-07
- Deciders: maintainer, reviewer, Claude Code

## Context

[ADR-0045](0045-status-id-aggregation-across-renames.md) added stable Jira status IDs to `transitions` and `time_slices` and rewrote `discover_status_groups` to join on ID with name fallback. That ships **Path A** — new transitions get the rename-aggregation property immediately.

Legacy data persisted before the columns existed has `status_id = NULL`. Under Path A's mixed-mode aggregation, NULL-id rows fall through the pre-ADR-0045 case-folded name grouping — they continue to exhibit the rename drift (a status rename splits that status's history into two disconnected name groups) that ID-keyed aggregation is meant to eliminate.

A tenant whose backfill ran before the id-keying fix shipped, then later renames a workflow status, will see TWO display groups in the time-in-status chart — the orphaned pre-rename name (legacy NULL rows) and the new post-rename name (post-fix ID-keyed rows). The rename-drift fix is only partial for them under Path A alone.

Engineering principle: a known-wrong customer-facing behavior isn't deferred — it moves to next-available-slot priority. The partial fix leaves a truth-debt (legacy NULL-id rows still drift across renames); Path B closes it.

## Decision

A one-time per-tenant backfill that fetches the tenant's current Jira status list, builds a `name → id` lookup, and populates `status_id` on every legacy NULL row whose `name` matches.

### Where the Jira call happens

**Forge resolver side, not backend.** The new `backfillStatusIds` resolver in `forge-prod/src/resolvers/dashboard.ts` calls `api.asApp().requestJira(route\`/rest/api/3/status\`)`, forwards the JSON to a new backend endpoint `POST /api/forge/backfill/status-ids`, and renders the response.

Why resolver-side:

- Forge installs use Forge OAuth scopes, not the Connect-era email/token auth the backend's `JiraClient` is wired for. `api.asApp().requestJira(...)` is the supported pattern; adding Jira-auth machinery to the backend for this one use case would be wasted surface.
- One outbound Jira call per button click; pagination not needed (the `/rest/api/3/status` endpoint returns the full list).
- `asApp()` over `asUser()` — the backfill affects all tenant data, not user-scoped; status list is install-level info.

### Trigger mechanism — per-tenant manual button

Three options surfaced; chose **option 1: per-tenant manual trigger** via a Settings UI button.

- **Option 1 — chosen.** A "Backfill historical status IDs" button in the Settings tab POSTs to the resolver. Observable, debuggable, lowest engineering surface. Suitable for ~2 tenants today (maintainer's dev + example-tenant pilot).
- **Option 2 — deferred.** Auto-trigger on app upgrade via the lifecycle webhook. Cleaner for a paying-customer cycle but adds failure modes (what if the Jira call fails during an upgrade?) and ties the backfill to the lifecycle event timing. Easy to wire later: the existing `backfill_legacy_status_ids(session, tenant_id, jira_status_lookup)` function is trigger-agnostic; only the call site changes.
- **Option 3 — rejected.** One-shot job + admin endpoint that walks all tenants. Premature at our scale; introduces a per-tenant batching surface we don't need.

If a tenant with significant historical data onboards, Option 2 becomes worth the engineering. The function shape stays the same.

### Idempotency and the unresolved-name edge case

- **Idempotent by construction.** The UPDATE statements scope to `*_status_id IS NULL` only — re-running after a partial backfill picks up just the still-NULL rows. Safe to interrupt and resume.
- **Tenant isolation.** Every UPDATE includes `tenant_id = :tenant` in the WHERE clause. Verified by `test_backfill_scoped_to_tenant`.
- **Unresolved names.** A status that was renamed AND then deleted between the legacy write and this backfill won't appear in the current statuses lookup. Those names are surfaced in `BackfillResult.unresolved_names` and rendered in the Settings panel for the tenant admin to inspect. They can restore the status in Jira and re-run, or accept the orphan.
- **ORM identity-map staleness.** Bulk `update(...)` statements bypass the ORM identity map — any `Transition`/`TimeSlice` already loaded into the session still carries the pre-update values. `backfill_legacy_status_ids` calls `session.expire_all()` after the updates so subsequent reads pull fresh state from the DB. Without this, the caller's `.query().all()` returns stale objects.

### Distinct-name discovery covers all three columns

The first cut of `_distinct_null_status_names` only queried `transitions.to_status` and `time_slices.status`, missing `from_status` names that never appeared as a to-status anywhere (e.g., "To Do" is typically a first-transition `from_status` but rarely a `to_status`). Names that appeared only in NULL `from_status_id` rows got reported as unresolved (because they weren't included in the distinct query), and their `from_status_id` UPDATE never ran. Fixed and covered by `test_backfill_populates_all_null_rows`.

## Alternatives considered

### Alternative A — Defer Path B until a customer arrives with significant historical data

Rejected by maintainer direction 2026-06-07. The known-wrong behavior is the load-bearing reason to fix it, not customer arrival: a known-wrong customer-facing behavior isn't deferred. The queue framing is "closes the truth-debt opened by the partial fix" — the trigger to ship is queue slot opening, not customer signal.

### Alternative B — Build the backfill into the ADR-0045 PR

Rejected: bundling adds verification surface without clear benefit; the Path A foundation needs to ship first so subsequent rows write status_id correctly; Path B is a one-time pass that can land independently. Separated into this ADR.

### Alternative C — Avoid the backend entirely; do the backfill in a Forge resolver

Rejected: the backend has the persistence layer; the resolver doesn't see the database. Splitting fetch (resolver) + matching/UPDATE (backend) keeps responsibilities clean.

## Consequences

### Positive

- After this lands and it's run on a tenant, that tenant's historical data fully demonstrates the "renames don't break aggregates" behavior end-to-end.
- A tenant with significant historical data can run the same button immediately on install (or we wire it to lifecycle per Option 2).
- The implementation cost is bounded — ~200 lines of code (service + endpoint + resolver + Settings panel) + ~330 lines of tests. Pay-once.

### Negative

- The `_STATUS_IDS`-style seed test fixtures need to keep aligning pre-rename and post-rename names to the same id, or the seeded data won't demonstrate the property end-to-end. Already documented in the content-screenshots seed; carried as discipline for any future seed.
- A status that was renamed AND then deleted before the backfill runs leaves orphaned rows. The unresolved-name surface in the Settings panel makes this visible; tenant admin can restore the status and re-run, OR accept the orphan. Edge-case footprint expected to be zero for current tenants.

### Neutral

- The backfill button stays in the Settings tab even after a tenant has run it. No tracking column for "backfill completed" — re-clicking is a no-op (returns 0 updates), which is acceptable UX. Adding a tracker would be a small follow-up if it becomes annoying.

## Cross-references

- [ADR-0045](0045-status-id-aggregation-across-renames.md) — Path A (the foundation).
- [CLAUDE.md non-negotiable rule #1](../../../CLAUDE.md) — source of truth = changelog.
- [CLAUDE.md non-negotiable rule #12](../../../CLAUDE.md) — verification is load-bearing for "done".
- The standing principle that a known-wrong customer-facing behavior moves to next-available-slot priority — the reason Path B is pulled forward.
