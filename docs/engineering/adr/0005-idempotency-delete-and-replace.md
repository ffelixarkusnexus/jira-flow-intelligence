# 0005 — Idempotency via per-issue delete-and-replace

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #correctness #data-pipeline

## Context and problem statement

Section "Idempotency Rule" of `docs/jira_flow_intelligence/03_TECHNICAL_ARCHITECTURE/03_data_pipeline_design.md` states: "running the pipeline multiple times MUST produce identical results, not duplicate rows." The same payload ingested twice — or two payloads where the second is a refreshed version of the first — must end with the same DB state.

Strategies considered for transitions and time slices.

## Considered options

- **Insert-or-ignore via `UNIQUE` constraint** (`(issue_id, transitioned_at, to_status)` already exists). Cons: leaves orphan rows when the source data shrinks (e.g., a transition is corrected to a different `to_status`).
- **Per-issue `DELETE WHERE issue_id = ?` then bulk `INSERT`.** Pros: trivially correct, the latest extraction is the single source of truth. Cons: brief window where the issue has no transitions — only matters if a reader reads at exactly that instant.
- **Diff-and-patch** (compute the delta, apply UPSERT/DELETE for only changed rows). Pros: minimal writes. Cons: complex; not worth the savings for our volumes.

## Decision

Per-issue delete-and-replace, inside a single transaction so readers never see the empty intermediate state. Implemented in `transition_service.replace_transitions()` and `slicing_service.replace_time_slices()`. The `UNIQUE(issue_id, transitioned_at, to_status)` constraint on `transitions` stays as a defense-in-depth check.

## Consequences

- Positive: Trivial to reason about correctness. `test_pipeline_e2e.py::test_ingestion_idempotent_running_twice_produces_same_state` proves it for the round-trip case.
- Positive: Works for the "shrinking" case — if Jira corrects an entry, our DB shrinks too. Diff-and-patch would have required extra logic for this.
- Negative: Worst-case write amplification on issues with hundreds of transitions. At realistic Jira volumes (tens of transitions per issue), this is noise.
- Neutral: Alerts use a separate idempotency strategy — see ADR-0008.

## Notes

If we ever stream transitions from a CDC source instead of polling the Jira API, this ADR should be revisited; CDC is naturally insert-only and `UPSERT` semantics fit better.
