# Glossary

Domain vocabulary used throughout the codebase, ADRs, and the dashboard. When in doubt, the definitions here are authoritative — if the bootstrap docs use a term differently, raise it.

| Term | Definition |
|------|------------|
| **Active status** | A status considered "work happening." Configured via `Settings.active_statuses`; defaults to `["In Progress", "Review"]`. |
| **Active time** | Sum of `duration_seconds` across slices whose `status` is in the active set. |
| **Bottleneck** | A status whose multi-signal score (ADR-0007) is ≥ `bottleneck_min_score` (default 3) when comparing the current window to the previous one. |
| **Confidence** | Categorization of the bottleneck score: 3 → medium, 4 → high, 5+ → very_high. |
| **Cycle time** | `done_at - created_at` for completed issues, `now - created_at` for open issues. Per-issue. |
| **Done status** | A status that means "completed." Configured via `Settings.done_statuses`; defaults to `["Done", "Closed", "Resolved"]`. |
| **Idempotency** | Re-running a step (ingestion, alerting) produces identical results — same row counts, same values, no duplicates. See ADRs 0005 and 0008. |
| **Insight** | A structured object containing the bottleneck status, score, confidence, and reasons. The `/insights` endpoint returns this plus optional natural-language `explanation`. |
| **Open slice** | The final slice of an issue that is not yet `Done`. Has `is_open=True` and `end_at == now` at time of computation; gets recomputed on every sync. |
| **Previous window** | The comparison baseline. For a `days=7` query: `[now - 14d, now - 7d]`. |
| **Score** | Sum of weights from the four bottleneck signals (ADR-0007). |
| **Signal** | One input to the bottleneck score: time_ratio, wip_ratio, throughput_delta. |
| **Slice / Time slice** | A contiguous interval `[start_at, end_at]` during which an issue was in a single status. The central analytical unit; everything else aggregates from here. |
| **Throughput** | Count of distinct issues that completed work in a status during a window (i.e., have a slice ending in that status during the window). System-level throughput counts issues with `done_at` in the window. |
| **Throughput delta** | `(current_throughput - previous_throughput) / previous_throughput`. Negative = slowdown. |
| **Time ratio** | `current_avg / previous_avg` for a status's avg time-in-status. ≥ 1 means it got slower. |
| **Time slice engine** | The `slicing_service` module. Converts a sorted sequence of transitions into time slices that cover `[created_at, done_at_or_now]` exactly once. See ADR-0004 (tz handling), 0005 (idempotency). |
| **Transition** | A row in the `transitions` table representing a single status change: `(issue_id, from_status, to_status, transitioned_at)`. Extracted from the Jira changelog. |
| **WIP (work in progress)** | Time-weighted average count of items in a status during a window. Computed as overlap-area / window-length. |
| **WIP ratio** | `current_wip / previous_wip`. ≥ 1 means the queue grew. |
| **Window** | A `[start, end)` interval over which metrics are computed. Default `days=7` produces a current window `[now-7d, now]` and a previous window `[now-14d, now-7d]`. |
