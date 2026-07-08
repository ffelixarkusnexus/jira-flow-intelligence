# Getting started

## What this plugin does

Flow Intelligence reads your Jira issue changelogs and computes:

- **Flow metrics** — average / P50 / P90 time spent in each workflow status, throughput, WIP averages.
- **Bottleneck detection** — identifies the workflow stage most likely slowing your team down right now, scored on three signals (time, WIP, throughput) and explained in one sentence.
- **Trends** — current window vs. prior window comparisons surfaced as "improving" / "worsening" / "stable."
- **Charts** — WIP Aging (which tickets are stuck right now), CFD (how work is piling up over time), Cycle Time Scatter (which completed tickets took longer than usual).
- **Alerts** — rules that fire when a configured threshold is breached.

Everything is computed deterministically from Jira's changelog. The same data always produces the same metrics. AI is used **only** to translate already-computed signals into one-sentence explanations — it never affects the numbers.

## Where the plugin lives in Jira

The plugin is a **`jira:projectPage`** module — meaning it appears in the left-nav of each individual Jira project under "Apps." Click it from a project's nav to see that project's flow analytics.

The header banner shows which cloud and project you're looking at:

> cloud `<cloud-id>` · project `VPST`

If the banner shows a project key, every chart and metric on the page is filtered to that project. There is currently no global "all projects" view — each project gets its own scoped page.

## First-time install

A Jira admin installs the plugin once for the whole site. After install:

1. Open any project in Jira.
2. Click "Apps" in the left-nav (or "Apps" → "Flow Intelligence" depending on your nav layout).
3. The dashboard loads. On a fresh install it shows "Loading flow intelligence…" and then either populated data (if Jira has activity on this project) or an empty state.

## Initial backfill (new and existing installs)

The first time the plugin loads on a fresh install, a **historical backfill** runs in the background — paginating through every Jira issue your install can read, with no time floor. New installs trigger this automatically; existing installs (anyone whose install pre-dates 2026-05-06, including example-tenant) need a one-time click on the **Settings → Historical backfill → Start** button.

While the backfill runs, the Settings tab shows live progress (`1,234 / ~5,000 issues`). The dashboard updates as batches complete; you can leave the page. Hard cap: 50,000 issues per run; sites larger than that can contact support for an extension.

After backfill completes, webhooks (below) keep the dashboard fresh going forward.

## Sync — automatic, with manual escape hatches

The dashboard is **automatically kept in sync** via webhooks (shipped 2026-05-06). When you change a Jira issue's status, assignee, or any tracked field, the dashboard reflects it within ~30 seconds without you doing anything. A daily reconciliation pass catches anything webhooks may have missed.

A "Last sync: 5m ago" indicator next to the buttons shows when the most recent ingest happened — webhook-driven, scheduled-reconciliation-driven, or manual.

The two buttons remain as escape hatches:

- **Refresh now** — does an immediate incremental sync. Use this if you just made a change in Jira and want to verify it reflects, instead of waiting the ~30s webhook delay.
- **Force full** — ignores the last-sync watermark and re-pulls the full 30-day window. Useful after a plugin upgrade that adds new tracked fields (so existing issues backfill them), or to bootstrap sprint data on a recently-installed instance.

Both buttons trigger Jira reads with the user's permissions (Forge `asUser`). The plugin never writes to Jira.

## What loads on first dashboard render

Two tabs:

- **Overview** — one-line top insight, bottleneck breakdown panel, recent alerts list, trends list. Driven by the bottleneck and metrics computations against the chosen time window.
- **Flow** — the three flow charts side-by-side: WIP Aging, CFD, Cycle Time Scatter.

A window picker (currently 7d / 30d / 90d) controls the analytical window for both tabs. WIP Aging is in-flight only and doesn't react to the picker; everything else does.

## What if the dashboard looks empty?

A few possible reasons, depending on where the gap is:

- **The whole page is loading forever.** The backend hasn't responded; check the runbook's "App Runner backend" section.
- **Overview says "Not enough recent activity."** Your project has no completed or transitioned tickets in the chosen window. Widen it (30d / 90d) or open the Flow tab to see what's currently in flight.
- **Flow charts are populated but Overview is empty.** Same root cause: bottleneck detection needs *closed* slices in the window, and your project doesn't have any. The Flow tab works because it uses different signals (in-flight tickets for WIP Aging, all overlapping slices for CFD).
- **Both tabs are empty for a project that should have data.** Click "Force full" once. If still empty after that, the plugin's view of Jira may not include this project — confirm the install scope with your admin.

## What's new in 6.4.0

Four customer-facing additions to be aware of when first exploring the plugin:

- **External-blocking statuses** — mark workflow statuses where work is paused on a third party (e.g., *Blocked*, *Waiting for Customer*) so the bottleneck card stops attributing slowdowns to time your team can't act on. Configure in **Settings** → External-blocking statuses. Default-off; existing dashboards see no change until you opt in. See [`settings.md`](settings.md#external-blocking-statuses-640).
- **Work schedule** — per-tenant timezone + working days + work hours + holidays. When set, cycle time, time-in-status, alert thresholds, and the bottleneck card's time signal all honor working hours instead of wall-clock. Default is 24/7 calendar time (the pre-6.4.0 behavior). Activating or editing a schedule triggers an async recompute of every historical slice; a banner shows progress. See [`settings.md`](settings.md#work-schedule-640).
- **Jira Flow Intelligence issue panel** — per-issue time-per-status data on the Jira issue view. Setup requires a per-issue **Apps** button click per current Atlassian platform behavior; admin-level project-wide setup is documented at [example.com/docs/issue-panel](https://example.com/docs/issue-panel).
- **CSV export** — "Export CSV" button on the dashboard top bar. Output is one row per (issue, slice) with `external_blocking` and `is_terminal` markers, so you can do per-stage analysis in your own spreadsheet.

## Permissions

The plugin uses Forge's `asUser` for Jira reads, meaning it sees exactly what the currently-logged-in Jira user sees. If a user can't see a project in Jira, they can't see it in the plugin either. There's no separate access list to manage on the plugin side.

The backend stores data per **tenant** (the Jira site as a whole) — not per individual user. Two users on the same site looking at the same project see the same numbers.
