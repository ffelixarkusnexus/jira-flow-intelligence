# 0041 — State-based alert re-fire via daily UTC bucket (supersedes ADR-0008)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Decision-makers:** the maintainer (maintainer direction); Claude Code drafted the technical change
- **Supersedes:** [ADR-0008 — Alert idempotency via composite key](0008-alert-idempotency-key.md)
- **Tags:** #alerts #idempotency #proactive-push #correctness

## Context and problem statement

ADR-0008 (2026-04-29) locked the idempotency key for `cycle_time` alerts as `f"{issue.id}|cycle"` — no time bucket. Combined with the `UNIQUE(tenant_id, rule_id, issue_id, status, key)` constraint on the `alerts` table, this meant a ticket that breached its cycle-time threshold fired exactly **once, ever**. The reasoning at the time:

> Cycle time is monotone; once exceeded, alerting again on the same issue adds nothing.

This is mathematically true (the elapsed-time NUMBER only grows) but operationally wrong. A team that misses the first notification — Slack-buried, out sick, on holiday — has no recovery path. The ticket is still stuck a week later, but no alert ever fires again for it. The `trend` rule's daily "things are worsening" alert tells the operator *something* is wrong, not *which ticket* to look at.

On 2026-06-05 the maintainer observed exactly this gap on the test board: Monday and Tuesday produced per-ticket cycle-time alerts; Wednesday and Thursday produced only worsening trend alerts, even though the same tickets were still stuck. The fire-once-then-silent pattern was the bug.

### The flaw was systemic, not cycle_time-specific

Two other rule types had the same architectural shape under ADR-0008:

| Rule | ADR-0008 key | Why it never re-fires |
|---|---|---|
| `cycle_time` | `f"{issue_id}\|cycle"` | No time component — fires once per issue, ever |
| `status_duration` | `f"{issue_id}\|{status}\|{slice_start_ts}"` | `slice_start_ts` is fixed while the issue sits in one status — re-fires only when the issue leaves and re-enters the status |
| `no_activity` | `f"{issue_id}\|noactivity\|{last_event_ts}"` | `last_event_ts` is fixed while there's no activity — by definition can't advance on an idle ticket, which is the very condition the rule exists to flag |

All three are **state-based** rules — they describe a condition that persists in time ("ticket is stuck", "status held too long", "no activity for N days"). Keying their idempotency by an **event timestamp** (slice start, last event) or by **nothing** at all (cycle_time) caused the fire-once-then-silent pattern across all three. Only `wip_breach` (window-bucketed) and `trend` (hourly-bucketed) re-fired correctly — they keyed their idempotency by *time*, which is what state-based rules need.

## Considered options

