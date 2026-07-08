# 0007 — Bottleneck scoring formula and tie-break rule

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #insights #product

## Context and problem statement

The spec gives two slightly different scoring rules:

- `06_INSIGHTS_ENGINE/01_bottleneck_detection.py` — three signals: `time_ratio>=1.3 → +2`, `wip_ratio>=1.2 → +1`, `throughput_delta<=-0.2 → +1`. Threshold to flag = 3.
- `10_CLAUDE_CODE_MASTER/06_insight_engine_spec.md` — same three rules **plus** an extra-weight rule: `time_ratio>=1.5 → +1`. Same threshold of 3.

Two issues this ADR resolves: (1) which formula do we ship, (2) what's the tie-break when two statuses get the same score.

## Considered options

- **Three-signal formula only** (the simpler one)
- **Four-signal formula** (with the extra-weight rule)
- **Make all weights configurable via `Settings`** and let operators tune

## Decision

Implement the four-signal formula from `06_insight_engine_spec.md`. Each threshold is exposed as a `Settings` field (`bottleneck_time_ratio_threshold`, `bottleneck_wip_ratio_threshold`, `bottleneck_throughput_delta_threshold`, `bottleneck_time_ratio_extra_threshold`, `bottleneck_min_score`). Confidence mapping: 3 → medium, 4 → high, 5+ → very_high.

Tie-break: when two statuses have the same score, pick the alphabetically first status name. This makes results reproducible across runs and makes diffs in tests stable. Documented in code at `insight_service.detect_bottleneck()`.

## Consequences

- Positive: Higher-severity slowdowns (50%+ time inflation) get pushed up to "very_high" confidence even without WIP/throughput contribution. Operators see the worst case first.
- Positive: Settings-tunable means a downstream operator can dial the system without a code change.
- Positive: Deterministic tie-break — required by ADR-0006 (deterministic engine).
- Negative: The two spec files now disagree slightly. Resolution is documented here; we don't edit `docs/jira_flow_intelligence/`.
- Neutral: We have no telemetry yet on how often the extra-weight rule fires. After first real customer, revisit thresholds with data.

## Notes

The scoring formula is exercised by `backend/tests/test_insight_engine.py` — six tests, including the extra-weight rule and the cross-status tie scenario. If you change a threshold, change the test.
