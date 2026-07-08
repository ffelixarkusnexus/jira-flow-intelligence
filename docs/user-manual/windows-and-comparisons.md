# Windows and comparisons

The dashboard's metrics — bottleneck score, trends, CFD bands, scatter percentiles — are all computed against a **time window**. This page explains the choices.

## Day-based windows (live)

The picker at the top right of the dashboard offers:

- **7d** — the last 7 days. Tight; useful for active projects with daily completions.
- **30d** — the last 30 days. The default, and the most useful for typical projects.
- **90d** — the last 90 days. Gives the most stable percentiles for cycle time analytics on lower-volume projects.

The picker applies to:

- **Overview tab** — bottleneck panel, trends, alerts list, all-stages table. Always.
- **Flow tab — CFD** — yes (with a 7-day floor; 7d is enforced as the minimum CFD window).
- **Flow tab — Cycle Time Scatter** — yes.
- **Flow tab — WIP Aging** — no. WIP Aging is in-flight only and ignores the window.

## Why the default is 30d

Earlier the default was 7d. Two reasons the default moved to 30d:

1. **Sparseness.** Many `jira:projectPage` installs are on lower-volume projects. A 7-day window with no completions and no transitions has nothing for the bottleneck pipeline to score. The dashboard was rendering "Flow looks healthy" — which sounds reassuring but actually meant "nothing could be computed."
2. **Sample size.** Bottleneck and trend signals compare current-window stats to previous-window stats. At 7d, both windows can be statistically thin; at 30d, both windows usually have enough sample to score reliably.

Users who want the tighter view can still pick 7d; the picker just doesn't *default* to it.

## Comparison framing

The bottleneck card's stats — "Avg time", "WIP" (when configured), "Throughput" — show:

- **Current value** at the top.
- **prior:** the same metric for the previous window (immediately before the current one).
- **% change** in green (improving) or red (worsening).

So if you pick 30d, "prior" is the 30 days *before* the current 30 days. 60 days back to 30 days back, vs. 30 days back to today.

## Calendar windows

Two calendar-based options join the picker:

- **MTD** — month-to-date. Window = the first day of the current calendar month through today. Previous window = the previous calendar month, full.
- **QTD** — quarter-to-date. Window = the first day of the current calendar quarter through today. Previous window = the previous full calendar quarter.

These are useful when stakeholders ask in calendar units ("how was Q1?", "where are we month-to-date?") rather than rolling-day units.

## Sprint windows

> Status: shipped 2026-05-05.

For teams running Scrum on Jira Software, three additional options:

- **Current sprint** — the active sprint for this project. Bounds = sprint start to now.
- **Previous sprint** — the most-recently-closed sprint. Bounds = sprint start to sprint complete.
- **Last 3 sprints** — the most-recent three sprints (one active + two closed, or three closed).

When sprint mode is active, the bottleneck card reframes its comparison from "vs previous window" to "vs Sprint 42" (showing the actual sprint name).

A ticket that spans two sprints contributes to the metrics of both, weighted by which slices of its lifecycle fall within each sprint's bounds. Cycle time for completed tickets is attributed to the sprint in which the ticket was *completed* — matching how Scrum teams typically report velocity.

If your project has no sprints (Kanban-only board), the sprint options are hidden from the picker.
