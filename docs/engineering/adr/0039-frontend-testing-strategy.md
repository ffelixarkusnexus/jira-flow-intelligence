# 0039 — Frontend testing strategy: Vitest + RTL for the Forge Custom UI

- **Status:** Accepted (2026-06-02 — authorized after a testing-gap audit surfaced 0% frontend coverage)
- **Date:** 2026-06-02
- **Decision-makers:** the maintainer; technical drafting by Claude Code
- **Tags:** #testing #frontend #ci #forge #quality-gate

## Context

### Why this is being decided now

An end-of-session audit on 2026-06-02 surfaced that **the Forge Custom UI ships to customers with zero automated test coverage**. Backend is at 82.58% (gated at 80%). The Forge Custom UI — the in-Jira surface every customer sees — had no test harness at all. Three code chunks shipped to prod in the same session that motivated this ADR ran on a pure trust-the-types-and-eyeball-the-render basis:

- `humanDuration` (TypeScript) in `AlertsList.tsx` — a hand-port of the backend Python `human_duration` from `app/services/duration_format.py`. If either side drifts, customer-facing strings render seconds differently from outbound Slack/Teams messages. No automated parity check.
- `groupAlerts` / `renderAlertBody` in `AlertsList.tsx` — the logic that collapsed a 50-row flat alert dump into ~3-5 grouped rows after a customer-reported UX bug. No regression test.
- The Advanced settings expander in `TenantSettingsPanel.tsx` — the opt-in toggle for the Done→Terminal merge from ADR-0038. No test on the toggle state, dirty detection, or reset semantics.

This is the kind of gap that produces "I refactored the alert grouping last week and now the dashboard shows 50 rows again" — silent regressions that only customers catch. Closing it before the frontend grows more code.

### The Forge Custom UI risk shape

The `forge-prod/frontend/` tree builds via Vite 6 into a static bundle served by the Forge platform inside Jira. It's React 19 components with real state, branching render logic, and customer-data-driven views (alerts list, bottleneck panel, settings, charts). What can break: stateful render logic, payload-shape changes, and cross-language drift with the backend formatters. That risk shape — logic and state, not styling — is what the test framework choice below optimizes for.

## Decision

### A. Forge Custom UI: Vitest + React Testing Library + jsdom

**Test runner: Vitest 1.x.**

| Criterion | Vitest | Jest |
|---|---|---|
| Shares config with our build (vite.config.ts) | **Yes** | No — needs separate Babel/SWC transform pipeline |
| ESM-native | **Yes** | Workable but historically rough |
| Speed (our size, ~10 lib + ~10 component test files projected) | Sub-second unit, ~3s full | 5-10× slower; Jest worker model fights modern bundlers |
| Maintainership in 2025 | **Vite team — aligned with our React 19 / Vite 6 stack** | Stable but Meta investment plateaued |
| API | **Jest-compatible** (`describe`/`it`/`expect`) — escape hatch if we ever migrate off | — |

Jest would force config duplication (two transform pipelines for the same TypeScript/JSX) for zero benefit at our stack.

**Component testing: React Testing Library (RTL).** De-facto standard for React in 2025; queries by accessible role/label/text rather than implementation details, so tests survive refactors. Paired with `@testing-library/jest-dom` for additional matchers (`toBeInTheDocument`, `toHaveTextContent`, etc.) and `@testing-library/user-event` for interaction simulation.

**DOM environment: jsdom.** Mature default; happy-dom is faster but has spotty CSS/event support that bites at the worst time; @web/test-runner is real-browser but slow. jsdom is the right balance at our size.

**Coverage tool: `@vitest/coverage-v8`.** V8 native, accurate, no transformer overhead.

### B. Coverage gate strategy

Backend has a flat 80% gate, which works because every file is either domain logic or service code. The Forge UI has a mixed shape:

- **`src/lib/**`** — pure logic (humanDuration, alertGrouping). Same risk profile as backend services. **Gate: 80%** matching backend.
- **`src/components/**`** — JSX-heavy, mixed logic + rendering. Behavior is best covered by user-perspective component tests, but full coverage of every JSX branch isn't the right success metric. **Gate: deferred** for this first pass — no enforced threshold on components. Add tests as features land; revisit the gate once there's a meaningful baseline (~50% organic coverage from real test work).

This avoids the failure mode of "we have an 80% gate but our component tests are vacuous render-assertions to hit the number." We'd rather have fewer, real tests than padded coverage. The strict gate stays on lib code where it actually catches drift (e.g. humanDuration parity with backend).

