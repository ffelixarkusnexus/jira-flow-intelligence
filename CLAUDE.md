# CLAUDE.md

This file is the entrypoint for any AI assistant (Claude Code or otherwise) working on this repo. Read it first; it points to everything else.

## What this project is

Jira Flow Intelligence — turns Jira issue changelogs into deterministic flow metrics, multi-signal bottleneck detection, and threshold/trend alerts. Backend is FastAPI + SQLAlchemy; the in-Jira surface is a Forge app (Custom UI), backed by the FastAPI engine on AWS App Runner via Forge Remote (ADR-0019). AI is used **only** to translate structured insights into one-sentence explanations; it never computes or alters numbers.

The product/domain spec is in `docs/jira_flow_intelligence/`. Treat that tree as immutable input — if you need to disagree with it, raise the disagreement, don't quietly diverge.

## How we make decisions here

This project is run as a funded startup would. Architectural decisions belong to the human owner. Your job:

1. When you spot a non-trivial decision (framework, schema, threshold, dependency, deviation from spec), surface options with tradeoffs. Don't pick unilaterally.
2. After a decision is made, write or update an ADR in `docs/engineering/adr/` immediately. Don't batch.
3. Trivial code-level choices (variable names, helper layout, fixture shape) don't need approval.

Apply YAGNI/KISS to docs and process the same way you do to code. Don't add CHANGELOG / pre-commit hooks / extra issue templates / multi-page ADRs unless there's a real driver. Best practices ≠ maximalist process.

## Handoff verification flow

Claude Code implements; the reviewer verifies; the maintainer approves. Every Claude Code work-completion handoff routes through the reviewer before reaching the maintainer for final approval.

The reviewer's responsibilities at handoff time:

- Read the handoff against the prompt's stated acceptance criteria.
- Spot-check implementation claims against the actual code, not just against ADRs or narrative docs. When a claim is implementation-specific (*"X is keyed by Y"*, *"we store Z, not W"*), the verification step must include a grep against the named code.
- Push back on any reading that interprets acceptance criteria away from the prompt's intent.
- Either approve and present to the maintainer with the verification trail visible, or surface the gap with specific evidence and propose the corrective workstream.

The maintainer's role at handoff time is final approval, not first-pass verification. The reviewer delivers either (a) clean ready-to-merge with verification evidence, or (b) a corrective-workstream proposal with the gap evidence. The maintainer doesn't re-run the verification the reviewer just did; the reviewer doesn't kick verification decisions up.

Established after a verification gap surfaced: Claude Code's seed-fixture work reported *"status-aliasing not implemented"* and recommended accepting the gap as *"demo-the-drift"*; the reviewer verified the implementation directly via code grep, confirmed the gap was real, and held the public publish gate. Without the verification flow, a documented customer-facing capability the product didn't actually deliver would have shipped.

Apply to every Claude Code handoff. No exceptions for *"small"* or *"obvious"* handoffs — those are the ones most likely to have unverified interpretation drift, because nobody looks.

## Repo layout

```
backend/app/        # FastAPI app
  core/             # config, clock
  db/               # SQLAlchemy models, session, UTCDateTime decorator, bootstrap (alembic-on-startup)
  forge/            # FIT auth, middleware, lifecycle (lazy upsert + uninstall)
  services/         # ingestion, transition, slicing, metrics, insight, alert, ai_explanation, jira_client
  routers/          # sync, issues, metrics, insights, alerts, forge_lifecycle
  schemas/          # Pydantic API schemas
  seeds/            # demo dataset
backend/tests/      # pytest

forge-prod/         # The Forge app — single tree, deployed to both development and
  manifest.yml      # production environments via `forge deploy --environment {development|production}`.
  src/resolvers/    # The "prod" suffix is historical — there used to be a separate forge/ scaffold
  frontend/         # for dev iteration (deleted 2026-05-27). Dev installs hit the dev backend via
                    # remotes[].baseUrl resolution that switches per env.
                    # app.id = ari:cloud:ecosystem::app/00000000...; full scope set + triggers + scheduledTrigger.
                    # src/resolvers/ — dashboardResolver, lifecycleResolver, installResolver,
                    # issueWebhookResolver, issueDeletedResolver, reconcileResolver,
                    # personalDataReportingResolver.
                    # frontend/ — Vite + React 19 + Tailwind 3 — built into static/main/.

docs/
  jira_flow_intelligence/   # IMMUTABLE bootstrap spec — product/domain
  engineering/              # YOUR docs go here (ADRs, plans, runbook, glossary)
    adr/                    # MADR-format ADRs, NNNN-kebab-title.md
    plans/                  # initiative / phase plans (phase-1-...md, etc.)

infra/                      # AWS CDK (Python) — added in Phase 1
.github/workflows/ci.yml    # CI pipeline
.github/workflows/deploy.yml # CDK deploy via OIDC (Phase 1)
```

