# 0043 — Work Schedule and working-time duration math

- **Status:** Accepted
- **Date:** 2026-06-06
- **Decision-makers:** the maintainer; Claude Code drafted
- **Tags:** #duration-math #per-tenant-config #recompute-consumer #accuracy-correction
- **Related:** [ADR-0033](0033-backfill-consumer-queue-rebuild.md) (the Forge consumer pattern this mirrors); [ADR-0041](0041-state-based-alert-refire-daily-bucket.md) (alert thresholds this changes the interpretation of); [CLAUDE.md](../../../CLAUDE.md) rules #9 (proactive notification on recompute failure) and #10 (safe-default-first hierarchy)

## Context and problem statement

Every duration in Jira Flow Intelligence today is **calendar time**. A ticket that enters Review on Friday at 17:00 and exits Monday at 09:00 has a calendar duration of ~64 hours and a working duration of ~0 hours. Three consequences:

1. **Cycle-time and time-in-status alert thresholds fire at intuitively-incorrect boundaries.** A `cycle_time > 7d` rule fires at exactly 7×24 calendar hours; users mean *7 working days*.
2. **The bottleneck card's time signal is inflated by weekends and holidays.** A "stuck" status that's actually stuck *over a weekend* scores the same as a status genuinely stuck during business hours.
3. **WIP aging double-counts idle non-working hours.** A ticket sitting in Code Review from Friday 17:00 to Monday 09:00 is aging by ~64 hours under current math; under working-time math it would age by ~0.

Duration math that ignores business hours is inaccurate: it counts nights, weekends, and holidays as active time when no one was working. For the metrics to be accurate — and to match what users mean by "cycle time" and "time in status" — the math must respect working hours.

## Considered options

1. **Calendar time only (current behavior).** Rejected — leaves the three accuracy holes above.
2. **Working-time math with forward-looking-only activation** (new slices use the schedule; historical slices stay calendar-time). **Rejected — load-bearing rejection, documented at length below.**
3. **Working-time math with async recompute of all historical slices on schedule activation / change / disable.** Adopted.
4. **Per-issue manual schedule override.** Rejected — power-user complexity for an unproven need. Single per-tenant active schedule.
5. **Multi-schedule support (one schedule per project, or per team).** Deferred to Tier 3. Storage shape supports it; activation logic is single-schedule for v1.

## Decision

### Schema (additive migration, no breaking change)

```sql
CREATE TABLE work_schedules (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(client_key),
  name TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  working_days_mask SMALLINT NOT NULL,  -- bitfield, Mon=1, Tue=2, ..., Sun=64; Mon-Fri = 31
  work_start_time TEXT NOT NULL DEFAULT '09:00:00',
  work_end_time TEXT NOT NULL DEFAULT '17:00:00',
  holidays JSON NOT NULL DEFAULT '[]',  -- array of ISO date strings
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, name)
);

ALTER TABLE tenants
  ADD COLUMN active_work_schedule_id BIGINT NULL REFERENCES work_schedules(id),
  ADD COLUMN recompute_status TEXT NULL,  -- idle | pending | running | completed | failed
  ADD COLUMN recompute_progress_pct INTEGER NULL,  -- 0-100
  ADD COLUMN recompute_started_at TIMESTAMP NULL,
  ADD COLUMN recompute_error TEXT NULL;
```

### Default behavior

`tenants.active_work_schedule_id IS NULL` → all duration math is calendar time, bit-for-bit identical to today. No existing test changes behavior. New tenants land in this state on install.

### The duration helper (`backend/app/services/working_time.py`)

```python
def working_seconds_between(
    start: datetime,
    end: datetime,
    schedule: WorkSchedule | None,
) -> int:
    """Returns working seconds between start and end.
    If schedule is None or schedule.enabled is False, returns calendar seconds.
    Honors: working_days_mask, work_start_time, work_end_time, holidays, timezone."""
```

Implementation iterates day-by-day, capping each day to the schedule's work window, skipping non-working days and holidays. Uses `zoneinfo` from the standard library — no new dependency. Performance: O(days between start and end), acceptable for the slice durations we compute (max-size tenant ~50k issues × ~10 slices avg = ~500k rows recompute, 3-5 minutes batched).

### Where the helper applies

All current duration callsites are audited and refactored to read the active schedule (if any) and call `working_seconds_between`:

- `slicing_service.build_time_slices` — slice `duration_seconds`.
- `metrics_service` — cycle-time, time-in-status, throughput-windowed metrics.
- `wip_aging` — aging seconds.
- `alert_service.evaluate_alerts` and `evaluate_issue_alerts` — `threshold_seconds` is interpreted as working seconds when a schedule is active.

The interpretation of an existing alert rule's `threshold_seconds` (currently calendar) becomes working-seconds when a schedule activates. This is part of why activation triggers full recompute — alert thresholds and slice durations must use the same math, or thresholds fire incorrectly relative to displayed durations.

### Async recompute on activation / change / disable — the load-bearing decision

When a tenant enables, edits, or disables their schedule, an async recompute job processes every historical `time_slices` row in the tenant, replacing `duration_seconds` with the new schedule's interpretation.