1. **Fire on every evaluation cycle.** Rejected — alert fatigue. A daily sweep would push the same N stuck tickets every day, then every hour-tier sweep would push them again. Operators would mute the channel.
2. **Fire on discrete escalation thresholds (7d, 14d, 30d).** Rejected — discontinuous and hard to predict from a user's perspective ("why didn't I get one yesterday at day 9?"). The right shape for paging-style escalation, wrong for flow alerts.
3. **Keep fire-once with explicit acknowledge/snooze UI.** Real product work — UI + state machine + dispatch interaction — and the operational problem can't wait for that. Surfaced as a follow-up in §Future considerations.
4. **Daily UTC bucket — chosen.** Idempotency key includes `utc_date_iso`. One alert row per ticket per UTC day per breaching condition. Matches operational rhythm; gives missed-notification recovery (next day's alert re-surfaces the breach); avoids the fatigue of per-evaluation firing.

## Decision

**All state-based ticket-level rules** (`cycle_time`, `status_duration`, `no_activity`) include `utc_date_iso` (YYYY-MM-DD in UTC at evaluation time) as a component of their idempotency key.

| Rule | New key shape |
|---|---|
| `cycle_time` | `f"{issue_id}\|cycle\|{utc_date_iso}"` |
| `status_duration` | `f"{issue_id}\|status_duration\|{status}\|{utc_date_iso}"` — `status` is preserved because a ticket can legitimately re-breach in a different status |
| `no_activity` | `f"{issue_id}\|no_activity\|{utc_date_iso}"` |

**`trend` is untouched.** Its hourly bucket (`int(now.timestamp() // 3600)`) is the correct cadence for a project-level signal and predates this ADR as the existing correct pattern. This ADR generalizes the trend rule's approach to the broken cases, not the other way around.

**`wip_breach` is untouched.** Audit finding (2026-06-05): the existing `window_start.timestamp()` bucket aligns with the customer-configured `breach_minutes` cadence per ADR-0037's cadence-tiering model — a customer who sets `breach_minutes = 60` (1h) gets hourly evaluation by design ("a rule the customer sets to a *short* threshold is the one that genuinely needs frequent checking"). Forcing a daily bucket on `wip_breach` would defeat that customer-tuned cadence. Wip_breach is also project-level (no `issue_id`), not ticket-level, so it's outside this ADR's scope by definition.

**Idempotency cadence is hard-coded daily in v1.** Per CLAUDE.md rule #10 (best-in-category defaults), the safe default for 95% of the workflow shape — daily — ships now without configuration overhead. Per-rule configurable cadence is named under §Future considerations.

## Consequences

### Positive

- A perpetually-stuck ticket now re-surfaces once per UTC day until it stops breaching. The missed-notification recovery path the maintainer identified as missing is now in place.
- The principle is unified across the three rule types instead of three different broken approaches. Future contributors can reason about idempotency once.
- The `trend` rule's existing correct pattern is now the documented template, not the exception.

### Behavior change visible to operators on first deploy

The first evaluation after deploy will see no matching `{issue.id}|cycle|{today}` row for currently-stuck tickets (their old `{issue.id}|cycle` rows from ADR-0008 don't match the new key shape — verified by `test_legacy_adr0008_alert_row_does_not_collide_with_new_daily_bucket`). Result: every currently-breaching ticket re-fires once on deploy day as a single batch. **This is intentional and announces the behavior change** — operators don't have to wonder "did the new keying actually take effect?", they see a re-surfaced batch on day one.

A ticket stuck for 14 days under the new model will produce ~14 alert rows over its lifetime (one per UTC day) instead of one ever. Storage cost: negligible at single-tenant volumes — `alerts` rows are small JSON payloads. Worth re-evaluating if multi-tenant scale shows the table growing faster than expected; not a current concern.

### Neutral

- The `alerts` table UNIQUE constraint shape `(tenant_id, rule_id, issue_id, status, key)` is unchanged. The `key` field gets a different value pattern; everything else (including `_persist`'s NULL-issue_id-equivalent existence check) is unaffected.
- No migration of historical rows. Old ADR-0008 keys stay in the table as audit trail. This makes the deploy a one-way ratchet — once shipped, the new key shape is what runs; rollback would lose nothing but would re-introduce the gap.

### Negative

- An operator who acknowledged a stuck ticket on Monday and consciously chose to live with it for the rest of the week now sees it again Tuesday through Friday. The right UX response is acknowledge/snooze (see §Future considerations); the daily cadence is the v1 fix that closes the worst gap without introducing new UI surface.

## Future considerations (explicitly deferred)

- **Per-rule configurable cadence.** Add `cadence_seconds` (or similar) to `alert_rules.config` once customer data shows demand. Per CLAUDE.md rule #10, this is an advanced opt-in, not a default-visible setting. Daily-default for 95% of users; the 5% who want a different cadence reach for the lever.
- **Acknowledge / snooze UI.** Customer-visible way to tell the system "I see it, stop alerting until X happens." Real product work — UI + state machine + dispatch interaction. Out of scope for this fix.
- **Whether to re-evaluate a known-acknowledged ticket at all.** Tied to ack/snooze above. If the ack record exists and is still valid, skip the rule for that ticket entirely; don't even compute the breach.

## Guardrail for future contributors

If a new rule type is added that genuinely **is** event-based — fires on a single discrete transition that never recurs (e.g., "ticket completed under SLA", "deploy succeeded") — use a key that does NOT include `utc_date_iso`, and document the reason explicitly in the rule's evaluator docstring. The default expectation for any new rule going forward is daily-bucketed state-based keying; opting out requires a written reason.

## Tests proving the change

In `backend/tests/test_alerts.py`:

| Test | What it proves |
|---|---|
| `test_cycle_time_refires_next_utc_day` | Same ticket, T0=23:55 UTC and T1=00:05 UTC next day → 2 alert rows with `...\|2026-06-10` and `...\|2026-06-11` keys |
| `test_cycle_time_does_not_refire_same_utc_day` | Two evaluations within one UTC day → 1 alert row |
| `test_status_duration_refires_next_utc_day` | Same UTC-day-boundary pattern for `status_duration` |
| `test_no_activity_refires_next_utc_day` | Same pattern for `no_activity` — proves the fix specifically for the case ADR-0008's `last_event_ts` key couldn't handle (idle tickets can't advance their last_event) |
| `test_ticket_that_stops_breaching_mid_day_does_not_fire` | Tickets that aren't breaching don't fire just because it's a new UTC day — the daily bucket only matters when the breach is present |
| `test_legacy_adr0008_alert_row_does_not_collide_with_new_daily_bucket` | A pre-migration `{issue_id}\|cycle` row in the alerts table does not block a new `{issue_id}\|cycle\|{date}` row from being inserted — proves the no-migration approach is safe |
| `test_perpetually_stuck_ticket_yields_one_row_per_utc_day` | 7-day simulated integration: seven consecutive daily evaluations on a stuck ticket → exactly 7 alert rows, one per day, keys form `...\|2026-06-10`, `...\|2026-06-11`, … `...\|2026-06-16` |

The legacy-row test is the load-bearing one for "no migration needed" — it proves the pre-ADR-0041 rows stay where they are without colliding with the new key shape.

## Notes

ADR-0008 stays in `docs/engineering/adr/` with a SUPERSEDED banner pointing at this ADR. The historical reasoning is preserved as the artifact of when it was the right call (and why it later wasn't). Future contributors reading the alerts subsystem should read this ADR; ADR-0008 is the audit trail of the design we learned wrong from.
