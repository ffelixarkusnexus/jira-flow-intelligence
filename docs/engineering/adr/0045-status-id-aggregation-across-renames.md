# 0045 — Status-ID-aware aggregation across renames

- Status: Accepted
- Date: 2026-06-07
- Deciders: maintainer, reviewer, Claude Code

## Context

The plugin's documentation makes an explicit, customer-facing technical claim about how it handles status renames in Jira workflows.

The claim:

> *"Status renames don't break aggregates. The changelog records the status name at the time of the transition. Renames don't rewrite history; they just change what future transitions get tagged as."*

And, in more detail:

> *"The fix is to read the status ID, not the name. Jira's API exposes both; the changelog records the status name at the time of each transition (so renames don't rewrite history), but the underlying status ID is the stable identifier across renames. A plugin that joins on ID and renders the current name preserves comparison across the rename; a plugin that joins on name loses everything before the rename."*

The reviewer's verification gate against the current main branch surfaced that the implementation does NOT match the claim:

- `Transition.from_status` / `Transition.to_status` are `Mapped[str | None]` storing the status **name** (`backend/app/db/models.py:207-208`).
- `TimeSlice.status` is `Mapped[str]` storing the status **name** (`backend/app/db/models.py:229`).
- The unique constraint on transitions joins on `("tenant_id", "issue_id", "transitioned_at", "to_status")` — name (line 199).
- The time-slice index is `("tenant_id", "status", "start_at")` — name (line 223).
- `slicing_service.py` propagates status names through the slice pipeline (lines 56, 77, 84, 104).
- Zero `status_id|statusId` references anywhere in `backend/app/` or `forge-prod/src/`.
- `metrics_service.discover_status_groups` does **case-equivalent** name grouping via casefolding (`Code Review` ↔ `CODE REVIEW`) but not true rename aliasing (`In Review` → `Code Review`).
- `ingestion_service.py:43` reads `(fields.get("status") or {}).get("name")` — only the name.

Because the plugin joins on the status **name**, it "loses everything before the rename" — the pre-rename and post-rename names become two disconnected groups.