## Source of truth

- **Product / domain behavior:** `docs/jira_flow_intelligence/`
- **Engineering decisions:** `docs/engineering/adr/` (MADR format)
- **Initiative plans:** `docs/engineering/plans/`
- **How to operate:** `docs/engineering/runbook.md`
- **Definition of done (per-change-type verification checklists):** `docs/engineering/definition-of-done.md`
- **Handoff template (mandatory format for work-completion handoffs):** `docs/engineering/handoff-template.md`
- **Domain vocabulary:** `docs/engineering/glossary.md`
- **End-user / admin manual:** `docs/user-manual/` (charts, WIP limits, windows, alerts, settings)
- **How to contribute:** `CONTRIBUTING.md`
- **Vulnerability reporting:** `SECURITY.md`

## Commands you'll use most

Run from repo root unless noted.

| Goal | Command |
|------|---------|
| Install backend deps | `uv sync` |
| Apply DB migrations | `DATABASE_URL=sqlite:///backend/data/flow.db PYTHONPATH=backend uv run alembic upgrade head` |
| New migration (auto) | `DATABASE_URL=... PYTHONPATH=backend uv run alembic revision --autogenerate -m "<message>"` |
| Backend lint | `uv run ruff check backend` |
| Backend format check | `uv run ruff format --check backend` |
| Backend format apply | `uv run ruff format backend` |
| Backend types | `uv run mypy` |
| Backend tests + coverage | `uv run pytest --cov` (gate: 80%) |
| Seed demo data | `uv run python -m app.seeds.demo` |
| Run backend | `PYTHONPATH=backend uv run uvicorn app.main:app --reload` |
| Forge resolver deps | `cd forge-prod && npm install` |
| Forge resolver typecheck | `cd forge-prod && npm run tsc` |
| Forge manifest lint | `cd forge-prod && forge lint` |
| Custom UI deps | `cd forge-prod/frontend && npm install` |
| Custom UI typecheck | `cd forge-prod/frontend && npm run typecheck` |
| Custom UI build | `cd forge-prod/frontend && npm run build` |
| Custom UI tests (watch mode) | `cd forge-prod/frontend && npm run test` |
| Custom UI tests + coverage (one-shot, gate ≥80% on `src/lib/*`) | `cd forge-prod/frontend && npm run test:coverage` |
| Local Forge tunnel | `cd forge-prod && forge tunnel` |
| Deploy Forge dev | `cd forge-prod && forge deploy --environment development` |
| **Workflow YAML pre-flight** (mandatory before pushing any `.github/workflows/*.yml` change) | `actionlint .github/workflows/<file>.yml` |

CI runs all of the above on every PR. Don't bypass it locally with `--no-verify` etc. — fix the cause.

## Coding rules (non-negotiable)

