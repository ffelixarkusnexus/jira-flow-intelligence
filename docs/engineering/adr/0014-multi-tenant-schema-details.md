# 0014 — Multi-tenant schema details

- **Status:** accepted
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #data #multi-tenant #correctness

## Context and problem statement

ADR-0011 committed us to row-level multi-tenancy via `tenant_id` on every business table. This ADR records the concrete schema choices made during the refactor — they're load-bearing and a future reader will want to know why each was picked.

## Decisions

### 1. `tenants` table is keyed by Atlassian `client_key`

The `installed` lifecycle webhook from Atlassian sends a stable `clientKey`. We use it directly as the primary key (text). No surrogate UUID. The `cloud_id` is stored as a regular column for queries that join on Atlassian's instance UUID but is not the PK because some older Atlassian Connect installs don't populate it consistently.

### 2. Composite PK `(tenant_id, id)` on `issues`

Jira issue IDs are unique per Atlassian instance, not globally. Tenant A's `10001` is a different issue than tenant B's `10001`. Composite PK encodes this correctly.

Considered: a synthetic UUID PK + UNIQUE on `(tenant_id, jira_id)`. Rejected because the natural identity is the composite, the surrogate adds a column that everything has to look up by, and SQLAlchemy 2.x handles composite PKs cleanly.

### 3. `transitions` and `time_slices` use composite FK to `issues`

Both use `ForeignKeyConstraint(["tenant_id", "issue_id"], ["issues.tenant_id", "issues.id"], ondelete="CASCADE")` at the table level (declared in `__table_args__`). The ORM `relationship` between `Issue` and these children resolves the join automatically because there's exactly one composite FK.

### 4. CASCADE deletes on every tenant-scoped FK

`ondelete="CASCADE"` everywhere a tenant-scoped FK is declared. Deleting the `tenants` row removes all of that tenant's issues, transitions, slices, alerts, rules, and metrics. The `uninstalled` lifecycle webhook (Stream 2) just deletes the tenant row; cleanup is automatic.

The cross-tenant isolation test suite explicitly asserts cascade behavior end-to-end (`test_dropping_a_tenant_cascades_its_data`).

### 5. SQLite FK enforcement is opt-in; we opt in

SQLite ships with FK constraints disabled at the connection level. CASCADE wouldn't fire without `PRAGMA foreign_keys = ON`. We register a `connect` event listener on every SQLite engine (`backend/app/db/session.py::_enable_sqlite_foreign_keys`) plus the test conftest. Postgres enforces FKs by default; the listener is a no-op there.

### 6. Per-tenant config lives on the `tenants` row

`active_statuses`, `done_statuses`, and the per-tenant bottleneck thresholds are JSON/Float columns on `tenants` with `NULL` meaning "inherit the `Settings` default." `TenantContext` is the read-side accessor and merges the two.

Alternative considered: separate `tenant_config` table. Rejected as YAGNI — we have ~5 config fields and they're 1:1 with the tenant.

### 7. `TenantContext` is the seam, not raw `Settings`

Every service function takes `ctx: TenantContext` instead of `settings: Settings`. This is the single most important code-level change — it forces the caller to think about which tenant. The `_Thresholds` Protocol in `insight_service` lets it accept either `Settings` (for the CLI / direct unit tests with no DB) or `TenantContext` (production), without code duplication.

### 8. Routers extract tenant via `current_tenant_context` dependency only

There is no header-based tenant override. The dependency reads `request.state.tenant`, which is set by the JWT auth middleware (Stream 2). For tests, we override the dependency directly via `app.dependency_overrides`. **There is no `X-Tenant-Key` header path or similar in production code** — that would be a backdoor. This is the contract.

### 9. Idempotency keys are unchanged in shape but tenant-scoped in their unique constraints

Per ADR-0005 and ADR-0008, alert and transition idempotency keys retain their existing structure (`{issue_id}|{status}|{slice_start_ts}` etc.). The unique constraints on those tables now lead with `tenant_id`, so the same key string under two tenants is two distinct rows. No code changes needed in the alert evaluator.

## Consequences

**Positive**
- Cross-tenant isolation is provably correct — `test_tenant_isolation.py` covers the four scenarios that matter (dual ingestion, scoped reads, scoped alert eval, cascade delete).
- The shape works on both SQLite (dev) and Postgres (prod) without dialect-specific code.
- Adding a new tenant-scoped table is one column + one FK + a couple of unique-constraint updates — formulaic.
- Refactor preserved 87% coverage; no service logic regressed.

**Negative**
- Composite PKs in SQLAlchemy require `session.get(Model, (tenant_id, id))` instead of `session.get(Model, id)`. We accept the boilerplate for correctness.
- Composite FKs require `ForeignKeyConstraint` at table level (not in column definition). One-time learning cost.
- Coverage on `db/session.py` dropped a bit because the Postgres branch isn't exercised in CI yet. Tracked in the Phase 1 plan's deferred work.

## Notes

The cross-tenant isolation test (`backend/tests/test_tenant_isolation.py`) is the load-bearing guard for this ADR. If this ADR's decisions ever conflict with reality, that test is where the conflict surfaces. Don't disable it without a successor ADR.

Future hardening — Postgres Row-Level Security policies as a hard gate — is deferred to Phase 2 and tracked in the Phase 1 plan's "deferred work" table.
