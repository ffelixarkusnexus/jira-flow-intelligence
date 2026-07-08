# 0021 — Project-scoped metrics for `jira:projectPage` modules

- **Status:** accepted
- **Date:** 2026-05-02
- **Decision-makers:** the maintainer
- **Tags:** #forge #scoping #correctness

## Context and problem statement

The plugin renders inside Jira via a `jira:projectPage` module — meaning
the user opens it from one specific project's left-nav, on a URL of the
form `/jira/software/projects/<KEY>/apps/<id>`. Customers (and Atlassian
reviewers) expect a `jira:projectPage` to show data scoped to that
project. Until now every metrics endpoint computed tenant-wide values
(every issue, every project, summed). The Custom UI already pulls
`context.extension.project.key` from Forge and renders it in the header
band, but it never passed it to the backend — so the dashboard text said
"project ABC" while the bottleneck card was actually summarising the
whole site.

This is a Marketplace-blocker (security review will flag the mismatch),
and it would silently corrupt webhook semantics: an "issue updated"
event on PROJ-A would invalidate caches that mix PROJ-A's and PROJ-B's
data together.

## Considered options

- **A. Status quo (tenant-wide).** Cheap. Misleading. Disqualifying for
  the Marketplace category we want to ship under.
- **B. Add a `project_key` query parameter to every read endpoint;
  filter at the SQL layer with an `Issue.project_key = ?` clause (or a
  JOIN through Issue for slice-level tables).** Backwards-compatible
  (no `project_key` = old behaviour). No schema change.
- **C. Make project_key load-bearing — drop the optional path entirely.**
  Cleaner contract but breaks any future "all projects" view (settings
  page, multi-project rollup) and forces every test to thread a key.

## Decision

**Option B.** Threaded `project_key: str | None = None` through every
read service (`metrics_service`, `wip_aging`, `cfd`, `cycle_scatter`,
plus `discover_statuses` / `discover_status_groups`) and surfaced a
`project_key` query parameter on each router endpoint. The Custom UI
captures `ctx.projectKey` from `getContext` and forwards it on every
api call; the backend filters with either a JOIN through Issue (for
slice-keyed tables) or a direct `WHERE Issue.project_key = ?`. The
parameter stays optional so non-project surfaces (tests, future global
views, settings page) still work unmodified.

For alerts: persisted `Alert` rows pre-date this change and have no
`project_key` column. When `list_alerts` is called with a project key,
we filter to alerts whose `issue_id` belongs to an issue in that
project, plus all non-issue (status/trend) alerts. Persisting a
`project_key` on the alert row itself is deferred until the
alert-rules UI gives us a natural place to record per-project rules.

## Consequences

**Positive.**

- Every chart on the dashboard now agrees with the project banner.
- Webhooks can scope cache invalidation by project without a
  schema migration.
- Atlassian's review process won't flag the scope mismatch.
- The `Issue.project_key` index introduced in earlier migrations
  carries the filter at near-zero cost.

**Negative.**

- Eight extra unit tests (`test_project_isolation.py`) now run on
  every CI build. ~50ms total — accepted.
- Any service that queries TimeSlice now does an indexed JOIN through
  Issue when project filtering is on. Plan-level cost is fine on the
  current dataset; will revisit if Postgres EXPLAIN ever shows it as a
  hot path.
- Persisted alerts have no project tag yet — a later change will close the loop.
