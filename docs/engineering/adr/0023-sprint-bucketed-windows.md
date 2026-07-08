# 0023 — Sprint-bucketed windows for sprint-aware comparisons

- **Status:** accepted
- **Date:** 2026-05-05
- **Decision-makers:** the maintainer
- **Tags:** #flow-analytics #jira-software #user-feedback

## Context and problem statement

Day-bucketed windows (7d / 30d / 90d / MTD / QTD) don't match how Scrum teams reason about flow. When an SM or PO opens the dashboard the question they're trying to answer is "*how did this sprint go vs. last sprint?*" — not "how did the last 7 days compare to the 7 days before that."

a pilot admin flagged this directly:

> los filtros de 7, 30 y 90 dias solo lso veo en overview. lo chido sería.. tener .. por sprint, por mes y por cuarto... eso ya se hace super administrativo.

> siempre ten una comparativa.. este sprint como vamos?.. comparado con el sprint anterior que rollo?

Without sprint awareness:
- We can't surface sprint-over-sprint comparison framing in the bottleneck card.
- Customers running sprints see a generic 30-day view that smears across sprint boundaries and team cadence.
- The "best in class for flow intelligence" claim doesn't survive a five-minute conversation with a Scrum Master.

## Considered options

- **A. Skip sprints entirely.** Recommend `MTD` / `QTD` as proxies. Doesn't match Scrum cadence; sprints are not month-aligned.
- **B. User-managed "sprint" windows by date.** Let users define a custom date window labeled "Sprint 42." Brittle, and the team has to maintain a separate truth from Jira.
- **C. Pull sprints from the Jira Software Sprint API; persist sprint metadata + issue-sprint membership; bucket metrics by sprint.** Real integration. The expected behavior for a flow tool installed on a `jira:projectPage`. ~1 week. Requires a manifest scope addition (`read:sprint:jira-software`, `read:board-scope:jira-software`) which triggers a Forge permission re-grant prompt for the admin on next install upgrade.
- **D. Query Jira for sprint metadata at every dashboard render.** No DB cost, but slow (extra API calls in the hot path) and breaks once we have a real customer with rate-limit pressure.
- **E. (Path 3) Read sprint metadata + issue-sprint membership directly from the Sprint custom field on each issue payload that the existing sync already fetches.** No new scope (the `read:jira-work` scope already grants custom-field reads). No new API calls in the resolver. The trade-off is sprints whose issues all sit *outside* the current sync window aren't discoverable — for the dashboard's purposes that's fine, since those sprints have no signal to surface anyway.

## Decision

**Option E (Path 3)**, shipped 2026-05-05. We initially chose Option C, but during implementation realized the issue payload's Sprint custom field already carries everything we need (id, name, state, boardId, dates), and the existing `read:jira-work` scope already grants access. Path 3 strictly dominates Option C on reliability — no permission re-grant prompt, no extra Jira API calls, no rate-limit risk on multi-board projects. The "sprints with no recent issues" gap Path 3 has is irrelevant in practice (there's no flow signal to surface for sprints whose issues are all outside the sync window).

