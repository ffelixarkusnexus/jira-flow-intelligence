# Architectural Decision Records

This directory captures decisions worth remembering. Format: [MADR](https://adr.github.io/madr/).

## Index

| ID | Title | Status |
|----|-------|--------|
| [0001](0001-modular-monolith.md) | Adopt a modular monolith with an explicit service layer | accepted |
| [0002](0002-stack-fastapi-nextjs.md) | Accept FastAPI + Next.js as the application stack | accepted |
| [0003](0003-sqlite-default-postgres-optional.md) | Default to SQLite, support PostgreSQL via optional extra | accepted |
| [0004](0004-utc-datetime-decorator.md) | Round-trip UTC tz-aware datetimes via a TypeDecorator | accepted |
| [0005](0005-idempotency-delete-and-replace.md) | Idempotency via per-issue delete-and-replace | accepted |
| [0006](0006-deterministic-engine-ai-text-only.md) | Deterministic engine; AI is text-translation only | accepted |
| [0007](0007-bottleneck-scoring.md) | Bottleneck scoring formula and tie-break rule | accepted |
| [0008](0008-alert-idempotency-key.md) | Alert idempotency via composite key | accepted |
| [0009](0009-tooling-baseline.md) | Engineering tooling baseline | accepted |
| [0010](0010-distribution-atlassian-connect.md) | Distribute as an Atlassian Connect app, private install first | superseded by [0019](0019-pivot-to-forge.md) |
| [0011](0011-multi-tenancy-row-level.md) | Multi-tenancy via row-level `tenant_id` in a single Postgres | accepted |
| [0012](0012-cloud-aws-app-runner-rds.md) | Run on AWS: App Runner + RDS Postgres, single account to start | accepted |
| [0013](0013-iac-cdk-python.md) | Use AWS CDK (Python) for infrastructure-as-code | accepted |
| [0014](0014-multi-tenant-schema-details.md) | Multi-tenant schema details (composite PKs, FK CASCADE, SQLite enforcement, TenantContext) | accepted |
| [0015](0015-atlassian-connect-lifecycle.md) | Atlassian Connect lifecycle handling (tiered auth, qsh, hard-delete on uninstall) | superseded by [0019](0019-pivot-to-forge.md) |
| [0016](0016-jwt-auth-middleware.md) | JWT auth middleware and skip list (fail-closed, app factory for testability) | superseded by [0019](0019-pivot-to-forge.md) |
| [0017](0017-frontend-embedding-and-api-prefix.md) | Frontend embedding architecture and `/api` route prefix (iframe + AP bridge + context-qsh) | superseded by [0019](0019-pivot-to-forge.md) |
| [0018](0018-deploy-workflow-and-ecr-split.md) | Deploy workflow and EcrStack split (chicken-and-egg, image_tag context, smoke test) | accepted |
| [0019](0019-pivot-to-forge.md) | Pivot from Atlassian Connect to Forge | accepted |
| [0020](0020-app-runner-sunset-defer-migration.md) | App Runner sunset: stay through Phase 2, plan migration in Phase 3 | accepted |
| [0032](0032-backfill-browser-loop-supersedes-0025.md) | Backfill ships as a UI-driven browser loop (supersedes 0025's delivery mechanism) | accepted |
| [0038](0038-best-in-category-defaults-and-done-terminal-merge.md) | Best-in-category-defaults hierarchy (CLAUDE.md rule #10) + safe-default Done→Terminal merge with opt-in independent lists | accepted |
| [0040](0040-resend-for-customer-facing-email.md) | Resend for customer-facing transactional email; SES retained for maintainer-self path (post-AWS-SES-denial pivot) | accepted |

> **Index gap (2026-05-25):** entries for 0021–0031 are missing from this table. The ADR files themselves exist in this directory; only the index row is absent. The README convention requires adding an entry in the same PR as a new ADR, so the gap is real hygiene debt. Out of scope for the 0032 ship; close as a separate cleanup pass.

## Adding a new ADR

1. Copy `0000-template.md` to `NNNN-kebab-case-title.md` using the next available number.
2. Status starts at `proposed`. It moves to `accepted` after review.
3. Add an entry to the table above in the same PR.
4. Don't edit a merged ADR's decision text. If the decision changes, write a new ADR that supersedes it and update the old one's status to `superseded by [NNNN](NNNN-...md)`.

Keep ADRs short. The audience is "future me" who has forgotten everything except the question.
