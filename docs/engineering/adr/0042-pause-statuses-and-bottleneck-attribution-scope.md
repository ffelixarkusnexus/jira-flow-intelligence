# 0042 — Pause statuses (external-blocking) excluded from bottleneck attribution

- **Status:** Accepted
- **Date:** 2026-06-06
- **Decision-makers:** the maintainer; Claude Code drafted
- **Tags:** #bottleneck #scoring #per-tenant-config #accuracy-correction
- **Related:** [ADR-0007](0007-bottleneck-scoring.md) (the scoring function this changes); [ADR-0038](0038-best-in-category-defaults-and-done-terminal-merge.md) (the per-tenant status-set pattern this extends); [CLAUDE.md](../../../CLAUDE.md) rule #10 (safe-default-first hierarchy)

## Context and problem statement

The bottleneck card today answers an unstated question: *"Across all statuses in the workflow, which one has the most concerning signal?"* It aggregates time + WIP + aging + transitions and names the highest-scoring status (per ADR-0007). For a workflow that includes external-blocking states — *Waiting on Customer*, *Blocked: Vendor Response*, *In External Review* — that math can correctly identify "Waiting on Customer is the bottleneck" and be operationally useless. The team **cannot fix** waiting on a customer.

Excluding external-blocking wait time is a correctness requirement, not a niche edge case. If the bottleneck card counts *Waiting on Customer* time against the team's own stages, it can name a "bottleneck" the team cannot fix — a wrong answer to the question the card is meant to answer (*"where is the team stuck on something they can fix?"*). A customer who asks *"does your bottleneck card exclude time we're waiting on the customer?"* should get a yes.

The user actually wants: *"Where is the team's **controllable** bottleneck?"* That's a different question, and it requires excluding statuses that are not team-controllable from the **attribution** step.

## Considered options

1. **Filter external-blocking statuses from time-slice generation entirely.** Rejected — the time data is still useful for charts, per-issue history, and customer-facing accuracy ("yes, this ticket spent 8 days Blocked"). Removing slices destroys legitimate information.
2. **Filter external-blocking statuses from the bottleneck card only.** Adopted. Slices preserved everywhere; only the attribution step (which status gets named as the bottleneck) excludes them.
3. **Surface a separate "external-blocking time" panel.** Future enhancement, not v1. The first job is to make the bottleneck card answer the right question; a dedicated panel is additive.
4. **User-configurable per-rule scoring weights (set weight to 0 for paused statuses).** Rejected as over-engineering. Boolean inclusion/exclusion is the simpler model; a weight slider invites tuning rabbit-holes and matches no operational decision a team actually makes.

## Decision

Add a per-tenant `external_blocking_statuses` configuration following the existing `active_statuses` / `done_statuses` / `terminal_statuses` pattern from [ADR-0038](0038-best-in-category-defaults-and-done-terminal-merge.md):

- **Schema:** `tenants.external_blocking_statuses` JSON column. `NULL` = inherit `Settings.external_blocking_statuses` default (which ships as `[]`).
- **TenantContext:** new `external_blocking_statuses` property mirroring the existing status-set accessors.
- **Scoring:** in `insight_service.detect_bottleneck`, statuses in `ctx.external_blocking_statuses` are skipped from the attribution iteration. The skip is case-folded (matches the existing `terminal_statuses` filter; see `insight_service.py:160-165`).
- **Slice data:** unchanged. `time_slices` rows continue to record external-blocking time. Charts (CFD, time-by-status) continue to display it. Per-issue history continues to surface it.
- **Default:** empty set. Existing tenants see no behavior change. The feature is opt-in via Settings.

## Consequences

### Positive

- The bottleneck card answers the question users actually have: *"Where is the team stuck on something they can fix?"*
- Slice and chart data preserved — the user can still see "yes, this ticket spent 8 days Blocked" without losing the operational signal in the bottleneck card.
- Makes bottleneck attribution correct: time spent waiting on an external party no longer counts against the team's own stages.
- The scoring change is one branch in one function (`detect_bottleneck`). Surface area for regressions is minimal.

### Neutral

- The Settings UI gains one more status-set picker. Cognitive load is bounded because the help text frames it precisely: *"Statuses where work is paused waiting on a third party. Time spent in these statuses is still tracked, but excluded from 'where is the team stuck?' attribution."*
- Per CLAUDE.md rule #10, the default is the *safe* default (empty set, current behavior). Configuration is the opt-in path. Default UI cognitive load is paid for by users who don't need the feature; the 5% who reach for it absorb the cost willingly.

### Negative

- A tenant who configures the wrong statuses as external-blocking can hide a real team-controllable bottleneck. The Settings help text names this risk: *"Leave blank if every status in your workflow is team-controllable."*
- The metric "what fraction of cycle time is external-blocking?" becomes meaningful and customers may ask for it. That's a future panel, not a regression.

## Tests proving the change

In `backend/tests/test_insight_service.py` (or wherever `detect_bottleneck` tests live):

- `test_detect_bottleneck_with_no_external_blocking_statuses_matches_existing_behavior` — empty set produces the same result as today.
- `test_detect_bottleneck_skips_external_blocking_statuses_from_attribution` — a status that would otherwise be named (highest score) is skipped when in the set, and the next-highest non-paused status is named instead.
- `test_detect_bottleneck_case_folds_external_blocking_match` — `"BLOCKED"` in the set matches a `"Blocked"` slice (and vice versa). Mirrors the existing `terminal_statuses` case-folding.
- `test_external_blocking_statuses_do_not_remove_slices` — `time_slices` rows for excluded statuses still exist with their durations; only attribution is affected.

## Cross-references

- [ADR-0007](0007-bottleneck-scoring.md) — the scoring function modified.
- [ADR-0038](0038-best-in-category-defaults-and-done-terminal-merge.md) — the safe-default-first pattern this extends. `external_blocking_statuses` defaults to empty (current behavior) for the same reason `independent_done_terminal_lists` defaults to False — safe-default for the 95%, opt-in for the workflows that need the lever.
- CLAUDE.md rule #10 — best-in-category-defaults hierarchy. The empty default for `external_blocking_statuses` is rule-compliant.
