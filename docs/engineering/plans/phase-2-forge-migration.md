# Phase 2 — Forge migration

**Goal:** convert the running Atlassian Connect app into a Forge app, install it in a developer instance and then in our company's Jira, with the deterministic engine, dashboard, and alerts working end-to-end against real Jira data — so that a future Marketplace listing is unblocked.

**Why now:** ADR-0019. Atlassian closed new Connect listings on 2025-09-17 and ends Connect support in December 2026. Continuing on Connect ships an app with no path forward.

**Out of scope for Phase 2:** Marketplace listing itself, paid tier, scheduler, structured logs / tracing, custom domain, RLS hardening, multi-AZ. Those move to Phase 3.

## Acceptance criteria

1. A Forge app exists with manifest covering: `jira:projectPage` for the dashboard, an `installed`/`uninstalled` event trigger to manage the `tenants` row, and `remotes` declaring the App Runner backend.
2. Backend authenticates incoming requests by validating the Forge Invocation Token against Atlassian's JWKS, audience-checked to our Forge App ID. Tenant identity is `(cloudId, installationId)`.
3. The dashboard renders inside Jira — same components, same Tailwind theme — with bottleneck, alerts, and trends backed by the existing engine.
4. Atlassian admin can install the app via a Forge installation link in the developer instance, then in our company instance.
5. Connect-specific code is removed (descriptor route, lifecycle router, HS256 verification, qsh, frontend proxy app). Tests for those modules go with them.
6. CI green: backend (ruff/mypy/pytest @80%), frontend (eslint/prettier/tsc/build), infra (cdk synth × 3 envs), and a new `forge lint` check on the Forge app.
7. CDK retires the frontend App Runner service. ECR frontend repo can be removed in a follow-up once stable.

## Workstreams

```
[Stream A] Forge app skeleton + manifest                     (independent)
[Stream B] Backend FIT validation                            (independent)
       ↓
[Stream C] Custom UI port of the dashboard                   (depends on A)
[Stream D] Schema + lifecycle: tenants for Forge             (depends on B)
       ↓
[Stream E] Wire up: resolver → backend → dashboard           (depends on B, C, D)
       ↓
[Stream F] Retire Connect plumbing                           (depends on E)
       ↓
[Stream G] Install in dev instance, then company instance    (depends on E, F)
```

## Stream A — Forge app skeleton

- Add `forge/` at repo root: `manifest.yml`, `package.json`, `src/resolvers/`, `src/frontend/` (Custom UI bundle source).
- Manifest modules:
  - `jira:projectPage` keyed `flow-intelligence-dashboard`, resource `main`, resolver `dashboardResolver`.
  - `trigger` for `avi:forge:installed:app` and `avi:forge:uninstalled:app` calling `lifecycleResolver`.
  - `remotes` declaring our backend's App Runner URL.
  - `permissions.scopes`: `read:jira-work`, `read:jira-user`, plus an `external-fetch.backend` to call our backend.
- `forge register` against the developer console to mint an App ID. App ID becomes the JWT audience.
- `npm install -g @forge/cli`; pin via `package.json` engines + `.tool-versions`.

Acceptance: `forge deploy` succeeds against the developer console; `forge lint` is clean.

## Stream B — Backend FIT validation

Replace the HS256 middleware (ADR-0016) with RS256 + JWKS:

- New module `app/forge/fit_auth.py` — fetches JWKS from `https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json`, caches per-`kid` keys with TTL, validates `aud == FORGE_APP_ID` and `iss` matches Atlassian's expected issuer.
- Extracts `cloudId` and `app.installation.id` from claims; binds to `request.state.tenant`.
- Replace `JWTAuthMiddleware`. Skip list reduces to `("/healthz", "/docs", "/redoc", "/openapi.json")` — no descriptor, no lifecycle.
- Settings adds `FORGE_APP_ID`. Set per environment via App Runner Secrets Manager.

Acceptance: `test_fit_auth.py` covers happy path, expired token, wrong audience, missing kid (cache miss → re-fetch), and a known-good fixture token. The middleware tests prove protected routes 401 without a FIT.

## Stream C — Custom UI port of the dashboard