The data model and bucket semantics described below are unchanged from Option C — only the *source* of sprint data changes (issue payload's custom field instead of `/rest/agile/1.0/board/{id}/sprint`).

### Data model

```sql
CREATE TABLE sprints (
    tenant_id    TEXT NOT NULL REFERENCES tenants(client_key) ON DELETE CASCADE,
    id           BIGINT NOT NULL,           -- Jira sprint id
    name         TEXT NOT NULL,
    state        TEXT NOT NULL,              -- 'active' | 'closed' | 'future'
    start_at     TIMESTAMPTZ,
    end_at       TIMESTAMPTZ,
    complete_at  TIMESTAMPTZ,
    board_id     INTEGER NOT NULL,
    project_key  TEXT,
    raw_payload  JSONB,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE issue_sprints (
    tenant_id    TEXT NOT NULL,
    issue_id     TEXT NOT NULL,
    sprint_id    BIGINT NOT NULL,
    PRIMARY KEY (tenant_id, issue_id, sprint_id),
    FOREIGN KEY (tenant_id, issue_id) REFERENCES issues(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, sprint_id) REFERENCES sprints(tenant_id, id) ON DELETE CASCADE
);
```

- An issue can belong to multiple sprints over its lifetime — Jira allows re-adding a ticket to a future sprint after carry-over. We store the union.
- Sprint metadata is fetched per board; one project can map to multiple boards (we sync them all and dedupe on `(tenant_id, sprint_id)`).
- `state` reflects Jira's notion at sync time. We re-fetch on each scheduled reconciliation.

### Sync flow (Path 3, shipped)

- **No new manifest scope.** `read:jira-work` (already granted) covers reading the Sprint custom field on issues.
- The existing `syncJira` resolver requests the Sprint custom field IDs (`customfield_10020`, `customfield_10010`, `customfield_10000`, `customfield_10001`) alongside the existing fields. Backend tries them in order — same fallback pattern as story points.
- During issue ingestion, `_extract_sprints` parses the Sprint custom field. Each sprint is upserted into the `sprints` table; the issue's `IssueSprint` membership is replaced with the union of sprints found.
- Issues with no Sprint custom field clear their membership — handles the case of a ticket being removed from a sprint between syncs.
- Idempotency: re-running ingestion doesn't duplicate sprint rows or membership rows (composite PKs + replace semantics on `set_issue_sprints`).

### Window semantics

The picker gains three sprint-bucket options on top of the existing day buckets:

- **"Current sprint"** — the active sprint for the project at request time. Uses `start_at` and (now or `end_at`) as the window bounds.
- **"Previous sprint"** — the most-recently-closed sprint. Bounds = `start_at` and `complete_at`.
- **"Last 3 sprints"** — union of the three most-recently-closed (or active+last-2-closed) sprints. The "previous window" used by the bottleneck pipeline becomes the three sprints before that.

The bottleneck card and trends panel rephrase from "vs previous window" to "vs Sprint 42" when sprint mode is active.

### Cross-sprint tickets

A ticket that was in sprint 41 and is also in sprint 42 contributes to the metrics of *both* sprints — slices that fall within sprint 42's bounds count toward sprint 42, slices in sprint 41's bounds count toward sprint 41. **This is deliberate and matches how teams reason about flow:** if a Code Review took 8 days spanning two sprints, both sprints saw 4 days of that delay, and both sprints' bottleneck signals should reflect it.

Cycle time for completed tickets is attributed to the sprint in which they were *completed* (the sprint containing `done_at`). This matches how Scrum teams already report velocity — a ticket completed in sprint 42 is a sprint 42 deliverable regardless of when it started.

### Multi-board projects

A project can have multiple boards. The picker scopes sprints to the project from `ctx.projectKey`, so a project with three boards sees sprints from all three. If two boards have overlapping sprint name spaces (rare; possible) we surface board name in the picker label to disambiguate.

## Consequences

**Positive.**

- The dashboard speaks Scrum out of the box. SMs and POs can reason about sprints without translation.
- Sprint-over-sprint comparison framing on the bottleneck card replaces the generic "vs previous window" text with something agile teams already think in.
- Foundation for sprint-level alerting (sprint-end velocity below baseline, etc.) in later extensions.

**Negative.**

- New scope means existing installs need a permission re-grant. Forge surfaces this on next install/upgrade.
- Two new tables + sync extension. ~1 week.
- Cross-sprint attribution doubles per-slice work; likely fine at our scale; revisit if it ever shows in profiling.

**Open questions.**

- Whether to expose Kanban-only projects (no sprints) gracefully — they should fall back to the day-bucket picker. **Decision: yes; the picker hides sprint options when no sprints exist for the active project.**
- Whether to integrate Jira's "Closed Sprint Report" data (committed vs. completed) into the dashboard. Out of scope for now; flag as a later stretch if customers ask.
