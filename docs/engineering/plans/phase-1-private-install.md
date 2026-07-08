# Phase 1 — Private Atlassian Connect install

> **Status as of 2026-05-02:** the private-install work through AWS deployment is complete. The remaining steps (install in the dev sandbox, then in production Jira) were halted when Atlassian's Connect deprecation timeline surfaced. Work continues in [Phase 2 — Forge migration](./phase-2-forge-migration.md) per [ADR-0019](../adr/0019-pivot-to-forge.md). This plan is kept as the historical record of the Connect work.

**Goal:** install Flow Intelligence as a private Atlassian Connect app in a private Jira Cloud instance, configured to test against one active board, deployed to AWS via CDK + GitHub Actions, with the deterministic engine, dashboard, and alerts working end-to-end against real Jira data.

**Out of scope for Phase 1:** Marketplace listing, paid tier, SSO/SAML, Multi-AZ RDS, multi-account AWS Organizations, Slack/email alert channels, scheduler.

## Acceptance criteria

1. Atlassian admin can install the app from a descriptor URL.
2. App handles `installed` / `enabled` / `disabled` / `uninstalled` lifecycle webhooks; `tenants` row is created/updated/disabled accordingly.
3. JWT auth from Atlassian validates correctly on every API call.
4. Issue ingestion runs against the live Jira instance using the OAuth/JWT credentials from the install handshake.
5. Dashboard renders inside Jira (iframe-embedded page) and shows the bottleneck for the configured board.
6. Alerts evaluate correctly and dedupe across runs.
7. Backend + frontend deployed to AWS App Runner; RDS Postgres in `us-east-1`; secrets in AWS Secrets Manager; deploys via GitHub Actions OIDC; environment per branch (`feature/*` → dev, `develop` → staging, `main` → prod).
8. CI passes: ruff/mypy/pytest@80% on backend, eslint/prettier/tsc/build on frontend, `cdk diff` on infra PRs, `cdk deploy` on merge.
9. Cost capped via CloudWatch billing alarm at $100/month for the test account.

## Workstreams (rough parallelism)

The work splits into four streams that can run partially in parallel after the multi-tenancy refactor lands:

```
[Stream 1] Multi-tenant refactor (blocks everything else)
       ↓
[Stream 2] Atlassian Connect: descriptor + lifecycle + JWT
[Stream 3] Frontend embedding: iframe-friendly dashboard
[Stream 4] AWS infra: CDK stacks + GitHub Actions deploy
       ↓
[Stream 5] Install in Atlassian dev sandbox → company instance
```

## Stream 1 — Multi-tenant refactor (single most important change)

Per ADR-0011. Touches the schema, models, every service, every test.

- Add `tenants` table (PK = `client_key`, columns: `cloud_id`, `base_url`, `shared_secret`, `installed_at`, `enabled`, `last_sync_at`, `plan`, `display_url`, `product_type`).
- Composite PKs `(tenant_id, id)` for `issues` (Jira IDs are not globally unique).
- `tenant_id NOT NULL FK REFERENCES tenants(client_key)` on every business table.
- Unique constraints rewritten to include `tenant_id`.
- `(tenant_id, ...)` composite indexes added on hot paths.
- `Settings`-level `active_statuses` and `done_statuses` move to per-tenant config (stored on the tenant row or a `tenant_config` table).
- `db_session()` and `get_db()` accept/expose a `current_tenant_id`. A SQLAlchemy `before_compile` event hook asserts every `select` includes `tenant_id` in the WHERE clause for tenanted tables. (Defense in depth; we still hand-write the filters.)
- All service functions take `tenant_id` as a required parameter — no implicit globals.
- Migrate to PostgreSQL as the canonical dev/test DB (SQLite still usable for unit tests, but integration tests run on Postgres in CI via a service container).
- Alembic added for migrations; baseline migration captures current schema; second migration adds tenant scaffolding.