1. **Source of truth = changelog.** Never derive flow metrics from `current_status` alone.
2. **Determinism.** Same data → same metrics, same bottleneck, same insights. AI is text-only.
3. **Idempotency.** Re-running ingestion must produce identical row counts and values. Re-running alert evaluation must not duplicate alerts.
4. **No gaps, no overlaps.** Every issue's slices must cover `[created_at, done_at_or_now]` exactly once.
5. **All datetimes are UTC tz-aware** (we use the `UTCDateTime` SQLAlchemy decorator to round-trip safely on SQLite).
6. **Tenant scoping is mandatory.** Every query against tenanted tables (`issues`, `transitions`, `time_slices`, `metrics_*`, `alerts`, `alert_rules`) MUST filter on `tenant_id`. Routers receive a `TenantContext` via the `current_tenant_context` FastAPI dependency; services accept that ctx and use `ctx.tenant_id` in `WHERE` clauses. `JWTAuthMiddleware` is the only producer of `request.state.tenant` in production. See ADR-0014, ADR-0016.
7. **Schema changes go through Alembic.** Don't rely on `Base.metadata.create_all` outside of tests; production migrations are `alembic upgrade head`.
8. **Auth is fail-closed.** A new route is automatically protected unless explicitly added to `SKIP_PATH_PREFIXES` in `app/forge/middleware.py`. If you need an unauthenticated route (rare — `/healthz` and the FastAPI doc routes are the only ones today), add it to the skip list AND add a test asserting it bypasses. See ADR-0019.
9. **Proactive notification.** Any signal that requires user action or decision must be **actively pushed** (email, in-app push, etc.), not passively surfaced waiting to be discovered. Applies to customer-facing AND maintainer-facing surfaces equally. The test: *"if this user never comes back to the surface where this is rendered, does something bad happen?"* If yes → push proactively. If no → passive is fine. Established 2026-05-25 during ADR-0033 review; load-bearing for backfill completion / failure notifications.
10. **Best-in-category-defaults hierarchy.** Every feature ships with a configuration shape that respects this priority order when the rules conflict:

    1. **Safe default first.** Every feature ships with a default that's correct for the majority of users without configuration. If the user does nothing, the tool behaves like the most thoughtful tool in the category for the most common workflow shape.
    2. **Conceptual simplicity for the default path.** A user who doesn't customize anything should never have to understand a setting to use the tool correctly. Default-UI cognitive load is paid for by the 95% who don't need that complexity; the 5% who need a specific lever absorb the cost willingly when they reach for it.
    3. **Edge-case preservation as opt-in, not as default visibility.** Real workflows have legitimate edge cases. Preserve the capability — but in an "advanced" surface that default users don't see. Opt-in means the user makes a conscious choice to leave the safe-default path, with clear help text naming the scenario where that choice makes sense.
    4. **Discoverability calibrated to who needs the feature.** Default users discover what they need at the right level. Power users discover the advanced lever when they search for it (docs, advanced toggles, support). Don't make every capability equally visible to every user.

    **The test:** when a feature design conflicts these rules, the lower-numbered rule wins. A buggy default is never acceptable to preserve an edge case; an exposed advanced toggle is never acceptable for the sake of discoverability.

    **Why this is non-negotiable.** Established 2026-06-01 after a bug ("Done is the bottleneck" on default workflows) surfaced from a real customer install and made the tool look much less thoughtful than advertised. Rules-without-hierarchy fail in conflict; this hierarchy makes the conflicts predictable to resolve. The risk of skipping is the tool drifting back into "47 settings the user has to understand" — exactly the complexity this rule exists to prevent. See ADR-0038.
11. **Don't cheat.** When any check, gate, test, hook, lint, type-check, format check, security scan, coverage threshold, or guard catches a problem, the job is to **fix what triggered the signal**, not to silence the signal. The shape of the cheat doesn't matter — the function does. If the action makes red go away without addressing the underlying issue, it's the cheat; enumerating specific patterns ("don't use `# type: ignore`", "don't add to `omit`", "don't lower `fail_under`") invites finding new mechanisms not on the list. The rule covers all forms, present and future, named and unnamed.

    See red → find the cause → fix the cause. If you can't fix it in this scope, surface the gap honestly (the cost, the tradeoff, the risk) and let the user decide whether to take it on — don't quietly negotiate with the gate to keep shipping. A working gate that's been negotiated with is worse than a broken gate that's loudly red — the negotiated gate gives false comfort and trains everyone, including future you, to ignore subsequent red badges as ambient noise. Inheriting an existing cheat without surfacing it is complicity in it.
12. **Verification is load-bearing for "done."** When a prompt names verification artifacts (screenshots, integration test outputs, specific assertions, deployment confirmations), those are required, not optional. "Endpoint returns expected HTTP status" verifies route registration, not logic correctness. "Tests pass locally" verifies code compiles and asserts pass, not that the logic shown to the maintainer is the logic the test exercises. "Done" means the named artifacts exist, are pasted in the handoff or PR description, and prove the work behaves as specified.

    The reusable test before claiming any feature done: *"Did I produce the artifacts the prompt named, or did I substitute weaker evidence?"* If substituted, the work is not done. Either produce the artifact or surface the gap before claiming completion.

    **Sub-rules. Each binding, each enforced at handoff time:**

    - **Coupled systems deploy together.** Backend + Forge frontend is a coupled system. Backend + CDK infrastructure is coupled. Never deploy half a coupled system without explicit coordination on the other half. If the other half requires a manual step the maintainer runs, surface the dependency BEFORE the half you are deploying, not after. The test: *"Does this change introduce a surface a corresponding change on another system must consume? If yes, both halves are coordinated in the deploy plan before either ships."*

    - **Inflight fixes are documented at fix time.** When a bug is hit and patched during a shipping cycle, the patch lives in three places before the workstream is reported done: (a) a descriptive commit, (b) `CHANGELOG.md` under `[Unreleased]` → `### Fixed` (or `### Internal`), (c) a test that exercises the bug condition. Mentions of fixes in a handoff that are not documented in those three locations are a regression to the "see red, silence it, ship" anti-pattern rule #11 exists to prevent.

    - **Risks are binary — closed or open, never "fingers crossed."** When a risk is identified during a shipping cycle, the handoff states one of two things, never a third: *"Mitigated. Implementation applies [specific lesson / ADR / pattern]. Code paste proving it: [snippet]."* OR *"Open. Implementation does NOT mitigate. Surfacing for guidance before deploy."* The middle state — flagging a risk in passing without stating mitigation status — is abdication of engineering ownership and gets sent back for correction.

    - **Mandatory handoff format.** All work-completion handoffs to the reviewer or the maintainer use the template at [`docs/engineering/handoff-template.md`](docs/engineering/handoff-template.md). Handoffs in any other format will be sent back for re-format before review. The template's verification slots cannot be skipped; "N/A" is acceptable as a value but blank is not.

    See [`docs/engineering/definition-of-done.md`](docs/engineering/definition-of-done.md) for the concrete checklists per change type.

