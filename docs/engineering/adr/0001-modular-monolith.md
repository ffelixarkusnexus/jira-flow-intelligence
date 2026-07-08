# 0001 — Adopt a modular monolith with an explicit service layer

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #architecture #backend

## Context and problem statement

The bootstrap spec (`docs/jira_flow_intelligence/03_TECHNICAL_ARCHITECTURE/01_system_overview.md`) prescribes a modular monolith and lays out a service-per-pipeline-stage decomposition: ingestion → transitions → slicing → metrics → insights → alerts. We needed to choose a structure that supports correctness invariants (determinism, idempotency, no-gap/no-overlap) while letting us split components later if traffic or team size demands it.

## Considered options

- **Modular monolith** with thin FastAPI routers delegating to pure-ish service modules
- **Microservices from day one** (one process per pipeline stage)
- **Single flat module** with no service boundaries

## Decision

Adopt the modular monolith with an explicit service layer. Routers in `backend/app/routers/` only orchestrate request/response and delegate work to `backend/app/services/*`. Services depend on the data layer (`backend/app/db/`) and on each other through narrow function APIs, not shared mutable state.

## Consequences

- Positive: One deployable, one schema, no inter-service contracts to version. Fast iteration during MVP.
- Positive: Service modules are independently testable (`backend/tests/test_*` mirrors them). Pure logic (slicing, scoring) needs no DB.
- Positive: Splitting later is mechanical — each `services/*.py` can become a queue worker without rewriting business logic.
- Negative: A bad import edge could let routers reach into the DB directly. We rely on convention; lint doesn't enforce it yet.
- Neutral: Background work (incremental sync, alert sweep) currently runs synchronously via API calls. See the runbook for the deferred scheduler discussion.

## Notes

If/when we add a worker process, this ADR should be superseded with one that defines the queue boundary.
