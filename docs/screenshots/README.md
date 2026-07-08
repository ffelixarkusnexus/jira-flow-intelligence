# Screenshots

A curated 6-shot set of the dashboard, suitable for an Atlassian Marketplace
listing. All shots were captured against the **deterministic demo seed** (the
`marketplace` fixture: 250 issues, 5 sprints, a designed Review-stage bottleneck
firing all five alert rules) on a throwaway developer site. Branding and the
real Cloud ID have been replaced with neutral placeholders; the data itself is
the synthetic demo dataset.

Capture spec: 1840×1080, no browser chrome, no URL bar.

## Captions

The Marketplace listing form lets you attach a one-line caption per image.

| # | File | Caption |
|---|---|---|
| 1 | `01-overview-bottleneck-wip-breach.png` | "Where's the bottleneck? One sentence, every time — with a WIP-limit indicator built in." |
| 2 | `02-tenant-configuration.png` | "Configure for any Jira workflow — not just vanilla Scrum. Per-tenant active / done / terminal status sets, plus the bottleneck-detector thresholds." |
| 3 | `03-wip-aging.png` | "What's stuck right now — every in-flight ticket, by stage and aging. Click any bubble to open the issue in Jira." |
| 4 | `04-cumulative-flow.png` | "How work is piling up over time. Bands widening = trouble. Hover any day to see the exact distribution." |
| 5 | `05-cycle-time-scatter.png` | "Which finished tickets took longer than usual. Outliers above P95 jump out instantly." |
| 6 | `06-alert-rules.png` | "Five alert types, all configurable. Stuck tickets, breached cycle times, idle work, worsening trends, WIP breaches." |

## What each one shows

### 01 — Overview (7d window) with WIP-breach indicator

The bottleneck card identifies Review with HIGH CONFIDENCE, citing a 62% time
increase and a 187% WIP surge. The Bottleneck Breakdown row underneath shows the
WIP card in red — "16.1 / 15 · 7% over limit" — so the WIP-limit feature is
visible, not just described in copy.

### 02 — Settings → Tenant configuration

Custom active / done / terminal status sets are visible, showing the app isn't
hardcoded to vanilla Scrum and handles non-standard workflows. The
bottleneck-detector thresholds (time-ratio, WIP-ratio, throughput-delta)
underneath show the scoring is configurable.

### 03 — WIP Aging chart

50 in-flight tickets plotted by stage (Todo / In Progress / Review), X = days in
current status. The hover tooltip on a stuck Review ticket demonstrates that
bubbles are clickable and surface issue context. Color = assignee; bubble size =
story points. (The assignee names in the tooltips are synthetic demo-seed data.)

### 04 — Cumulative Flow Diagram (7d window)

Bands widen over the week, with the Review band accelerating up the chart. The
hover tooltip shows the exact split for a given day — confirming the bottleneck
shape numerically.

### 05 — Cycle Time Scatter (7d window)

23 done tickets over the window. A base distribution around ~1d with three clear
outliers above the P95 dashed line. The hover tooltip shows a ticket's type,
priority, and assignee (synthetic demo-seed data) and links to Jira.

### 06 — Settings → Alert rules

All five default alert-rule types with their config JSON: status_duration,
cycle_time, no_activity, trend, wip_breach. Template cards underneath show how to
add more.

## Recapturing

The demo seed is deterministic (`random.Random(42)`), so re-running produces the
same dataset and the same chart shapes — screenshots reproduce across recaptures.

1. Install the app on a developer Jira site you administer.
2. Enable the seed endpoint (`ALLOW_DEMO_SEED`) on the backend, then open the
   dashboard → Settings → **Load demo data**.
3. Refresh the dashboard and capture each frame at 1840×1080, no browser chrome.
4. Disable `ALLOW_DEMO_SEED` again afterward.
5. Replace any branding / real Cloud ID with placeholders before committing.
