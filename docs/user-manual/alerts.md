# Alerts

> Status: alert rule UI shipped 2026-05-06. Alert delivery (email / Slack) is a future phase.

## What's live now

The backend evaluates alert rules against the current vs. previous window every time the alerts pipeline runs. Alerts that fire are persisted and surfaced in the dashboard's "Recent alerts" panel on the Overview tab.

**Configuring rules.** Open any project page → **Settings tab → Alert rules**. Five rule types are available as one-click templates: ticket-stuck-in-status, cycle-time-exceeded, no-activity, trend-worsening, and WIP-breach. Click a template to populate a form with sensible defaults; tweak the threshold/status/metric and save. Rules can be edited, disabled (kept but not fired), or deleted.

Alerts are scoped per tenant. WIP-breach rules can be scoped to a specific project; the others fire system-wide on the watched signal.

## Rule types (today)

- **`status_duration`** — fires when an issue has been in a specific status longer than a threshold. Config: `{status, threshold_seconds}`.
- **`cycle_time`** — fires when a completed issue's total cycle time exceeded a threshold. Config: `{threshold_seconds}`.
- **`trend`** — fires when a metric (cycle time, throughput) trended worse than a configured ratio between current and previous window. Config: `{metric, status?, threshold_ratio}`.

## Rule types coming soon

- **`wip_breach`** — fires when WIP in a status stays over the configured limit for `sustained_minutes`. Requires a `wip_limit` row to exist for the (project, status). Idempotent per breach window.

## Idempotency

Every rule type is idempotent per its natural key. The same condition won't repeatedly fire alerts on every evaluation pass — the same `(rule_id, issue_id, status, breach_started_at)` (or equivalent for the rule type) writes once and is skipped on subsequent passes until the underlying condition resets.

This is the load-bearing guarantee that lets you call `/api/alerts/evaluate` repeatedly (or in a scheduled trigger) without flooding alert lists.

## Tenant + project scoping

Alerts are stored per tenant. Issue-level alerts also carry the `issue_id`, which lets the dashboard filter to the active project (alerts for issues in another project are hidden from the project page). Status / trend alerts that don't carry an `issue_id` surface tenant-wide today; once the alert row itself can carry `project_key`, those will scope to the active project too.

## Send-on-trigger destinations

Alerts are visible in the dashboard. They are *not* sent anywhere — no email, no Slack, no webhook. Send-on-trigger is out of scope for P3 and will get its own ADR before being introduced (likely as a separate notification service).

## Working-time thresholds (6.4.0)

When a [work schedule](settings.md#work-schedule-640) is active for your tenant, alert thresholds are interpreted in **working hours**, not calendar hours.

So a `cycle_time` threshold of **7 days** under a Mon–Fri 9–5 schedule fires after ~7 **working** days (about 11 calendar days), not 7 calendar days. Same for `status_duration` (stuck-in-status) and `no_activity` thresholds. The mental model: when you configure a `cycle_time > 7d` rule, you're saying "alert me when a ticket has actually been working for more than a week" — not "alert me when wall-clock has elapsed for a week regardless of whether anyone was working."

When no schedule is configured, thresholds are calendar-time (the pre-6.4.0 default — bit-for-bit unchanged).

The `trend` rule type is unaffected by the schedule — it compares windowed metrics, and the windows themselves are wall-clock. `wip_breach` is also unaffected; it fires on the rule's own evaluation cadence per the customer-configured `breach_minutes`.

Cross-reference: [`settings.md` → Work schedule (6.4.0)](settings.md#work-schedule-640) for the schedule configuration itself.