Per [CLAUDE.md non-negotiable rule #1](../../../CLAUDE.md) ("Source of truth = changelog"), [rule #11](../../../CLAUDE.md) ("Don't cheat"), and the standing rule that we don't ship a known-wrong customer-facing behavior, the right answer is to fix the implementation before the claim ships publicly.

## Decision

Add a stable status identifier to the transition + time-slice schema. Aggregate by ID; render the current name at query time.

### Schema additions

Three new nullable columns + indexes:

- `transitions.from_status_id` — `Mapped[str | None]`, indexed, nullable for legacy rows.
- `transitions.to_status_id` — `Mapped[str | None]`, indexed, nullable.
- `time_slices.status_id` — `Mapped[str | None]`, indexed, nullable.

Plus a composite index on time_slices: `("tenant_id", "status_id", "start_at")` alongside the existing name-keyed index (the name index stays for legacy NULL rows + render-time display lookups).

The unique constraint on `transitions` keeps its existing shape `("tenant_id", "issue_id", "transitioned_at", "to_status")`. We considered swapping `to_status` for `to_status_id`, but `to_status_id` is nullable for legacy rows — UNIQUE with NULL columns behaves dialect-specifically (Postgres treats NULLs as distinct, SQLite older versions treated them as equal). Keeping `to_status` (always populated) in the dedupe key avoids dual-dialect footguns. New rows still have unique IDs in `to_status_id` for query-side filtering; dedupe protection on a re-played changelog stays on (transitioned_at, to_status), which is the existing contract.

### Sync (ingestion) reads status.id from Jira payloads

Jira's REST API exposes both `id` and `name` on every status object:

- `fields.status.id` — stable string identifier (`"10042"`).
- `fields.status.name` — current display name (`"Code Review"`).

Changelog item shape (per Atlassian docs):

```json
{
  "field": "status",
  "from": "10042",           // status id (source)
  "fromString": "In Review", // status NAME at the time of transition
  "to": "10042",             // status id (target — same after a name-only rename!)
  "toString": "Code Review"
}
```

`ingestion_service.upsert_issue_from_payload` and the transition-emission path read both fields and persist the id into the new columns. Legacy payloads without the id continue to write `NULL` into the column.

### Slice construction propagates status_id

`slicing_service.build_time_slices` accepts each transition's `to_status_id` and emits it on the resulting `BuiltSlice` row. Final-state slices (open or done) inherit the same id from the most-recent transition.

### Aggregate queries: group by status_id, fall back to name

`metrics_service.discover_status_groups` returns `(display_name, name_variants, status_id)` tuples now. The grouping rule:

1. For slices with non-NULL `status_id`: group by `status_id`. Display name = the variant with the most-recent `MAX(end_at)` (i.e., the current name post-rename). All historical name variants for that id are returned as `variants` so downstream `WHERE status IN (variants)` queries continue to work.
2. For slices with NULL `status_id` (legacy data): fall back to the existing case-folded name grouping. These groups can't be merged with the ID-keyed groups — that's the bound the legacy data sets.

Result: a tenant whose data is fully populated with status_ids sees every rename collapsed into one display group. A tenant with mixed legacy + new data sees ID-keyed groups for new data and name-keyed groups for legacy data — the legacy data still exhibits rename drift until a one-time backfill is applied (see "Path B" below).

### Display-name resolution: dynamic, not via lookup table

We considered introducing a `statuses (tenant_id, status_id, current_name)` lookup table to make rename-to-display lookups O(1) instead of O(slice-rows-per-status-id).

**Decision: dynamic resolution from the slice rows for v1.** No lookup table.

Reasoning:

- Single source of truth (the changelog → time_slices) — no consistency risk between two tables.
- At ~50k issues per tenant, `MAX(end_at) GROUP BY status_id` is sub-millisecond with the new `(tenant_id, status_id, start_at)` index.
- Adding a lookup table is straightforward to revisit if a performance signal demands it; removing one is harder.
- Premature optimization in the absence of a performance budget violation.

### Legacy row handling

Two paths:

- **Path A — Mixed-mode aggregation (this ADR).** New transitions get `status_id` populated. Legacy NULL rows stay NULL and fall through the name-based grouping. Renames affecting legacy data continue to drift. Renames affecting only post-fix data are correctly aggregated.
- **Path B — Historical backfill (queued as follow-up).** Fetch `/rest/api/3/status` per project per tenant, build a name → id lookup, UPDATE NULL rows. Risk: a status renamed AND deleted between legacy write and backfill won't resolve. Mitigation: log unresolved names; tenant admins can re-run after restoring the status. Engineering cost: ~0.5 day.

This ADR commits to Path A. Path B is queued as a follow-up workstream — the maintainer decides when to ship it (see ADR-0046).

### Property the change makes true

After this ADR ships:

- A status renamed in Jira after the fix preserves its historical aggregates under the current name in Jira Flow Intelligence for the data captured after the fix.
- The documented claim *"status renames don't break aggregates"* is true of Jira Flow Intelligence for any tenant whose data is post-fix.
- The claim's specific prescription — *"join on ID, render name"* — matches what Jira Flow Intelligence implements.
- After Path B (when shipped), the property extends retroactively to pre-fix data.

## Alternatives considered

### Alternative A — Status aliasing in application code only

Keep the schema string-keyed. Add a `(tenant_id, name → canonical_name)` map and apply it as a translation layer in `discover_status_groups`.

Rejected: requires the maintainer to manually configure rename aliases per tenant. Doesn't read from Jira's source of truth. Brittle — every rename requires explicit configuration. Contradicts the *"deterministic from the changelog"* principle.

### Alternative B — Replace the schema column type entirely

Change `Transition.to_status` and `TimeSlice.status` from `Mapped[str]` to `Mapped[StatusRef]` (a value-object holding both id and name).

Rejected: breaks every existing query, every alert rule that targets a status name, the CSV export schema customers may have integrations against, and the demo-fixture seed format. Higher blast radius without enough benefit — the additive nullable column approach achieves the same property with strictly additive change.

### Alternative C — Defer the fix; leave the behavior as-is

Reject the "demonstrate the fix" interpretation; leave the drift demonstration the seed currently produces.

Rejected by maintainer direction 2026-06-07: don't ship a known-wrong customer-facing behavior. The claim is customer-facing; the product needs to live up to it.

## Consequences

### Positive

- The documented claim becomes true (forward-looking immediately; retroactively after Path B).
- The handoff-verification flow added to CLAUDE.md the same day caught a real documentation-vs-implementation gap before publish.
- All future aggregation paths inherit ID-keyed grouping for free (any new chart or alert built on `discover_status_groups` benefits automatically).
- The plugin now handles status renames correctly on this specific architectural axis — the documented claim holds without caveats.

### Negative

- Three new nullable columns + two new indexes. Storage cost is negligible at the per-tenant scale we operate at.
- `discover_status_groups` becomes ~15 lines more complex (variants now include both ID-discovered and name-fallback paths). Trade-off accepted — the existing case-folding code stays intact for legacy compatibility.
- A tenant whose data straddles the fix (legacy + new) sees two groups for any renamed status — one for the legacy data (orphaned under the pre-rename name) and one for post-fix data (under the current name). Path B closes this gap.

### Neutral

- The seed fixture that previously demonstrated the drift now demonstrates the fix — no seed-code change needed; the implementation change is what flips the meaning.

## Cross-references

- [CLAUDE.md non-negotiable rule #1](../../../CLAUDE.md) — source of truth = changelog
- [CLAUDE.md non-negotiable rule #11](../../../CLAUDE.md) — don't cheat
- [CLAUDE.md non-negotiable rule #12](../../../CLAUDE.md) — verification is load-bearing for "done"
- [ADR-0006 — deterministic engine, AI text only](0006-deterministic-engine-ai-text-only.md)
- [ADR-0042 — pause statuses and bottleneck attribution scope](0042-pause-statuses-and-bottleneck-attribution-scope.md) (filtering by status name continues to work because it groups via the same `discover_status_groups` path; ID-keyed grouping is additive)
- [ADR-0043 — work schedule and working-time duration math](0043-work-schedule-and-working-time-duration-math.md) (recompute pipeline unaffected — status_id is propagated through slice construction, not recomputation)
- Surfacing case: a reviewer code-grep verification on 2026-06-07 surfaced the gap; maintainer authorization for this workstream 2026-06-07.
