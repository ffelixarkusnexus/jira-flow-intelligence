# WIP limits

> Status: shipped 2026-05-05. Configure under Settings tab on any project page.

## Why bare WIP averages don't help

Earlier versions of the dashboard showed a "WIP (avg) 0.45" figure on the bottleneck panel. That number tells you *the average count of in-flight tickets in the bottleneck stage during the time window*, but it can't tell you whether that's good, bad, or normal — because it has no reference point.

Is `0.45` "good" or "bad"? Without a reference limit, it's just a number. WIP in a Kanban / Lean sense is meaningful only when you compare *actual* WIP to a *configured* limit. `5 / 3` means "this stage is 67% over its limit" — that's a decision. `0.45` is just a number.

## How to configure

Open any Jira project's Flow Intelligence page → click the **Settings** tab. You'll see a per-status limit table:

- **Status** — the workflow stage (datalist autosuggests the statuses Flow Intelligence has discovered for this project).
- **Scope** — `project <KEY>` for project-specific limits, `tenant-wide` for defaults that apply across all projects on the site.
- **Max in progress** — the cap. Edit inline; saves on blur.
- **Breach minutes** — controls the alert pipeline. `0` = visual indicators only (no alerts). Positive values enable the `wip_breach` alert rule type.

Project rows override tenant-wide rows for that status. Removing a limit row drops the configuration; the placeholder reappears on the bottleneck panel until you reconfigure.

## What changes when a limit is set

- **Bottleneck panel** — the WIP card switches from "Configure limits" placeholder to `current / limit`. Red border when over limit; amber when ≥80% of limit.
- **All-stages table** — the WIP column renders `current / limit` per row; over-limit rows get a red highlight.
- **WIP Aging chart** — per-status reference line at the limit value.
- **Alerts** — when `breach_minutes > 0`, a `wip_breach` rule for that (project, status) fires when the window's average WIP exceeds the configured limit. Idempotent per evaluation window.

## How to think about WIP limits

Some heuristics teams use to choose a starting limit:

- **Per-stage rule of thumb:** `team_size × 0.5` to `team_size × 1.0`. A team of 6 might set Code Review = 4, In Progress = 6.
- **Cycle-time-driven:** if your average cycle time is 5 days and you ship 3 tickets/day, your sustainable WIP per stage is roughly 15 ÷ N stages. Tight limits force smaller batches and faster flow — Little's Law.
- **Pull-system:** start with the team's *current* peak WIP per stage, then ratchet down by 20% over a sprint or two and watch what breaks. Flow tends to *improve* as WIP tightens, up to a point.

There is no universally correct WIP limit. The plugin's job is to *show* you the breach, not pick the limit for you.

## How alerts work

A `wip_breach` rule looks like:

```
Status: Code Review
Project: VPST  (or "all projects" for a tenant default)
Sustained minutes: 60
```

This says: "Fire an alert when WIP in Code Review on the VPST project exceeds the configured limit for 60 minutes straight." The "sustained" guard prevents flap-on-flap-off noise from short spikes.

Alerts are idempotent per `(rule, status, breach_started_at)` — the same breach won't re-fire repeatedly, but a new breach (after WIP drops back below the limit) will trigger again.

## What if my workflow has too many statuses to set limits on every one?

Set limits only on the stages that matter. Stages without a configured limit show neither breach indicators nor placeholders — they render normally without WIP context. The plugin doesn't require comprehensive configuration; partial configuration is supported and useful.
