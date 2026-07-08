# 0006 — Deterministic engine; AI is text-translation only

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #ai #correctness #boundary

## Context and problem statement

Both `docs/jira_flow_intelligence/06_INSIGHTS_ENGINE/01_bottleneck_detection.py` and `09_AI_AGENT_SPECS/01_ai_roles_and_responsibilities.md` mandate that AI must NOT compute metrics, detect bottlenecks, or change values. AI's only job is converting structured insight payloads into a one-sentence English explanation.

This is a hard product constraint, but it's also a design boundary that, if respected, makes the system testable, deterministic, and demonstrably correct without a model in the loop.

## Considered options

- **AI-driven scoring** (LLM consumes raw metrics, picks the bottleneck). Rejected by the spec; also non-reproducible.
- **AI-augmented thresholds** (LLM tunes scoring weights). Rejected for same reasons.
- **AI text-translation only**, with a deterministic template fallback when no API key is present.

## Decision

The bottleneck/insight engine (`backend/app/services/insight_service.py`) is pure Python with thresholds from `Settings`. It produces a structured `BottleneckInsight`. The `ai_explanation.explain_insight()` function takes that struct and returns a string. With `ANTHROPIC_API_KEY` unset, it uses a deterministic template (`_template_explanation`). With a key, it forwards the structured payload to Claude with a system prompt that explicitly forbids inventing numbers or changing meaning. If the API call fails for any reason, the template is used.

## Consequences

- Positive: The system can ship and operate with no AI dependency at all. The dashboard works; explanations are slightly less natural-sounding.
- Positive: Tests for the engine never need to mock an LLM — they assert exact scores against deterministic inputs.
- Positive: The template's behavior is itself testable (`test_ai_explanation.py`) and independent of any external service.
- Negative: We have not exercised the live Anthropic call path in CI. A mocked-SDK test could close that gap; tracked in deferred work.
- Neutral: The system prompt is short and embedded inline. If it grows, move to a separate file with version tracking (separate ADR).

## Notes

If anyone ever proposes "let's let the LLM decide what counts as a bottleneck," that proposal supersedes this ADR — open a new one. Don't slide.
