# 0011 — Multi-tenancy via row-level `tenant_id` in a single Postgres

- **Status:** accepted
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #data #architecture #multi-tenant

## Context and problem statement

Once we install in multiple Atlassian Cloud instances, one DB will serve multiple tenants. The current schema (ADR-supercedes territory: 0001/0005) has no `tenant_id` — it assumes one customer per process. We need to choose an isolation model before writing the migration.

## Considered options

- **A. Single Postgres, row-level isolation via `tenant_id` column on every business table.**
- **B. Schema-per-tenant** (one Postgres schema per Atlassian instance).
- **C. Database-per-tenant** (one RDS instance per tenant — or container DB per tenant on shared instance).

## Decision

**Single Postgres database with `tenant_id` (FK to `tenants`) on every business table. Composite primary keys `(tenant_id, jira_id)` for entities whose IDs come from Jira (which are unique per-instance, not globally unique).**

The new `tenants` table is keyed by Atlassian `client_key` (the install identifier Atlassian sends in the `installed` lifecycle webhook), with `cloud_id`, `base_url`, `shared_secret` (for JWT signing), `installed_at`, `last_sync_at`, and `plan` columns.

## Consequences

**Positive**
- Lowest operational complexity. One DB, one set of backups, one migration to run.
- Cheapest at our scale (Phase 1: one tenant; Phase 2: tens; we don't expect hundreds before re-evaluating).
- Postgres row-level security (RLS) is available as a defense-in-depth check we can add later without schema changes.
- ORM-level enforcement via SQLAlchemy event hooks (`before_compile`) is straightforward — we'll add a tenant-aware base query that asserts every read filters on `tenant_id`.

**Negative**
- A bug in the service layer that omits `tenant_id` from a `WHERE` clause is a cross-tenant data leak. We mitigate via:
  1. A unit test that fails if any new query helper omits `tenant_id`.
  2. Postgres RLS policies as a hard gate (added in Phase 2 before Marketplace listing).
  3. Code review checklist item.
- Backups, restores, and exports are all-or-nothing. If a tenant requests data deletion (GDPR-style), we run a tenanted purge — straightforward but real work.
- A noisy neighbor (one tenant with 100k issues) impacts query performance for others. Mitigated by `(tenant_id, ...)` composite indexes and connection pool sizing.

**Neutral**
- Migrating to schema-per-tenant later is mechanical (one-time copy + DDL change). We don't burn this option by starting with row-level.

## Schema notes

Concrete shape (full migration spec lives in the Phase 1 plan):

- `tenants` (PK = `client_key TEXT`)
- `issues` (PK = `(tenant_id, id)`); `id` is the Jira issue ID (not globally unique)
- `transitions`, `time_slices`, `metrics_issue`, `metrics_status_window`, `alerts`, `alert_rules` — all gain `tenant_id NOT NULL REFERENCES tenants(client_key)`; unique constraints prefixed with `tenant_id`.

Composite PK `(tenant_id, id)` is the lift; FKs from child tables become composite. SQLAlchemy 2.x handles this cleanly.

## When to revisit

- A specific enterprise customer contractually requires hard data isolation → move them to schema-per-tenant or a dedicated DB.
- Aggregate p95 query latency degrades because of cross-tenant table size → consider partitioning by `tenant_id` or moving to schema-per-tenant.
- We onboard >50 tenants → re-evaluate.

## Notes

ADR-0005 (idempotency via delete-and-replace per issue) becomes "delete-and-replace per `(tenant_id, issue_id)`" — semantically identical, just one column more. Tests in `test_pipeline_e2e.py` will be parametrized over `tenant_id`.
