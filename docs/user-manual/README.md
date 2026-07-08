# Flow Intelligence — User Manual

The Flow Intelligence Jira plugin turns your team's Jira changelog into deterministic flow metrics, multi-signal bottleneck detection, and threshold/trend alerts. This manual is for the people using and administering the plugin — not the developers building it (see `../engineering/` for that).

## Sections

- [Getting started](getting-started.md) — installing the plugin and seeing your first dashboard.
- [Charts explained](charts-explained.md) — what each chart means, when to look at it, and how to read it.
- [WIP limits](wip-limits.md) — how WIP signals work, why limits matter, and how to configure them.
- [Windows and comparisons](windows-and-comparisons.md) — choosing between day, calendar, and sprint windows.
- [Alerts](alerts.md) — what alert types exist, how to create rules, and how alerts surface.
- [Settings](settings.md) — the per-project / per-tenant configuration available to admins.
- [FAQ](faq.md) — "is it safe to click X twice?", sync paths compared, delete handling, and other recurring questions.
- [Glossary](glossary.md) — flow analytics vocabulary used throughout the product.

## Status of this manual

The manual is built incrementally alongside the product. As of **2026-05-05**:

- ✅ Getting started — installed and live in `example-tenant.atlassian.net`. Auto-sync via webhooks + historical backfill both shipped 2026-05-06.
- ✅ Charts explained — WIP Aging, CFD (with terminal-status exclusion), Cycle Time Scatter all shipped.
- ✅ WIP limits — full configuration shipped. Per-status limits with breach indicators + `wip_breach` alert rule.
- ✅ Windows and comparisons — day-based + MTD/QTD calendar + sprint-bucketed picker all live (shipped 2026-05-05).
- ✅ Alerts — backend pipeline + alert rule UI both shipped (2026-05-06). Email / Slack delivery is a future phase.
- ✅ Settings — full settings tab live: WIP limits, Backfill, Alert rules, Tenant configuration.

The 🚧 sections describe what's planned. They will be updated as features land.

## Audience

- **End users** (Scrum Masters, Product Owners, team leads, engineering managers): "I want to see how my team's flow looks and where work is stuck."
- **Jira admins**: "I'm installing this for my team(s) and configuring the per-project signals."

If you're a developer working on the plugin codebase, the right entry points are `CLAUDE.md` at the repo root and `docs/engineering/`.
