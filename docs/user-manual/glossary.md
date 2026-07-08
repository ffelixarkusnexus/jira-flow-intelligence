# Glossary

Vocabulary you'll see throughout the plugin and this manual.

**Active statuses.** Workflow stages where work is *being actively done*. The default is `["In Progress", "Review"]`. Tracked separately from "in-flight" because some workflows have stages like "Blocked" or "Waiting" where a ticket is in flight but no work is happening. Configurable per tenant.

**Bottleneck.** The workflow stage most likely slowing the team down right now. Computed from three signals — time ratio (current avg time in this stage / previous), WIP ratio (current WIP / previous), throughput delta (current completions / previous, signed). Each signal that crosses its threshold contributes to the score; the stage with the highest aggregate score wins.

**CFD (Cumulative Flow Diagram).** Stacked area chart over time showing the count of tickets in each non-terminal status at end-of-day. Bands widening over time = work piling up.

**Cycle time.** Wall-clock duration from a ticket's `created_at` to its `done_at`. The "how long did it take to ship?" metric.

**Done at.** The timestamp at which a ticket entered a "done" status (or, equivalently, its `resolutiondate` from Jira). NULL for in-flight tickets.

**Done statuses.** Statuses considered terminal-with-value-shipped. The default is `["Done", "Closed", "Resolved"]`. Used to compute cycle time and throughput.

**External-blocking status.** A workflow status configured per-tenant (6.4.0) where work is paused on a third party — e.g., *Blocked*, *Waiting for Customer*, *In External Review*. Time spent in external-blocking statuses is still tracked in slices, still shown in time-by-status charts, still surfaced in the issue panel; only the bottleneck-attribution surface excludes it. Default empty; opt-in per tenant. See ADR-0042 and [`settings.md`](settings.md#external-blocking-statuses-640).

**FIT (Forge Invocation Token).** Atlassian-issued, RS256-signed token attached to every call from the Forge runtime to the backend. The backend validates it against Atlassian's JWKS to confirm a request really came from a legitimate Forge install. See ADR-0019.

**In-flight.** A ticket where `done_at IS NULL`. Still moving through the workflow regardless of which specific status it's in.

**Recompute / async recompute (6.4.0).** A background pass that rewrites the `duration_seconds` on every historical `time_slices` row for a tenant under a newly-configured (or changed, or disabled) work schedule. Triggered when a tenant saves a Work schedule change; surfaced to the user as a "Recomputing metrics with your new work schedule…" banner with progress percentage. Idempotent — safe to re-run and safe to interrupt. The mental model is "lock the tenant into a single math model after activation"; the alternative would permanently blend calendar-time historical slices and working-time new slices. See ADR-0043.

**P50 / P85 / P95.** Percentiles computed by linear interpolation from the sorted set of cycle times in the window. P95 = the cycle time below which 95% of tickets finished. The Cycle Time Scatter overlays these as horizontal dashed lines; the WIP Aging chart uses P95 of recent cycles as a vertical reference.

**Project key.** The Jira project identifier (e.g., `VPST`). Every metrics endpoint accepts an optional `project_key` and filters to that project. The plugin's `jira:projectPage` mode always sets it; the underlying API supports tenant-wide queries when project_key is omitted.

**Sample size.** The number of *closed* slices in a status during a window. Bottleneck scoring requires non-zero sample size on both current and previous windows for a status to be eligible.

**Slice / TimeSlice.** A continuous interval `[start_at, end_at]` during which a ticket sat in a single status. Slices are the source of truth for every flow metric — never `current_status` alone.

**Sprint.** A Scrum-style fixed-length iteration. Sprint metadata (id, name, start_at, end_at, complete_at, board_id) lives in the `sprints` table; issue ↔ sprint membership in `issue_sprints`. Populated by the sync flow.

**Tenant.** One Jira site (Forge installation). All data is scoped to one tenant; cross-tenant queries are forbidden by middleware. See ADR-0014.

**Terminal statuses.** Statuses where work has *left* the pipeline — `done_statuses` plus rejected / cancelled / archived / etc. The CFD excludes these so the chart shows flow rather than archive growth. Default = settings list ∪ done_statuses; per-tenant override on `tenants.terminal_statuses`.

**Throughput.** The count of tickets that *completed* a status during the window (closed slice with `end_at` inside the window). The bottleneck panel's "Throughput" stat uses this for the bottleneck status; the system-wide cycle-time-throughput uses count of completed *issues* (`done_at` in window).

**Trend.** A current-vs-previous comparison labeled "improving" / "worsening" / "stable" based on configured thresholds. Trends are emitted both for the system-wide cycle time and for each per-status stat (avg time, WIP, throughput).

**WIP (Work-In-Progress).** The count of tickets in a given status at any given moment. The "WIP avg" stat is the time-weighted average of that count across the window. WIP is meaningful only against a configured limit (see [wip-limits.md](wip-limits.md)).

**WIP limit.** A configured maximum number of in-flight tickets a status should hold. Configured per `(tenant, project, status)`; tenant-wide rows act as defaults.

**Window.** A `(start, end)` pair the metrics are computed against. Day-based (7d / 30d / 90d); calendar-based (MTD / QTD); sprint-based.

**Work schedule (6.4.0).** Per-tenant working-calendar configuration — timezone, working days (e.g., Mon–Fri), work hours (e.g., 09:00–17:00), holidays. Default is 24/7 calendar time (pre-6.4.0 behavior). When active, every duration computation in the system — cycle time, time-in-status, alert thresholds, the bottleneck card's time signal — uses the working-hours-only math defined by this schedule instead of wall-clock. Saving, changing, or disabling a work schedule triggers an **async recompute** (see entry) of every historical slice. See ADR-0043 and [`settings.md`](settings.md#work-schedule-640).

**Working time.** Calendar duration **intersected with** the configured work-schedule calendar. The math the system uses everywhere when a **work schedule** (see entry) is active. A slice spanning Friday 17:00 → Monday 09:00 under a Mon–Fri 9–5 schedule has 0 hours of working time (the entire span fell outside working hours). The same slice with no work schedule configured would have ~64 hours.
