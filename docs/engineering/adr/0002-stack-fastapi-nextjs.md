# 0002 — Accept FastAPI + Next.js as the application stack

- **Status:** accepted
- **Date:** 2026-04-29
- **Decision-makers:** the maintainer
- **Tags:** #architecture #frontend #backend

## Context and problem statement

`docs/jira_flow_intelligence/10_CLAUDE_CODE_MASTER/01_master_instructions.md` mandates "Backend skeleton (FastAPI)" and a "Next.js insight-first UI". This ADR records that we accepted those choices rather than substituting our own framework picks, and the reasons for accepting.

## Considered options

- **FastAPI + Next.js** (spec-mandated)
- Substitute Django REST + Vite/SvelteKit
- Substitute Flask + plain React (CRA-style)

## Decision

Use FastAPI for the backend and Next.js (App Router, React 19, Tailwind) for the frontend, as the spec mandates.

## Consequences

- Positive: Pydantic v2 schemas double as request/response validation and as the type contract for the auto-generated `/docs` (OpenAPI). The Next.js dashboard server-renders one route and `fetch`es the FastAPI in `Promise.all` — fast and minimal.
- Positive: Both frameworks have first-class typing stories: Pydantic + mypy on Python, generated Next.js types + tsc on the frontend. Our toolchain (ADR-0009) enforces both.
- Negative: Server-rendered Next + a Python backend means two runtimes in CI. Acceptable cost.
- Neutral: API auth is unimplemented (the public surface is unauthenticated). Owner deferred this until there's a real driver — see runbook "deferred work".

## Notes

If the dashboard ever needs interactivity beyond what server-rendered components give us, this ADR should be revisited (e.g., add SWR or React Query as a separate ADR — don't sneak it in).