### C. CI integration — path-aware

The path-aware CI pattern (`changes` job sets per-area booleans, downstream jobs gate on `if: needs.changes.outputs.<area>`) already exists. The new test job slots in naturally:

- The existing `forge` CI job (currently does `npm run tsc` + `npm run typecheck` + `npm run build`) gains a `Run Vitest` step at the end. Same `if: needs.changes.outputs.forge == 'true'` — only runs when `forge-prod/**` changes.

### D. What's explicitly NOT being adopted now (and why)

- **Playwright for Forge Custom UI.** Forge Custom UI runs inside Forge's iframe with a real Atlassian session — testing it in a real browser requires logged-in Atlassian state, an installed app in a dev cloud, and Forge bridge mocks. The cost-to-value ratio is bad at our stage. RTL + jsdom catches the bugs we'll actually have for the foreseeable future.
- **Visual regression (Chromatic / Percy / Loki).** Useful when a design system has many component variations to track; we have a single dashboard surface with no shared design system pretensions. Defer until justified.
- **Storybook.** Same reasoning — overkill for current scale.

These all stay tracked as "considered, not adopted now" in this document so future contributors can see they weren't oversights.

## Consequences

### Positive

- **Cross-language parity locked down.** `humanDuration` (TS) now has a parametric test mirroring `test_duration_format.py` (Python). Drift between the two becomes a failing test, not a customer-facing UI bug.
- **AlertsList grouping logic regression-protected.** The 50→4 collapse from the 2026-06-02 customer report won't silently regress.
- **TenantSettingsPanel state semantics tested.** The Advanced toggle + form-dirty + reset flow is locked in.
- **Foundation for adding tests with new features.** From this point on, new Forge UI components ship with at least one test covering their primary state machine.

### Negative / honest costs

- **~30 dev dependencies added to `forge-prod/frontend/`.** Dependabot's grouped + ignore-major policy keeps the maintenance trickle bounded — one grouped PR per week for minor/patch on the Forge UI tree.
- **Backend ↔ frontend parity has to be maintained manually.** When the backend `human_duration` semantics evolve, the TS port must follow and the parity test updated. There's no machine enforcement that the two implementations are identical — only that they match against a shared test corpus.
- **Component tests don't cover CSS rendering.** jsdom doesn't apply Tailwind classes to compute actual layout. Visual regressions still slip past — accepting that for this first pass; visual regression service is a future decision.

### Implementation scope for this ADR's ship

1. `forge-prod/frontend/package.json` gains vitest + RTL + jsdom + coverage tooling
2. `forge-prod/frontend/vitest.config.ts` configures jsdom + setup file + coverage
3. `forge-prod/frontend/src/test/setup.ts` extends RTL matchers + mocks `@forge/bridge`
4. `forge-prod/frontend/src/lib/duration.ts` — extracted from AlertsList.tsx for testability
5. `forge-prod/frontend/src/lib/alertGrouping.ts` — same
6. `forge-prod/frontend/src/lib/duration.test.ts` — humanDuration parity tests
7. `forge-prod/frontend/src/lib/alertGrouping.test.ts` — grouping logic tests
8. `forge-prod/frontend/src/components/AlertsList.test.tsx` — render tests via RTL
9. `forge-prod/frontend/src/components/TenantSettingsPanel.test.tsx` — interaction tests
10. `.github/workflows/ci.yml` — `forge` job gains a `Run Vitest` step
11. `docs/engineering/runbook.md` — testing commands added to the commands table; CI section updated to reflect the new step

## Verification (per the discipline rule from this session)

- Local: `cd forge-prod/frontend && npm run test:coverage` passes; lib coverage ≥80%; component coverage reported but not gated.
- CI authoritative: post-push, `gh run list --branch=main` shows the new test step running inside the `forge` job, conclusion=success.

## Related

- **Builds on:** ADR-0013 (CDK Python — the broader tooling baseline), ADR-0009 (engineering tooling), and the path-aware CI/Deploy design.
- **Defers / does not address:** Forge UI in-browser e2e, visual regression, Storybook, a11y testing. Each future when justified by a real driver.
- **CLAUDE.md alignment:** Coverage testing fits rule #11 (no gate cheats) — the lib-only 80% gate is the gate that exists where it adds enforcement value; declining to set a component gate avoids the "we have a gate but the tests are vacuous" failure mode rule #11 forbids.
