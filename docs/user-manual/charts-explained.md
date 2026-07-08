# Charts explained

Three flow charts make up the Flow tab. Together they answer three different questions:

| Chart | Question it answers | Best signal |
|---|---|---|
| **WIP Aging** | *Right now, where is in-flight work stuck?* | Bubbles further to the right are aging out of normal flow. |
| **CFD** | *How is work piling up over time?* | Bands that bulge as you scan left-to-right indicate accumulation in that stage. |
| **Cycle Time Scatter** | *Which completed tickets took longer than usual?* | Dots above the P95 line are outliers; dots above P50 take longer than the median. |

## WIP Aging

> "Aging Work-In-Progress" — industry-standard pattern.

Every in-flight ticket (any ticket with `done_at IS NULL`) is one bubble. The chart's axes:

- **X-axis: days in current status.** A bubble at X=12 has been sitting in its current status for 12 days. Bubbles further right have been stuck longer.
- **Y-axis: status.** Each row is one workflow stage.
- **Bubble size:** story points if available, otherwise priority weight (Highest=8 / High=5 / Medium=3 / Low=2 / Lowest=1).
- **Bubble color:** assignee. A grey bubble = unassigned.
- **Vertical reference line (red):** P95 of cycle times for tickets completed in the last 90 days. Tickets to the right of this line are aging *beyond* what normally takes to finish a whole ticket — strong signal that they need attention.

**How to read it:**

- A row with mostly small, short-distance bubbles is healthy.
- A row with one or two long-distance bubbles = a few stuck tickets. Click the bubble to open the ticket in Jira.
- A row with many bubbles all aging right of the P95 line = a stage that's struggling broadly, not just one ticket.

**Filters available:**

- Assignee dropdown — narrow to one person's workload.
- (More filters ship with settings.)

## Cumulative Flow Diagram (CFD)

A stacked area chart over the chosen time window. Each band is one workflow status; the band's height on a given day = the number of distinct issues that were in that status at end-of-day.

**The key visual signals:**

- **Bands widening over time** = work piling up in that status faster than it leaves. This is the classic CFD bottleneck signature.
- **Bands narrowing over time** = work clearing out (good) or, less ideally, work being deleted/archived (also good for the chart, but worth checking why).
- **Hovering** any day shows the per-status breakdown for that day in a tooltip.

**Terminal statuses are excluded.** Done, Won't Do, Cancelled, Rejected, Closed, Resolved — these don't appear on the chart. The CFD's purpose is to show work *flowing*; once a ticket is terminal, it has left the pipeline. Including those bands would just show a perpetually-growing pile that visually drowns the actual in-flight stages.

If your tenant uses different terminal status names (e.g., "Archived" or "Out of Scope"), an admin can override the default list under `tenants.terminal_statuses` (surfaced in the settings UI).

## Cycle Time Scatter

One dot per *completed* ticket in the chosen time window. The axes:

- **X-axis: completion date.**
- **Y-axis: cycle days** — wall-clock days from `created_at` to `done_at`.
- **Dot color:** issue type (Story=blue, Bug=red, Task=green, Epic=purple, Sub-task=cyan, anything else=grey).
- **Dashed overlay lines:** P50 (grey), P85 (amber), P95 (red) of all the dots in the window.

**How to read it:**

- Most dots concentrated near or below P50 = healthy cycle time discipline.
- Many dots above P85 = your team has a long tail of slow-moving work; the bottleneck panel will say where that work is stuck.
- Outliers above P95 = candidates for retrospective discussion. Click any dot to open the ticket in Jira.

The scatter only includes completed tickets (`done_at IS NOT NULL`). In-flight work is on the WIP Aging chart instead.

## Why three charts?

WIP Aging shows the **present** (what's in flight right now). CFD shows the **trend** (how the pipeline has shaped itself over time). Scatter shows the **outliers** (which finished tickets behaved unusually).

A team using one of these in isolation gets a partial picture. All three together = the canonical flow analytics view.

## 6.4.0 changes — working hours and external-blocking attribution

Two configurable behaviors from 6.4.0 affect what the charts and the bottleneck card show. Both default-off, so existing installs see no change until you configure them.

### When a [work schedule](settings.md#work-schedule-640) is active

- The **bottleneck card's time signal** (the *"avg time grew +X% vs. prior window"* part) is computed in **working hours**, not calendar hours. A status that's stuck only over weekends doesn't inflate the time signal anymore.
- **Cycle time, time-in-status, WIP aging** numbers everywhere — chart axes, alert evaluation, the bottleneck card — all honor working hours. A ticket that entered Review on Friday 17:00 and exited Monday 09:00 reads as **0 hours** in Review under a Mon–Fri 9–5 schedule, not ~64.
- When you save (or change, or disable) a schedule, every historical slice is recomputed under the new math so the charts don't blend old calendar-time and new working-time numbers. A "Recomputing metrics…" banner sits at the top of the dashboard while this runs.

### When [external-blocking statuses](settings.md#external-blocking-statuses-640) are configured

- The **bottleneck card stops naming** those statuses as "the bottleneck" even when their raw signal would otherwise dominate. The card answers *"where is the team's controllable bottleneck?"* — time the team can't act on doesn't drive attribution.
- **Time-by-status charts still show the band.** The Cumulative Flow Diagram still surfaces the duration. Per-issue history (in the Issue Panel) still surfaces it. Only the attribution surface — *which* status gets named — is affected.

Both features are opt-in. Their absence preserves the pre-6.4.0 chart behavior exactly.
