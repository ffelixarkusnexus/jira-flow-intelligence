# 0038 — Best-in-category-defaults hierarchy; safe-default Done→Terminal merge with opt-in independent lists

- **Status:** Accepted (2026-06-01 — maintainer direction after a real-customer footgun surfaced on example-tenant)
- **Date:** 2026-06-01
- **Decision-makers:** the maintainer; technical drafting by Claude Code
- **Tags:** #ux #defaults #tenant-config #non-negotiable-rule #regression-fix

## Context

### The triggering bug

On 2026-06-01 the dashboard on example-tenant' install showed:

> **Top Insight — Done is the current bottleneck (VERY HIGH CONFIDENCE)**, avg time +93,514%.

Pure math artifact: terminal-state tickets accumulate forever and their time-in-status grows without bound. Two layered causes:

1. **`detect_bottleneck` never had terminal-status filtering.** Only `compute_trends` excluded terminal statuses (since the ADR-0036 trend-floor work). `detect_bottleneck` happily scored Done and friends. → Fixed in `afe5752` independent of this ADR; documented here for the audit trail.
2. **The customer's `terminal_statuses` override did not include `DONE` (all-caps).** They had added "DONE" to `done_statuses` to match their workflow but the existing `TenantContext.terminal_statuses` merge logic only auto-merged Done into Terminal when terminal was *not* overridden. Once the customer overrode terminal (any customization counted), the override became authoritative and Done values stopped being auto-included. So even with `detect_bottleneck` filtering by `terminal_statuses`, the DONE status fell through.

### Why two lists in the first place

Semantically distinct:

- **Done statuses** → "did the ticket *ship*?" Drives cycle-time and throughput.
- **Terminal statuses** → "is the ticket *out of flight*?" Drives bottleneck, CFD, trend exclusion. Includes shipped outcomes (Done, Released) plus not-shipped outcomes (Cancelled, Won't Do, Rejected, Duplicate).

In most workflows `Done ⊆ Terminal`. A rare-but-real edge case keeps them separate: workflows where Done is a *transient* state (Done → Verified → Released) — the team genuinely wants bottlenecks within Done flagged.

### Options considered

Three options went to the maintainer + reviewer; a fourth emerged from that discussion.

| # | Approach | Pro | Con |
|---|---|---|---|
| 1 | Always merge Done into Terminal at lookup time | Simple (~5 lines), eliminates footgun for everyone | Loses the rare Done≠Terminal workflow |
| 2 | UI warning when lists are inconsistent | Preserves flexibility, educates | More UI work + cognitive load; "inconsistent" is itself a concept the user has to understand |
| 3 | Redesign as one "endpoints" list with per-status "counts as shipped" flag | Cleanest conceptual model | Schema migration + UI rewrite + retrains existing users |
| **4** | **Safe-default merge + opt-in toggle for full independence** | **Footgun fixed for 100% of tenants by default; rare edge case preserved without taxing the 95%** | One extra column, one toggle in an Advanced section |

## Decision

### A. Implement Option 4 (the data shape)

Add `tenants.independent_done_terminal_lists BOOLEAN NULL` (additive migration). NULL / False is the safe default.

`TenantContext.terminal_statuses` becomes:

```python
base_terminal = (
    self.tenant.terminal_statuses
    if self.tenant.terminal_statuses is not None
    else self.settings.terminal_statuses
)
if self.tenant.independent_done_terminal_lists:
    return list(base_terminal)
merged = {*base_terminal, *self.done_statuses}
return sorted(merged)
```

Safe default merges `done_statuses` INTO terminal **regardless of override**. Toggle=True restores the prior "override is authoritative, no merge" behavior — the explicit opt-in for advanced workflows.

Settings UI surfaces the toggle in a collapsed **Advanced settings** expander at the bottom of the Tenant Configuration panel, native `<details>` element, single toggle inside. Help text names the specific scenario (Done → Verified → Released) so the user understands when to flip it.

### B. Establish CLAUDE.md rule #10 (the general principle this ship serves)

The fix above is a one-off if it isn't lifted into a general rule. The same shape of bug — "the default doesn't behave well, but the lever to fix it requires understanding a setting" — will recur across the product (alert config, threshold defaults, custom-field detection, sprint heuristics, …). Codify the principle now while the cost of the recent miss is fresh:

> **10. Best-in-category-defaults hierarchy.** Every feature ships with a configuration shape that respects this priority order when the rules conflict:
> 
> 1. **Safe default first.**
> 2. **Conceptual simplicity for the default path.**
> 3. **Edge-case preservation as opt-in, not as default visibility.**
> 4. **Discoverability calibrated to who needs the feature.**
> 
> **Test:** lower-numbered rule wins on conflict.

Full text lives in `CLAUDE.md` rule #10. Applied forward — no retroactive audit. When a feature design conflicts the rule, the lower-numbered priority wins. When a rule-#10 violation surfaces from a real install, stop and fix.

## Consequences

### Positive

- **Footgun fixed for all current and future tenants without any action on their part.** New column defaults to False; safe-default merge engages automatically on next request.
- **No retraining cost.** The default user sees no UI change; the Settings panel's three status lists keep their existing semantics.
- **The rare edge case is preserved**, accessible via one toggle in an Advanced section that 95% of admins will never open.
- **Forward-looking principle.** Rule #10 now governs Settings-UI design for ADR-0037 alert destinations and everything downstream.

### Negative / trade-offs

- One additional Tenant column to maintain (BOOLEAN, nullable).
- The Settings UI now has a new "Advanced settings" surface — if it accretes more toggles over time, that's a signal we may be drifting toward complexity that the hierarchy was designed to prevent. Re-evaluate the panel shape when the Advanced section approaches ~3 toggles.
- The toggle requires customer education when needed (docs / support). Acceptable: the population needing it is small and self-selects by reaching for the Advanced section.

### Migration

- Alembic migration `a14d0e5b2c83` adds the column nullable, no backfill needed (NULL is read as False by the merge logic).
- No data migration on existing tenants — their behavior changes from "override authoritative" to "override + done auto-merged" automatically on first request after deploy. This is the bug fix; the prior behavior was the bug.
- A tenant that *wants* the prior behavior (advanced workflow) flips the toggle in Settings; their effective terminal list returns to override-as-authoritative.

## Verification

- 5 unit tests in `backend/tests/test_tenant_context_done_terminal_merge.py` covering the full matrix (default tenant, override + toggle off, toggle on + override, toggle on without override, toggle flip-back).
- Existing tests in `backend/tests/test_insight_engine.py` (the `detect_bottleneck_excludes_terminal_statuses` cases shipped in `afe5752`) continue to verify the downstream filter works once the effective terminal list is correct.

## Related

- **Supersedes / extends:** none. The merge logic in `TenantContext.terminal_statuses` previously had no ADR — it was an inline implementation detail.
- **Cross-references:** ADR-0036 (the trend-floor / case-fold work that established terminal-status filtering in `compute_trends`), ADR-0037 (alert delivery destinations — first feature designed under rule #10), `afe5752` (the prior `detect_bottleneck` filtering fix that surfaced this footgun).