If you're tempted to violate any of those, stop and ask.

## Knowledge capture (non-negotiable)

If you spend non-trivial effort acquiring a fact about how an **external system actually behaves** (Atlassian, AWS, Forge, vendor APIs, browser/runtime quirks, etc.), capture it in the repo before reporting the task done. Not as a follow-up. Not when the user notices and asks. As part of the work itself.

**Triggers — any one of:**

- Read **2+ external doc pages** to derive an answer.
- Spent **>15 min** in logs, `forge tunnel` output, vendor source, or the partner console to find a root cause.
- **Reverse-engineered** vendor behavior that isn't obvious from our code or already in the runbook.
- Caught yourself **giving an answer you later had to correct** — that means the knowledge cost was paid in tokens and a wrong answer; don't pay it again next session.
- Discovered an **undocumented gap or constraint** that affects future deploys, decisions, or operations.

**Destinations:**

- **Operational mechanics, vendor behavior, deploy semantics, runtime quirks →** `docs/engineering/runbook.md`. Add a subsection under the right top-level heading; don't fragment into new files unless the runbook is genuinely outgrowing one file.
- **Decisions** (we picked X over Y because Z) **→** MADR ADR in `docs/engineering/adr/`.
- **Vocabulary** (a term we keep needing to define) **→** `docs/engineering/glossary.md`.

**Mark every claim** as **documented** (cite the source URL), **observed** (note where and when we saw it), or **undocumented** (state the gap and the conservative operating rule). Future-readers — human or AI — must be able to tell what is load-bearing fact vs. inference.

**Why this rule is non-negotiable:** YAGNI/KISS applies to *speculative* docs (don't write a CHANGELOG nobody asked for). This rule is the opposite — it captures facts that **already cost real effort to acquire**. Re-deriving them every session burns tokens, wall-clock time, and trust in our answers. The cost of writing it down once is bounded; the cost of re-deriving it forever is not. Skipping this step *is* the waste.

## Scope of authority for an AI assistant

You can do, without asking:
- Write tests, fix lint findings, fix type errors
- Refactor within a service module
- Update docs that describe what's already true
- Run any read-only command

You should ask first:
- Add or remove a dependency
- Change a public API contract (route, schema)
- Change a metric formula or threshold
- Modify any file under `docs/jira_flow_intelligence/`
- Skip a CI check or coverage gate
- Touch CI workflows or release machinery

## When you finish a task

- **Apply rule #12.** Before claiming any feature done, run the test from rule #12: *"Did I produce the artifacts the prompt named, or did I substitute weaker evidence?"* If substituted, the work is not done. The concrete checklists per change type live in [`docs/engineering/definition-of-done.md`](docs/engineering/definition-of-done.md); the mandatory format for the work-completion handoff lives in [`docs/engineering/handoff-template.md`](docs/engineering/handoff-template.md). Handoffs in any other format will be sent back for re-format before review.
- **Release-notes surface for customer-visible Forge production deploys.** If the task involved a `forge deploy --environment production` that surfaces a customer-visible behavior change (new feature, customer-facing bug fix, UI change, anything the customer can perceive), name the customer-visible changes in your handoff so the reviewer can draft release notes within ~24h. Engineering / CI / coverage / refactor work does not need release-notes surfacing — those go in ADRs and the runbook. Per-version notes get pasted into the Atlassian Partner Console once maintainer-approved.
- Run the full local toolchain (lint, types, tests, coverage on backend; typecheck + build on the Forge Custom UI; cdk synth + tests on infra) before reporting done.
- If a decision was made during the task, write the ADR. If a behavior changed, update `runbook.md` or relevant ADR.
- **Knowledge capture check.** Did the task require reading external docs, debugging through logs, or reverse-engineering vendor behavior? If yes, the facts you derived must be in `runbook.md` (or an ADR / glossary entry, per the "Knowledge capture" section above) **before** you report the task done. This is not optional and not a follow-up.
- For UI changes, actually open the dashboard in a browser. If you can't, say so explicitly — don't claim success on a green type-check alone.