- Vite + React + Tailwind project under `forge/frontend/`, building into `forge/static/main/` (the resource directory referenced by the manifest's `jira:projectPage`).
- **Copy** components from `frontend/components/` into `forge/frontend/src/components/`. `frontend/lib/api.ts` becomes `forge/frontend/src/lib/requestRemote.ts`, rewritten to use `invoke`/`requestRemote` from `@forge/bridge` (the bridge attaches the FIT automatically).
- The existing `frontend/` tree is *not* edited during Stream C — it stays running as the deployed Connect surface until Stream F retires it.
- Remove the Connect `AP.context.getToken()` plumbing in the copy — Forge has no equivalent.

Acceptance: dashboard renders inside Forge tunnel mode (`forge tunnel`) showing real backend data.

## Stream D — Schema + lifecycle for Forge

- Alembic migration: `tenants.shared_secret` becomes nullable then is dropped; add `forge_installation_id TEXT`, `cloud_id TEXT`, both nullable for one release window.
- New service `app/forge/lifecycle.py` exposing functions called from the Forge resolver via the backend's `/api/forge/lifecycle/installed` and `/uninstalled` endpoints (these are normal protected backend routes; the FIT validates the call is from the app).
- `installed`: upsert tenant row keyed by `(cloud_id, forge_installation_id)`. No shared secret to store.
- `uninstalled`: hard delete the tenant row. CASCADE handles the rest (ADR-0014).
- `disabled`/`enabled`: Forge handles app disable at the platform level — we don't get an event we have to act on. Leave the column in place, mark deprecated.

Acceptance: install/uninstall in dev tenant correctly create/delete the row. Cross-tenant isolation tests still pass.

## Stream E — End-to-end wire-up

- Resolver `dashboardResolver` returns the page-load payload (the URL of the dashboard bundle, plus a derived `cloudId`).
- Custom UI calls `requestRemote('GET /api/insights?days=7')`. Backend validates FIT, derives tenant, returns data.
- Page renders. Bottleneck shown. Alerts list populated. Trends list populated.

Acceptance: run `forge tunnel`, open the project page in dev Jira, see the dashboard with real data.

## Stream F — Retire Connect plumbing

Delete:

- `backend/app/atlassian/` (descriptor route, lifecycle router, HS256 middleware, qsh helpers).
- The entire `frontend/` tree (App Router pages, Connect proxy routes, Dockerfile, Next config). The CI matrix entry for `frontend` is removed in the same PR.
- The frontend App Runner service + ECR frontend repo in `infra/stacks/compute_stack.py` and `infra/stacks/ecr_stack.py`. CDK retires the resources on next deploy.
- Tests under `backend/tests/test_lifecycle.py`, `test_jwt_middleware.py` (replaced by `test_fit_auth.py`), `test_descriptor.py`.
- Runbook section "How do I hit a protected endpoint with `curl` from my laptop?" (Connect-specific JWT minting recipe). Replace with a Forge tunnel recipe.

Acceptance: `rg -i "atlassian.connect|client_key|shared_secret|qsh"` returns nothing under `backend/app/` or `infra/`. `frontend/` no longer exists.

## Stream G — Install + smoke

- Free developer instance via `go.atlassian.com/cloud-dev`. Run `forge install --site <dev>.atlassian.net --product Jira`.
- Smoke: project page loads, sync runs against seeded data, bottleneck shown, alert fires.
- Generate a distribution link (`forge install --share`).
- Send to company Jira admin; install on company instance.
- Run real sync. Confirm.

Acceptance: real bottleneck visible on company Jira.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FIT validation has a subtle audience or iss mismatch | Medium | Medium (won't auth) | Build a fixture-token test against published Atlassian sample claims; verify with `forge tunnel` before remote deploy |
| Custom UI iframe can't reach the App Runner backend (CSP, CORS, or `external-fetch` permission misset) | Medium | Medium | Add backend explicitly to manifest `remotes`; backend sets `Access-Control-Allow-Origin: https://*.atlassian-dev.net` for Forge origins |
| Existing components rely on Next.js features that don't survive a static export | Medium | Low | Components are already client-only React; only the page shell uses Next; rewrite the shell |
| Forge installation limits delay the company install (admin approval, scope review) | Low | Low | Keep scope list minimal; send permissions diff to admin in advance |
| Schema migration runs against prod with Connect-only tenants present | Low | Medium | Migration is additive then drop; schedule the drop after we've cut over |

## Decisions (locked 2026-05-02)

1. **Custom UI bundling:** **Vite/React** under `forge/frontend/`, building into `forge/static/main/`. Static-export Next was rejected — the Next runtime overhead and the page-shell rewrite would not pay back, since SSR + server-proxy routes are exactly what Stream F deletes.
2. **Component sharing:** **Copy components into `forge/`, delete `frontend/`** in Stream F. The workspace-package option was rejected — it kept `frontend/` alive purely as a non-Forge local smoke test surface, which `forge tunnel` already covers. No second non-Forge consumer is concrete enough to justify the workspace tax.
3. **Forge environment naming:** **1:1 with AWS envs** — Forge dev ↔ AWS dev, Forge staging ↔ AWS staging, Forge prod ↔ AWS prod. Costs nothing extra; gives a Jira-iframe soak window before admins install on prod.
4. **`FORGE_APP_ID` storage:** **SSM parameter per env**, surfaced as an App Runner env var. Public ID but env-scoped.

## Out-of-scope reminders

- No Marketplace submission yet — separate gate, separate ADR (security review prep).
- No paid tier code yet.
- No incremental sync.
- No structured logs / tracing / scheduler — those gates carry forward unchanged from Phase 1.
