# 0004 — Round-trip UTC tz-aware datetimes via a TypeDecorator

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #database #correctness

## Context and problem statement

The slicing engine compares datetimes (transition timestamps vs. `created_at` vs. `now`). If any of those is naive while another is tz-aware, Python raises `TypeError: can't compare offset-naive and offset-aware datetimes`. SQLite stores timestamps as ISO strings without timezone info; SQLAlchemy's `DateTime(timezone=True)` does not magically reattach `tzinfo` on read for SQLite. We discovered this when three e2e tests failed on the first integration run.

## Considered options

- **Normalize everything to naive UTC** at boundaries. Pros: avoids the round-trip problem. Cons: error-prone — a single forgotten conversion crashes a slice.
- **Switch to PostgreSQL**, which preserves tz info natively. Cons: see ADR-0003 — we don't want infra dependency in CI.
- **`UTCDateTime` SQLAlchemy `TypeDecorator`** that always stores UTC and re-attaches `tzinfo=UTC` on read.

## Decision

Use a `TypeDecorator[datetime]` named `UTCDateTime` (`backend/app/db/types.py`). On bind, normalize naive → UTC and aware → UTC-converted. On result, attach `tzinfo=UTC` if missing. Every datetime column uses it. `core/clock.py::utcnow()` returns `datetime.now(UTC)` — never the naive `datetime.utcnow()`.

## Consequences

- Positive: All datetimes flowing through the system are UTC tz-aware by construction. The "can't compare" class of bugs is impossible.
- Positive: Works identically on SQLite and PostgreSQL.
- Negative: A small layer to remember when reading tracebacks involving SQL types. Documented here.
- Neutral: We don't store original timezone info from the Jira API. We don't need to — flow metrics are timezone-agnostic — but if we ever care about local-business-hours, that's a separate ADR.

## Notes

Three tests are guard rails for this decision: `test_pipeline_e2e.py::test_ingestion_e2e_writes_issue_transitions_and_slices` (round-trip), `test_alerts.py::test_cycle_time_rule` (comparison across persisted vs. fresh datetimes), and `test_metrics_engine.py::test_status_window_avg_and_throughput` (DB-driven aggregation).
