# 0010 — Distribute as an Atlassian Connect app, private install first

- **Status:** superseded by [ADR-0019](./0019-pivot-to-forge.md) on 2026-05-02
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #distribution #architecture #strategy

> **Superseded.** Atlassian closed new Connect listings on 2025-09-17 and announced Connect end of support for December 2026. We pivot to Forge before the company install. See [ADR-0019](./0019-pivot-to-forge.md).

## Context and problem statement

Phase 1 goal: install this in our company's Jira Cloud instance and test against one active board. Phase 2: list on the Atlassian Marketplace for other RCM/healthcare-adjacent companies. The current codebase is a standalone FastAPI integration that pulls Jira via Basic auth; it isn't yet "a plugin."

We must pick a distribution mechanism. The choice constrains the language, the runtime, the auth model, and how much of the existing code survives.

## Considered options

- **A. Atlassian Connect** — descriptor + JWT auth; our code runs on our infra; iframe-embedded UI.
- **B. Forge** — Atlassian-hosted FaaS; JS/TS only; managed runtime + storage.
- **C. Hybrid Forge UI + self-hosted backend** — Forge custom UI calls our API.
- **D. OAuth 2.0 (3LO) integration, no Marketplace** — admin grants access on our domain; no in-Jira UI placement.

## Decision

**Atlassian Connect, with a private install (descriptor URL) as the first step.** Marketplace listing is Phase 2.

## Consequences

**Positive**
- Existing FastAPI engine survives. The 62 tests / 87% coverage, the deterministic slicing, the bottleneck scoring — all preserved. Forge would have required a TypeScript rewrite of the engine (4–8 weeks of work that gains us nothing for the initial release).
- Connect is well-documented and battle-tested. JWT-based auth and lifecycle hooks (`installed`, `enabled`, `disabled`, `uninstalled`) are standard.
- Private install via descriptor URL means we can iterate without Marketplace review (which takes weeks). Marketplace review happens once we know the product is right.
- We control hosting, observability, and data location — important for a healthcare-adjacent customer base where infosec teams want to know exactly where data sits.

**Negative**
- We operate the infrastructure (paid out of ADRs 0012 + 0013). Forge would have shifted hosting to Atlassian.
- Connect is being positioned as the "older" platform; Forge is Atlassian's strategic direction. We're choosing operational reality over platform alignment. If Atlassian deprecates Connect, we have a re-platform on the roadmap (we accept this risk; deprecation timelines historically run multiple years with grace periods).
- Marketplace gating later requires meeting Atlassian's security review (OWASP-style, no admin-bypass), which we can plan for from day one.

**Neutral**
- A future second Jira plugin in this family could choose Forge if it's a thin UI extension with no real backend — that's a separate ADR for that project.

## Pros and cons of the options

### A. Atlassian Connect
- **Good:** keeps Python backend; our team's existing AWS/CDK competence applies; iterative private install before Marketplace.
- **Bad:** we host; we operate; Atlassian's strategic direction is Forge.

### B. Forge
- **Good:** Atlassian-hosted; less ops; aligned with platform direction.
- **Bad:** JS/TS only — engine rewrite cost; storage limits (Forge KV / Forge SQL beta caps); compute is paid to Atlassian and bills can surprise; less control over data residency.

### C. Hybrid
- **Good:** Forge in the UI, our backend for compute.
- **Bad:** two systems to maintain, two deploy pipelines, complexity tax not justified before Phase 2.

### D. OAuth-only SaaS
- **Good:** Smallest delta from today.
- **Bad:** Not a "plugin." No in-Jira UI. Doesn't meet the stated goal.

## Notes

When we pursue Marketplace listing in Phase 2, this ADR may be supplemented (not superseded) with one specifying the listing requirements, security review prep, and free-vs-paid feature gates. If we ever decide to migrate to Forge, that ADR supersedes this one and triggers a real rewrite.
