# FAQ

## Is it safe to click "Refresh now" / "Force full" / "Start historical backfill" twice?

**Yes — every sync path is safe to re-run.** The plugin will not duplicate tickets, transitions, sprints, or any other data. Concretely:

- Issues already in the database get **updated in place** if they changed in Jira since the last sync; otherwise they're **skipped** and consume no extra work.
- Transitions and time-slice history for changed issues are atomically replaced (the old set is removed and the new one inserted in one operation).
- Brand-new tickets (created in Jira since the last sync) are pulled in.

The same guarantees apply whether the trigger is automatic (webhooks, daily reconciliation) or manual (the buttons on the dashboard).

## What's the difference between Refresh now, Force full, and the historical backfill?

| Action | Where | What it does | When to use |
|---|---|---|---|
| **Refresh now** | Header buttons | Pulls only issues *changed since the last sync* (tracked by an internal timestamp). Cheap. | When you want to verify a recent Jira change reflects immediately, instead of waiting ~30 seconds for the webhook to land. |
| **Force full** | Header buttons | Pulls every issue in the **last 30 days** regardless of timestamp. | After a plugin upgrade that adds new tracked fields (story points config change, sprint custom-field change, etc.), so existing tickets pick up the new data. |
| **Historical backfill** | Settings tab → Historical backfill | Pulls every issue your install can read, **with no time floor**. | First-time setup on a brand-new install, or when you want full Jira history (older sprints, longer cycle-time scatter, etc.) reflected on the dashboard. |

In day-to-day operation you should rarely need any of these — webhooks keep the dashboard in sync within ~30 seconds of a Jira change, and a daily reconciliation pass catches anything webhooks miss.

## Will running a sync delete tickets that I deleted in Jira?

**Webhooks remove deleted tickets automatically.** When you delete a ticket in Jira, the plugin receives the `avi:jira:deleted:issue` event and removes the corresponding rows from its database within seconds.

**A manual sync (Refresh now / Force full / backfill) will not detect deletions.** The plugin's sync queries Jira for issues that exist; deleted issues don't appear in the response, so the plugin has no signal that they ever existed. If a delete event was missed (rare — Forge can drop events under load), the orphan row remains until either:
- The same ticket key is re-created in Jira (the plugin treats it as an update).
- A future delete-reconciliation feature is built (tracked internally; not yet shipped).

If you ever notice a ticket on the dashboard that you've deleted in Jira and you want it removed immediately, contact support — manual cleanup is a database operation that can be performed on request.

## What happens if I close the tab while a backfill is running?

The backfill runs as a chained loop in your browser tab. **Closing the tab pauses progress** at the most recent completed batch. Reopening the dashboard and clicking the button again resumes from where it left off. The browser will warn you with a "Leave site?" prompt before letting you close the tab while a run is in progress.

The webhook + reconcile sync paths run server-side in Forge and do not depend on the dashboard being open.

## Why is my dashboard showing "no bottleneck" when I expect one?

The bottleneck pipeline needs **completed slices** in both the current and previous time window to compute a comparison. Two cases produce a "no bottleneck":

- **Healthy flow.** No stage shows a significant slowdown vs. the prior window. The InsightCard will say *"Flow looks healthy."*
- **Sparse data.** No tickets completed or transitioned in the window. The InsightCard will say *"Not enough recent activity. Open the Flow tab to see what's currently in flight."* — try a wider window from the picker (30d / 90d / MTD / QTD), or open the Flow tab where the WIP Aging chart shows in-flight work regardless of the window.

## Where should I report issues or feature requests?

Open an issue at the repository's GitHub Issues page, or email the support address listed under "Support" in the Atlassian Marketplace listing once the app is publicly listed.

## Why does the same issue key appear on multiple rows of the CSV export?

That's the per-slice row model, not a duplicate-row bug. The CSV emits **one row per (issue, slice)** — every status the issue passed through becomes its own row. A ticket that moved In Progress → Review → Done gets three rows, all sharing the same `issue_key` but with different `slice_status`, `slice_start_at`, `slice_end_at`, and `slice_duration_seconds`.

This is the shape that lets you do per-stage analysis in your own spreadsheet (sum durations by status, pivot by `external_blocking`, filter by `is_terminal`). If you want one row per issue, group by `issue_key` and pick the columns you want to aggregate.

## Why doesn't the Jira Flow Intelligence panel appear automatically on every Jira issue?

Forge issue panels (which is the technology Jira Flow Intelligence uses for the per-issue panel) are hidden by default until a user adds them via the issue's **Apps** button. This is current Atlassian platform behavior — Atlassian tracks the limitation as [FRGE-734](https://ecosystem.atlassian.net/browse/FRGE-734) and they're working on an official solution.

A project admin **can** pre-enable the panel across a JQL-selected set of issues today using a JavaScript snippet Atlassian's developer community published — see [example.com/docs/issue-panel](https://example.com/docs/issue-panel) for the full setup guidance and links to Atlassian's published admin guide.

When Atlassian ships FRGE-734, the manual Apps-button step goes away platform-wide and Jira Flow Intelligence picks it up automatically.

## How does enabling a work schedule affect my historical numbers?

When you save a work schedule (timezone, working days, work hours, holidays) — or change one, or disable it — Jira Flow Intelligence runs an **async recompute** of every historical `time_slices` row for your tenant, rewriting `duration_seconds` to match what the working-time math produces against the new schedule. While the recompute runs, a banner at the top of the dashboard reads *"Recomputing metrics with your new work schedule…"* with a progress percentage. The banner auto-dismisses when recompute completes.

This design is deliberate. The alternative — applying the schedule only to *new* slices going forward — would leave you with a permanent blend: historical slices in calendar time, new slices in working time. Charts and alert thresholds would operate on a mixed math model indefinitely. Async recompute is the cost of correctness: every tenant lands in a single consistent math model after activation.

The recompute is idempotent — safe to re-run, safe to interrupt and resume. If you change your mind mid-day and disable the schedule, durations get recomputed back to calendar time and you're back where you started.

Cross-reference: [`settings.md` → Work schedule (6.4.0)](settings.md#work-schedule-640).
