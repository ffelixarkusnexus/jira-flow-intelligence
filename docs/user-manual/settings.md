# Settings

> Status: WIP limits, Backfill, Alert rules, and Tenant configuration all live as of 2026-05-06.

## What's live now

There is no settings UI yet. The plugin uses sensible defaults out of the box:

- **Active statuses** — `["In Progress", "Review"]`. Used by the `active_seconds` calculation on the issue-metrics service.
- **Done statuses** — `["Done", "Closed", "Resolved"]`. Used to mark tickets as completed (drives cycle time + throughput).
- **Terminal statuses** — Done statuses plus `["Won't Do", "Wontfix", "Cancelled", "Canceled", "Rejected", "Duplicate"]`. Excluded from the CFD chart. Per-tenant override available on the `tenants.terminal_statuses` column (currently editable only by a developer).
- **Bottleneck thresholds** — `time_ratio >= 1.3`, `wip_ratio >= 1.2`, `throughput_delta <= -0.2`, minimum aggregate score 3.

These defaults match the recommended baselines from the original product spec. Most teams will benefit from leaving them as-is until they have ~30 days of data and a sense of their own normal flow.

## WIP limits panel

A focused **Settings tab** alongside Overview and Flow. The first version ships with one panel:

- **WIP limits** — per-status `max_in_progress` and `breach_minutes`. Project-scoped rows override tenant-wide defaults. See [wip-limits.md](wip-limits.md).

## Additional panels

The same Settings tab gains additional panels:

- **Active statuses** — picker that knows your discovered status names (case-folded groups).
- **Done statuses** and **terminal statuses** — same picker pattern.
- **Threshold sliders** for bottleneck and trend signals.
- **Story-points custom field** — currently the plugin tries `customfield_10016 / 10026 / 10002 / 10004` in order. An admin can pin the right field for their site.
- **AI explanation toggle** — currently always on if `ANTHROPIC_API_KEY` is configured; a tenant can disable it.
- **Reset to defaults** button — drops all overrides on the `tenants` row, returning to the in-config values.

## Tenant vs. project scope

Some settings are tenant-wide (one value per Jira site): `active_statuses`, `done_statuses`, thresholds. Others are per `(tenant, project)`: WIP limits. The settings UI will make the scope explicit — tenant-wide settings show with a banner ("applies to all projects on this site"), project settings with a banner naming the active project.

## External-blocking statuses (6.4.0)

Statuses where work is paused waiting on a third party — *Blocked*, *Waiting on Customer*, *In External Review*, anything along those lines. Configurable from **Settings → External-blocking statuses** on the project page.

**What this changes:**
- The **bottleneck card** stops attributing time spent in these statuses. The card still surfaces the team's most concerning workflow stage, but a status the team can't act on (because they're waiting on someone external) is no longer named as "the bottleneck."
- Time spent in these statuses is **still tracked everywhere**. The Cumulative Flow Diagram still shows the band. The time-in-status charts still surface the duration. Only the attribution surface filters them.

**Default:** empty. Existing installs see no behavior change until you add a status.

**How to configure:** open Settings, locate the *External-blocking statuses* picker next to *Active* / *Done* / *Terminal*. Type or pick the status names that match your workflow. Save.

**No recompute needed** — this is a query-time filter applied at insight calculation, not a stored attribute on the slice rows. The next time the dashboard renders (or the alert evaluator runs), the new set takes effect.

Reference: ADR-0042.

## Work schedule (6.4.0)

Configure your team's working hours so cycle time, time-in-status, alert thresholds, the bottleneck card's time signal, and WIP aging all honor business hours instead of treating every hour the same. **Settings → Work schedule** on the project page.

**Fields:**
- **Timezone** — single tz for the schedule (e.g., `Europe/Madrid`, `America/New_York`).
- **Working days** — Mon–Sun chip picker. Mon–Fri is the typical default.
- **Work hours** — start and end (e.g., 09:00–17:00).
- **Holidays** — comma-separated `YYYY-MM-DD` dates.
- **Enabled** — toggle. When off, calendar time applies everywhere.

**Default:** no schedule configured. All duration math is 24/7 calendar time — bit-for-bit identical to pre-6.4.0 behavior. Existing installs see no change until you create a schedule.

**What this changes when a schedule is active:**
- A ticket that enters Review on Friday 17:00 and exits Monday 09:00 has a working-time duration of 0 hours (the weekend is skipped).
- A `cycle_time > 7d` alert under a Mon-Fri 9–5 schedule fires after ~7 *working* days (about 11 calendar days), not 7 calendar days.
- The bottleneck card's time signal isn't inflated by weekends or holidays.

**What happens when you save a schedule (activate, change, or disable):** a background **recompute** runs over every historical `time_slices` row for your tenant, rewriting `duration_seconds` under the new schedule. While it runs, a **"Recomputing metrics with your new work schedule…"** banner sits at the top of the dashboard with a progress percentage. The banner auto-dismisses when recompute completes. This design (rather than forward-looking-only activation) is deliberate: it guarantees you land in a single consistent math model after activation, instead of permanently blended numbers where old slices are calendar-time and new slices are working-time.

Recompute is idempotent. Safe to re-activate / re-disable as many times as you want; the math always lands on what `working_seconds_between(start, end, schedule)` produces against the currently-saved schedule.

Reference: ADR-0043.

## Permissions

Settings is admin-only. The plugin uses Forge's `asUser` and the underlying admin scopes to determine who can edit; non-admins see a clear "you don't have permission to edit settings" state.
