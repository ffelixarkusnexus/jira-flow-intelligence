# 0019 — Pivot from Atlassian Connect to Forge

- **Status:** accepted
- **Date:** 2026-05-02
- **Decision-makers:** the maintainer
- **Tags:** #distribution #architecture #strategy #atlassian
- **Supersedes:** [ADR-0010](./0010-distribution-atlassian-connect.md), [ADR-0015](./0015-atlassian-connect-lifecycle.md), [ADR-0016](./0016-jwt-auth-middleware.md), [ADR-0017](./0017-frontend-embedding-and-api-prefix.md)

## Context and problem statement

ADR-0010 picked Atlassian Connect on the assumption that "deprecation timelines historically run multiple years with grace periods." Two facts surfaced after the deploy:

1. **17 September 2025**: Atlassian stopped accepting new Connect listings on the Marketplace. New apps must be Forge.
2. **December 2026**: Connect end of support — only critical security fixes after that.

Concurrently, our `your-site.atlassian.net` instance shows: *"Development Mode no longer supports installing private apps using an app descriptor URL."* The descriptor-URL upload path that the Phase 1 install plan was built around has been removed from regular Cloud sites; only purpose-built free developer instances at `go.atlassian.com/cloud-dev` may still support it, with no SLA on how long.

The Marketplace listing was the Phase 2 goal. Phase 2 is no longer possible on Connect. Continuing on Connect ships an app that has no path forward and will be force-rewritten in ~7 months regardless.

## Considered options

- **A. Stay on Connect; install privately in our company Jira.** Test-only path, dies in Dec 2026.
- **B. Pivot to Forge now, before any install.** One rewrite, on the platform that has a future.
- **C. Stay on Connect for the company install AND start a parallel Forge port.** Two systems to maintain during a window in which Connect is dying anyway.
- **D. Abandon the plugin format and ship as a SaaS with OAuth (3LO).** No in-Jira UI, doesn't meet the original goal.

## Decision

**Option B: pivot to Forge now.** Before the private-install work completes. The Connect deploy stack stays running but is no longer the path forward; the Connect-specific code is removed once the Forge equivalents pass.

Architecture:
- **Forge UI (Custom UI)** — the existing Next.js dashboard becomes a Custom UI resource bundle in the Forge app. We keep React + Tailwind. We trade `next/server` runtime features for a static export delivered through Forge's iframe.
- **Forge Remote** — the FastAPI backend on AWS App Runner stays. Forge calls it via `fetch()` from a resolver, passing a Forge Invocation Token (FIT) — an asymmetric JWT signed by Atlassian.
- **Backend JWT validation** — replaces our HS256-shared-secret middleware (ADR-0016) with JWKS-based RS256 validation against `https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json`, audience-checked against our Forge App ID.
- **Tenant identity** — comes from the `app.installation.id` and `cloudId` claims in the FIT, not the Connect `clientKey`. The `tenants` table grows a `forge_installation_id` column; existing per-tenant business logic (ADR-0011, 0014) is unchanged.
- **Lifecycle** — Forge's install/uninstall events flow through Forge resolvers, not our backend's `/lifecycle/*` endpoints. The shared-secret rotation defense (ADR-0015) becomes irrelevant because there is no shared secret.
- **Distribution** — installation link from the Forge developer console. No descriptor URL upload, no Marketplace listing required for the company install. Marketplace listing in Phase 3 once the product is validated.

## Consequences

**Positive**
- A path forward. Forge is where the platform is going; new modules and features ship there only.
- The deterministic engine survives unchanged — slicing, metrics, bottleneck scoring, alerts, ingestion, AI explanations, all of `backend/app/services/`. ADRs 0001–0009, 0011, 0014 (the schema parts) all stand.
- AWS infrastructure mostly survives — App Runner backend, RDS Postgres, ECR, observability. ADR-0012, 0013, 0018 stand. We retire one App Runner service (the Next.js front-door); the frontend bundle ships inside the Forge app instead.
- JWT validation simplifies: asymmetric signature against a public JWKS, no per-tenant shared secret in our database, no `qsh` canonicalization, no `context-qsh` sentinel.
- One distribution mechanism (Forge installation link → Marketplace listing) covers both the company install and the public listing path.

**Negative**
- Real rewrite cost. Frontend module config, lifecycle handlers, JWT middleware, descriptor route, lifecycle router, and a chunk of tests all go away or move. Estimated ~1 week of focused work.
- ADR-0010's explicit "if we ever migrate to Forge, that ADR supersedes this one" warning is now cashed in. We accept it.
- Forge has stricter platform constraints: Custom UI runs in a sandboxed iframe, network egress from the Forge runtime is limited, and `console.*` is the only debug surface (no Forge-side log shipping to CloudWatch). Our backend remains observable through CloudWatch as before.
- Forge Custom UI does not support Next.js SSR/server components — we ship a static export. Server-only patterns currently in `frontend/app/api/**` and `frontend/app/lifecycle/**` (the proxy routes) are removed; the bundle calls our backend directly via authenticated `requestRemote` from `@forge/bridge`.
- The shared-secret-spoof scenario in the runbook ("Re-install fails with 401 — `JWT required for existing tenant`") goes away, along with its remediation steps. We delete those.

**Neutral**
- The free `go.atlassian.com/cloud-dev` developer instance remains the recommended sandbox; it just speaks Forge install links instead of descriptor URLs.

## What we explicitly chose NOT to do

- **Did not pursue "Connect on Forge"** (the wrapper that lets some legacy Connect apps run inside a Forge shell). It is for migrating existing listed Marketplace apps, not for net-new development, and it inherits all of Connect's sunset timeline.
- **Did not pursue Forge UI Kit** instead of Custom UI. UI Kit is React-but-not-React (a constrained component set). Our existing dashboard, charts, and Tailwind styling map cleanly to Custom UI; UI Kit would mean rewriting the components.
- **Did not move the FastAPI engine into Forge.** Forge runtime is JS/TS only and has request-time and storage limits unsuitable for the deterministic ingestion pipeline. Forge Remote was designed for exactly this case.

## Migration plan

See `docs/engineering/plans/phase-2-forge-migration.md`.

## Notes

ADRs 0010, 0015, 0016, 0017 are superseded by this one. They document a real piece of system history (the Connect implementation we built and ran in production briefly) and are kept for context, with a `Status: superseded` banner. ADR-0014 (multi-tenant schema) is amended (not superseded): the `tenants` table gains `forge_installation_id` and `cloud_id`, loses `shared_secret`. That amendment lands inline in 0014 when the migration is written.
