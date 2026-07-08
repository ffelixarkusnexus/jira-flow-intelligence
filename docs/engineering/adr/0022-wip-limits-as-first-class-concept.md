# 0022 — WIP limits as a first-class concept

- **Status:** accepted
- **Date:** 2026-05-05
- **Decision-makers:** the maintainer
- **Tags:** #flow-analytics #data-model #signals #user-feedback

## Context and problem statement

The dashboard surfaced a bare `WIP (avg) 0.45` figure on the bottleneck panel and a `WIP` column on the all-stages table. a pilot Jira admin raised an unanswerable question:

> ¿Cuál es el WIP LIMIT por etapa? No lo veo definido. ¿Qué cuenta como WIP? ¿El 0.45 es 'bueno' o 'malo'? Sin límites de referencia, es solo un número.

He's right. WIP in a Kanban / Lean sense is meaningful only against a configured limit. A `current_wip = 0.45` average without an associated limit gives the user no decision: is the team overloaded? underloaded? operating normally? The signal is noise unless rendered as `current / limit` with breach detection.

We had three problems:

1. The product showed a number that the admin couldn't act on.
2. Bottleneck reasons that compared WIP ratios across windows did carry context, but the bare displays drowned out that signal.
3. There was no place in the schema to record per-status WIP limits, no API, no UI.

## Considered options

- **A. Drop WIP entirely from the product.** Simplifies; loses real flow signal that other Kanban tools deliver.
- **B. Hardcode a single tenant-wide WIP threshold.** Easy. Useless: every workflow has different stages with different sensible limits.
- **C. First-class WIP limits, per status, per project (with tenant-wide fallback).** New `wip_limits` table; UI to configure; renderings update to `current / limit`; new alert rule type. Real product feature; ~1 week of work.
- **D. Defer until the broader settings UI ships.** Postpones the credibility fix; leaves the misleading display in production for weeks.

## Decision

**Option C, fast-tracked ahead of the webhook and backfill work.** Per the strategic principle that *making the numbers meaningful matters more than making them fresh* (webhooks/backfill make stale numbers fresh; meaningless numbers stay meaningless either way).

In the meantime (Tier 1, already shipped), the bare WIP averages were hidden and replaced with a placeholder pointing at the forthcoming configuration. This avoids leaving a misleading display in production while the WIP-limits work is in flight.

### Data model

```sql
CREATE TABLE wip_limits (
    tenant_id        TEXT     NOT NULL REFERENCES tenants(client_key) ON DELETE CASCADE,
    project_key      TEXT     NULL,        -- NULL = tenant-wide default
    status           TEXT     NOT NULL,    -- raw status name (case-preserved)
    max_in_progress  INTEGER  NOT NULL CHECK (max_in_progress >= 0),
    breach_minutes   INTEGER  NOT NULL DEFAULT 0 CHECK (breach_minutes >= 0),
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, project_key, status)
);
```

- `project_key NULL` rows act as defaults for any project on the tenant.
- A row with a populated `project_key` overrides the NULL row for that project + status.
- `breach_minutes` controls the alert pipeline (the `wip_breach` rule) — 0 disables the alert; positive value means "fire after N minutes sustained over `max_in_progress`."
- Status name is case-preserved on storage but the lookup helper (`get_wip_limit`) does case-folded matching to match the case-folded grouping behavior of the metrics services.

### Resolution helper

```python
def get_wip_limit(session, tenant_id, project_key, status):
    """Returns (max_in_progress, breach_minutes) or (None, None).

    Project-scoped row beats tenant-wide. Case-folded status matching."""
```

### Rendering contract

- When **no** WIP limit is configured for the (project, status) pair, the panel renders nothing (Tier 1 placeholder takes over until the WIP-limits settings ship).
- When configured, render `WIP {current_wip:.1f} / {max_in_progress}`. When `current_wip > max_in_progress`, the cell carries a red border (≥ 1.0× over) or amber (≥ 0.8× of limit, approaching).
- The WIP Aging chart adds a vertical reference line at the limit per status row.

### Alert rule

`wip_breach` rule type in the existing `alert_rules` / `alerts` machinery. Config:

```json
{"status": "Code Review", "project_key": "VPST", "sustained_minutes": 60}
```

Idempotent per `(rule_id, status, breach_started_at)` — the same breach window doesn't re-fire repeatedly.

## Consequences

**Positive.**

- The dashboard's WIP signal becomes actionable. `5 / 3` carries decision value; `0.45` does not.
- Per-project limits map directly to the `jira:projectPage` mental model (different teams, different limits).
- The new alert rule type slots into the existing alerts surface without a separate notification channel.
- Sets up the broader Settings tab that later work will extend.

**Negative.**

- One new table, one new endpoint, one new alert rule type. ~1 week of work.
- Every chart that mentions WIP has to know whether a limit exists for the (project, status) — adds a render-time lookup. Cached in memory at request scope.
- Users who don't configure limits get a degraded experience (placeholder). Acceptable since the alternative was misleading numbers.

**Open questions.**

- Whether to auto-suggest reasonable defaults (e.g., `max_in_progress = ceil(team_size × 0.5)`) on first install. Probably not — too presumptuous. Keeping the surface explicit until we see how users adopt it.
- Whether `breach_minutes` should be a per-rule parameter on the alert side rather than a column on `wip_limits`. Tradeoff: keeping it on `wip_limits` lets a tenant define limits once and have a single canonical breach window; moving it to the rule lets multiple rules with different sensitivities coexist. **Decision: keep it on `wip_limits` for the v1; revisit if customers ask for per-rule overrides.**