- **Endpoint:** `POST /api/forge/schedule/activate`. Persists the schedule state, sets `tenants.recompute_status = 'pending'`, enqueues a recompute task on a new Forge consumer queue.
- **Consumer:** `recomputeTimeSlicesConsumer` (mirrors `backfillConsumer` from ADR-0033). Paginated: load 1000 rows, recompute, bulk-write, re-enqueue continuation until done. `timeoutSeconds: 900` matches the backfill consumer.
- **Progress:** `tenants.recompute_progress_pct` advances 0→100. Dashboard banner reads it.
- **In-flight handling:** new slices created from incoming Jira transitions DURING the recompute window use the active schedule from the start — they do not queue or wait. Keeps the system responsive.
- **Idempotency:** safe to re-run if interrupted. On crash recovery, the consumer resumes from `recompute_progress_pct`.
- **Failure path:** `recompute_status = 'failed'` + `recompute_error` set; banner shows a Retry button; the existing backfill-failure email path (per ADR-0033 + ADR-0040) is invoked so the maintainer is notified. Matches CLAUDE.md rule #9 (proactive-notification).
- **Dashboard banner:** when `recompute_status IN ('pending', 'running')`, render a non-dismissible banner: *"Recomputing metrics with your new work schedule… {N}% complete. Numbers may temporarily blend old and new math until this finishes."*

### Why forward-looking-only activation is the wrong alternative

Forward-looking activation (new slices use the schedule; historical slices stay calendar-time) seems simpler and avoids the recompute infrastructure. **Rejected because it leaves every tenant — new installs and existing — with permanently-blended math after a schedule change.**

Concretely:
- A tenant with 6 months of history enables a schedule on day 200. From day 200 forward, new slices are working-time; days 1–199 stay calendar-time. The bottleneck card, cycle-time charts, alert thresholds, and trends now operate on a *blend* of two math models indefinitely.
- A tenant who DISABLES their schedule later — same problem in reverse.
- A **new install**'s backfill runs before any schedule is configured, producing calendar-time slices. When the user later enables a schedule, the same blend appears.

The blend is a known-wrong outcome. We will not ship it. Async recompute is the cost of correctness — every tenant lands in a single consistent math model after activation. A few-minute "Recomputing…" banner is honest engineering, not a defect.

### Cost analysis

50,000-issue tenant cap × ~10 slices average = ~500,000 rows max per recompute. Batched Python processing via `working_seconds_between` runs roughly 3–5 minutes for a max-size tenant on the App Runner backend. Acceptable as background work with a visible progress banner. Smaller tenants finish in seconds.

## Consequences

### Positive

- The accuracy claim in the product copy becomes honest: cycle-time, time-in-status, alert thresholds, and the bottleneck card respect business hours, weekends, and holidays.
- Every tenant lands in a **single consistent math model** after activation — no permanent blend.
- The recompute infrastructure reuses the Forge consumer pattern from ADR-0033, including the failure-email pattern. No new operational surface is invented.

### Neutral

- Cycle-time alert with `threshold_seconds = 7 * 86400` under a Mon–Fri 9–17 schedule fires after ~7 *working* days (~11 calendar days). This is the correct behavior. The runbook notes this for operators.
- Default is calendar time (`enabled = FALSE`). Existing installs see no behavior change. Existing tests do not change.

### Negative

- Schedule activation visibly degrades the metrics surface for a few minutes (the banner; the blended numbers underneath). The banner copy frames this honestly; it's not a regression to be hidden.
- A tenant who configures the wrong timezone or working hours sees wrong numbers until they fix the schedule. Same operational risk as any per-tenant config. Settings help text frames it.
- One new consumer module + one new column set on `tenants`. Surface area for ADR-0033's-class quirks (consumer module registration, queue binding, re-consent semantics) re-applies. Documented in the runbook.

## Tests proving the change

In `backend/tests/test_working_time.py` (new):

- `test_calendar_fallback_when_no_schedule` — `schedule=None` returns calendar seconds.
- `test_same_day_inside_work_window` — Mon 10:00 → Mon 11:00 returns 3600.
- `test_friday_5pm_to_monday_9am_returns_zero` — the load-bearing test for the weekend skip.
- `test_holiday_skip` — a holiday between start and end is excluded.
- `test_timezone_correctness` — schedule in `Europe/Madrid`, computation correct relative to local time.
- `test_exact_boundary_edges` — start/end at the exact work_start_time / work_end_time.

In `backend/tests/test_recompute.py` (new):

- `test_recompute_changes_durations_to_working_time` — seed N slices under calendar time, activate schedule, run consumer, verify all `duration_seconds` values match `working_seconds_between`.
- `test_recompute_idempotent_on_crash` — kill the consumer mid-recompute, restart, verify final state matches an uninterrupted run.
- `test_recompute_in_flight_new_slice` — during simulated active recompute, ingest a new transition, verify the new slice is computed under the active schedule AND does NOT block the recompute.
- `test_recompute_failure_path_triggers_email` — simulate malformed schedule, verify `recompute_status = 'failed'` and the failure-email path is invoked.

## Cross-references

- [ADR-0033](0033-backfill-consumer-queue-rebuild.md) — the Forge consumer pattern this mirrors (including failure-email path).
- [ADR-0041](0041-state-based-alert-refire-daily-bucket.md) — alert idempotency model. Alert thresholds now interpreted as working-time when a schedule is active; ADR-0041's UTC-day re-fire bucket is unaffected (re-fire bucket is wall-clock UTC, not working-time).
- CLAUDE.md rule #9 — proactive notification. Recompute failures trigger the existing backfill-failure email path. CLAUDE.md rule #10 — safe-default-first. Default is calendar time (current behavior); the schedule is opt-in.
