# 0026 — Postgres Row-Level Security as defense-in-depth

- **Status:** accepted
- **Date:** 2026-05-07
- **Decision-makers:** the maintainer
- **Tags:** #security #multi-tenant #defense-in-depth

## Context and problem statement

ADR-0011 (multi-tenant schema), ADR-0014 (multi-tenant details), and ADR-0019 (Forge auth) established that every query against a tenanted table MUST filter on `tenant_id`. The service layer is the single source of truth for tenant isolation today. ADR-0011 explicitly deferred Postgres Row-Level Security as a "defense in depth" item to be revisited before public Marketplace listing.

Production hardening and the Atlassian security review make that revisit due. The gap: a future bug — a forgotten WHERE clause, a custom report endpoint that bypasses the standard service helper, an admin-only API that uses a raw query — could leak cross-tenant data. RLS makes the database itself enforce the constraint; an app-layer mistake fails closed instead of silently returning another tenant's rows.

## Considered options

- **A. Skip RLS.** Trust the app layer indefinitely. Lower complexity, higher leak risk. Atlassian's security review process is likely to flag it.
- **B. Per-table policies pinned to a session GUC** (`current_setting('app.current_tenant')`). Each request `SET LOCAL`s the tenant identifier; policies on every tenanted table compare `tenant_id` against the GUC. Standard Postgres pattern.
- **C. Per-tenant Postgres roles** with table-level GRANT scoping. Cleaner conceptually but requires a role per tenant — operationally painful at scale (every install creates a role, role count grows with customers).
- **D. Schema-per-tenant.** Strong isolation but doubles the schema migration burden and breaks any cross-tenant analytics in the future.

## Decision

**Option B**, shipped as part of production hardening on 2026-05-07.

### Migration `f23b3b7a9fce`

Enables RLS on every tenanted table:

```
issues, transitions, time_slices, metrics_issue, metrics_status_window,
alerts, alert_rules, wip_limits, sprints, issue_sprints
```

`tenants` itself is excluded — the auth middleware needs to look up the tenant row before a tenant identifier is known, so the table that maps install IDs to tenants must remain readable without the GUC set.

Each table gets:

```sql
ALTER TABLE "<table>" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "<table>" FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "<table>"
  USING (tenant_id = current_setting('app.current_tenant', true))
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
```

`FORCE ROW LEVEL SECURITY` makes the policy apply even to the table owner. Without `FORCE`, the role that created the tables (which is also the app's runtime role today) bypasses RLS — making the policy inert.

The migration is a no-op on SQLite via `op.get_bind().dialect.name == "postgresql"` check. Tests run on SQLite and rely on the existing app-side filtering + the `test_tenant_isolation.py` and `test_project_isolation.py` suites.

### Session binding

`backend/app/core/deps.py:current_tenant` runs `SET LOCAL app.current_tenant = :t` on the request's session immediately after pulling the tenant out of `request.state`. `SET LOCAL` is transaction-scoped; FastAPI's per-request session lifecycle means each request operates in a fresh transaction, so there's no cross-request leak. SQLite-side runs are gated by the same dialect check.

`current_setting('app.current_tenant', true)` returns NULL when the GUC isn't set (the `true` argument makes it permissive instead of raising). The policy predicate `tenant_id = NULL` always evaluates to false, so any query path that fails to set the GUC sees zero rows. **Fail-closed**: an unauthenticated request, a background script that forgets the GUC, or a developer running raw SQL via the app role gets nothing back.

### What this protects against

- Future code paths that skip the standard service helpers and write `select(Issue).where(...)` without `tenant_id == ctx.tenant_id`.
- Bug-introduced cross-tenant queries in admin tooling.
- Developers running interactive psql sessions that forget the SET LOCAL — they get no rows from tenanted tables until they pin a tenant.

### What this does NOT protect against

- The auth middleware itself. If something tricks the middleware into binding the wrong tenant, RLS happily applies that wrong tenant's policy. App-layer auth still has to be correct.
- The `tenants` table — exempted by design, so a future bug in a query against it could leak install metadata across customers. Mitigation: keep the tenant row read pattern narrow (only the middleware reads it; lifecycle handlers write).
- Migrations themselves run as the app role with FORCE on, meaning future data migrations that operate across tenants would need to either (a) bypass RLS by running as a superuser role (RDS lets us configure one), or (b) explicitly SET app.current_tenant per tenant in a loop. Schema-only migrations are unaffected because RLS doesn't gate DDL.

## Consequences

**Positive.**

- Atlassian's security review has a concrete answer to "what stops a forgotten WHERE clause from leaking data?" — Postgres itself.
- A class of future bugs becomes impossible to write without explicit superuser bypass.
- Closes the gap ADR-0011 left open.
- No customer-visible behavior change. No new permission grants. No cost.

**Negative.**

- Future data migrations (rare but possible) need extra care. Documented in this ADR.
- One more failure mode to recognize: "I deployed a new endpoint and it returns no data" probably means the GUC isn't being set on that path.
- `current_tenant` dependency now writes to the session. Tests that override `current_tenant_context` (the standard pattern in test_routers.py) bypass this — fine — but tests that use the real `current_tenant` would need a Postgres test fixture, which we don't have today. Acceptable; SQLite tests cover the application logic, RLS is a Postgres-runtime guard.