Acceptance: existing 62 tests parameterized over `tenant_id` and still passing at 80%+ coverage; new tests proving cross-tenant isolation (a query for tenant A never returns tenant B's data).

## Stream 2 — Atlassian Connect

Per ADR-0010. New router `routers/atlassian.py` plus auth middleware.

- Serve `atlassian-connect.json` descriptor at `/atlassian-connect.json`.
- Lifecycle endpoints: `POST /lifecycle/installed`, `POST /lifecycle/enabled`, `POST /lifecycle/disabled`, `POST /lifecycle/uninstalled`. The `installed` handler creates/updates a `tenants` row with the shared secret Atlassian provides.
- JWT validation middleware: every authenticated request from Atlassian carries a JWT signed with the tenant's shared secret. We verify, extract `iss` (= `client_key`), and bind it as `request.state.tenant_id`.
- Replace the existing Basic-auth `JiraClient` path with one that uses OAuth/JWT credentials from the tenant row. The original API-token path stays as a `dev-mode` fallback (env-flag-gated) for local development.
- Update `routers/sync.py` to use the per-tenant credentials from the JWT context rather than env-var settings.
- Add a `descriptor_test` that asserts the descriptor passes Atlassian's schema validator (fetches the public JSON schema URL and validates against it).

Acceptance: Atlassian's developer console accepts the descriptor; lifecycle webhooks succeed against a `ngrok` or tunneled local backend during dev.

## Stream 3 — Frontend embedding

Currently the dashboard is at `/dashboard` on a standalone Next.js. To embed in Jira:

- Add the `@forge/bridge` or Atlassian Connect's `AP` JavaScript bridge (loaded from `https://connect-cdn.atl-paas.net/all.js`) for navigation/context.
- Strip the page chrome (no top-level header/nav) when rendered inside Jira; serve a "compact" variant.
- Set CSP / `frame-ancestors` headers to allow embedding from `*.atlassian.net` only. (Without this, the iframe is blocked.)
- The Jira "page" descriptor module specifies a key, location (`jira-projectPage` or `general-page`), and the URL — Atlassian iframes our `/dashboard` (or a new `/embedded` variant) inside Jira's chrome.
- Pass `client_key` and `xdm_e` query params from Jira to bind the dashboard to the correct tenant.

Acceptance: visiting the page in Jira loads our dashboard inside the iframe with the right tenant's data; opening it for a different installed tenant shows that tenant's data.

## Stream 4 — AWS infra (CDK)

Per ADR-0012 + ADR-0013. New `infra/` package, separate `pyproject.toml`.

Stacks (one stack per concern):

- `NetworkStack` — VPC + security groups + endpoints. **Phase 1 keeps RDS in a public subnet with security-group + IAM auth lockdown** to avoid NAT Gateway cost. Phase 2 moves RDS to private and adds a NAT.
- `DataStack` — RDS Postgres `db.t4g.micro` Single-AZ, KMS-encrypted, automated backups (7-day window), Secrets Manager secret for the master password.
- `ComputeStack` — ECR repos for backend + frontend; App Runner services (one per repo); Secrets Manager wiring for OAuth secrets + Atlassian shared secrets; CloudWatch log groups (14-day retention).
- `ObservabilityStack` — CloudWatch dashboards, billing alarm at $100, latency/error alarms on App Runner.

Three environments (`dev`, `staging`, `prod`) configured via `cdk.json` context + `--context env=...`. Each gets its own stack instances.

GitHub Actions workflow `.github/workflows/deploy.yml`:

1. OIDC role assumption (`aws-actions/configure-aws-credentials`).
2. `docker build` + `docker push` to ECR (immutable tags = commit SHA).
3. `cdk diff` (always, posted as PR comment).
4. `cdk deploy --all` (only on push to `main`/`develop`/`feature-*`).
5. Smoke test (`curl /healthz` against the deployed App Runner URL).

Acceptance: a fresh AWS account from zero deploys cleanly via `cdk deploy` on a `feature/*` branch and produces a working HTTPS endpoint.

## Stream 5 — Install + smoke

- Create an Atlassian Cloud developer sandbox (free).
- Upload the descriptor URL pointing at the staging environment.
- Trigger `installed` lifecycle, verify `tenants` row.
- Trigger an initial sync against test data in the sandbox.
- Verify dashboard renders, bottleneck shows up, alerts fire.
- Repeat against company's Jira instance with the production environment.

Acceptance: real bottleneck shown for the active board, with explanation, on the company's live Jira.

## Risks and how we'll handle them

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Multi-tenant refactor introduces a query that omits `tenant_id` | Medium | Critical (data leak) | SQLAlchemy event hook + Postgres RLS in Phase 2 + cross-tenant isolation test |
| App Runner cold start makes the iframe sluggish | Medium | Low (dev annoyance, not a blocker) | Hourly synthetic ping during business hours; bump to provisioned-concurrency only if user-visible |
| Atlassian Connect descriptor or JWT auth has subtle bugs | Medium | Medium (won't install) | Build against the dev sandbox first; never ship anything that doesn't pass `descriptor-validator` |
| RDS public-subnet exposure (Phase 1 cost-saving choice) | Low | Medium | Strict security group (only App Runner egress IPs); IAM auth required; rotate master password via Secrets Manager rotation |
| GitHub Actions OIDC misconfiguration | Medium | Low | Mirror the org's existing pattern exactly; deploy first to a throwaway account before pointing at the real one |
| Cost surprise (CloudWatch retention, NAT, idle Aurora etc.) | Low | Low | $100/mo billing alarm; ADR-0012 already documents the gotchas |

## Decisions still needed (small, surface-as-we-go)

These are the immediate forks I'd hit on day one. Each gets a quick decision before its stream starts:

1. **DB migration tool:** Alembic (default) or alternative.
2. **Atlassian Connect UI insertion point:** project page (per-project) or general page (instance-level)? Affects descriptor `module` choice.
3. **Frontend dashboard route for embedding:** new `/embedded` variant or reuse `/dashboard` with a query-param flag? Default: new `/embedded` variant.
4. **GitHub repo strategy:** keep `infra/` in this repo (default) or split to a sibling repo.
5. **Atlassian dev sandbox owner:** whose Atlassian account is the sandbox under?
6. **AWS account: existing or fresh?** And if existing, which account ID and what naming convention?
7. **Domain name for the app:** something like `flow-intelligence.<your-domain>.com`, or use App Runner's default `*.awsapprunner.com` for Phase 1.

## Out-of-scope reminders (so we don't drift)

- No paid tier code yet.
- No Marketplace listing yet.
- No background scheduler — sync stays manual via webhook + cron-on-deploy until Phase 2.
- No multi-account AWS Organizations.
- No SSO into the dashboard — Atlassian Connect's JWT *is* the auth; dashboard is only reachable from inside Jira.
