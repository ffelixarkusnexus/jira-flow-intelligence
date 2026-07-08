# 0008 — Alert idempotency via composite key

- **Status:** SUPERSEDED 2026-06-05 by [ADR-0041 — State-based alert re-fire via daily UTC bucket](0041-state-based-alert-refire-daily-bucket.md)
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #alerts #correctness

> **SUPERSEDED 2026-06-05.** The fire-once-per-issue keying for `cycle_time`
> (and the event-anchored keying for `status_duration` / `no_activity`)
> caused operationally-wrong fire-once-then-silent behavior for stuck
> tickets. ADR-0041 replaces those three rules' key shapes with a UTC-date
> bucket so state-based breaches re-fire once per day. The historical
> reasoning below is preserved as audit trail; do NOT rely on this ADR for
> current behavior. See ADR-0041 for the current model.

## Context and problem statement

`docs/jira_flow_intelligence/07_ALERTING_SYSTEM` requires deterministic, non-duplicating alerts. The same evaluation, run twice, must not double-alert. But re-evaluating after a state change (e.g., a slice's duration grew) should still produce a new alert if the situation is genuinely new.

## Considered options

- **One alert per (rule, issue) forever.** Simple but loses the ability to re-alert when an issue re-enters a status.
- **Time-bucketed dedupe** (one alert per (rule, issue, status, hour)). Smooths noise but feels arbitrary.
- **Composite key including a state fingerprint.** Re-fires only when the underlying state changes.

## Decision

`alerts` table has `UNIQUE (rule_id, issue_id, status, key)`. The `key` field is rule-type specific:

- `status_duration` → `f"{issue_id}|{status}|{int(slice_start_at.timestamp())}"` — one alert per slice. A new slice (re-entry into the status) gets its own key.
- `cycle_time` → `f"{issue_id}|cycle"` — one alert per issue, ever. Cycle time is monotone; once exceeded, alerting again on the same issue adds nothing.
- `no_activity` → `f"{issue_id}|noactivity|{int(last_event_at.timestamp())}"` — re-fires when the "last activity" timestamp advances.
- `trend` → `f"trend|{metric}|{status_or_*}|{int(now.timestamp() // 3600)}"` — one alert per metric+status per hour, system-level.

On insert, `IntegrityError` is caught and treated as "already triggered, skip" (`alert_service._persist`).

## Consequences

- Positive: `test_alerts.py::test_alerts_are_idempotent` proves it: two runs against unchanged state produce 0 new alerts on the second run.
- Positive: Each rule type's key shape is in one place, easy to reason about.
- Negative: The trend key buckets to the hour — running the evaluator twice in the same hour against the same trend is a no-op, but the next hour re-fires even if nothing changed. Acceptable noise floor; deferred refinement.
- Neutral: This is data-layer dedupe. If we later add a notification channel (Slack, email), the channel layer will need its own dedupe (don't email twice) — that's a separate concern.

## Notes

If the trend bucket size ever feels wrong in practice, the right fix is to make it a Setting, not to invert the rule.
