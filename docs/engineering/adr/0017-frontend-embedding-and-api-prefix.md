# 0017 — Frontend embedding architecture and `/api` route prefix

- **Status:** superseded by [ADR-0019](./0019-pivot-to-forge.md) on 2026-05-02
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #atlassian #routing #frontend #security

> **Superseded.** Forge serves the frontend itself (Custom UI bundle uploaded to Forge); there is no Next.js front-door and no proxy to FastAPI. The `/api` prefix on the backend stays useful as a routing convention. See [ADR-0019](./0019-pivot-to-forge.md).

## Context and problem statement

Phase 1 ships as an Atlassian Connect app whose dashboard is an iframe inside Jira. Atlassian's descriptor accepts a single `baseUrl` for the app — every URL it speaks (descriptor, lifecycle webhooks, API, iframe page) lives under that base. Our codebase has two separate services: a Next.js frontend (`frontend/`) and a FastAPI backend (`backend/`). Picking the baseUrl forces a routing decision, and the JWT `qsh` check makes naïve proxying (which strips a path prefix on the way to the backend) cause every API call to fail signature verification.

This ADR records:
- Which service is the "front door" Atlassian sees
- How paths are organized so `qsh` canonicalization works through the proxy
- How the iframe authenticates API calls back to our backend
- The `context-qsh` allowance that iframe SPAs need

## Considered options

- **A. Drop Next.js, build a static SPA served by FastAPI.** Cleanest single-service architecture, but throws away ~600 LOC and re-introduces a new build tool decision.
- **B. CloudFront in front of both services.** Production-grade, but adds non-trivial infra for Phase 1 and requires operating a CDN config.
- **C. Next.js as front door, transparent proxies to FastAPI for Atlassian-facing paths, `/api` prefix on FastAPI app routes so the proxy doesn't need to rewrite paths.**

## Decision

**Option C.**

### baseUrl

Atlassian's descriptor `baseUrl` is the **Next.js URL**. Next.js handles:

- `/embedded/dashboard` — directly (its own page; HTML shell + JS bundle)
- `/atlassian-connect.json` — proxied to `${BACKEND_URL}/atlassian-connect.json` (path preserved)
- `/lifecycle/:path*` — proxied to `${BACKEND_URL}/lifecycle/:path*` (path preserved)
- `/api/:path*` — proxied to `${BACKEND_URL}/api/:path*` (path preserved)
- `/healthz` — proxied (for liveness)

Path preservation through the proxy is what makes `qsh` work: the JWT was minted for `/api/insights`, the request hits Next.js as `/api/insights`, gets forwarded to FastAPI as `/api/insights`, and the middleware computes `qsh` for the same string. Match.

### `/api` prefix on FastAPI app routes

Application routes (`/sync`, `/issues`, `/metrics`, `/insights`, `/alerts`) move to `/api/sync`, `/api/issues`, etc. via a single `APIRouter(prefix="/api")` in `app.main.create_app`. **`/atlassian-connect.json`, `/lifecycle/*`, and `/healthz` stay at the root** — Atlassian's descriptor cannot have an `/api` prefix.

### Iframe authentication

The embedded page (`/embedded/dashboard`) is just a static HTML shell that:

1. Loads Atlassian's `https://connect-cdn.atl-paas.net/all.js` bridge.
2. Renders a client React component that polls for `window.AP` to be ready.
3. Calls `AP.context.getToken()` for each API request to mint a fresh JWT.
4. Sends `Authorization: JWT <token>` on every `/api/*` call.

Tokens minted by `AP.context.getToken()` carry **`qsh: "context-qsh"`** — a static sentinel, not a per-request hash. The iframe can't compute `qsh` itself (no shared secret on the client). Our `JWTAuthMiddleware` accepts `context-qsh` (passing `accept_context_qsh=True` to `verify_token`). Lifecycle handlers do **NOT** allow `context-qsh` — they keep canonical-qsh-only.

### CSP for the iframe

`frame-ancestors https://*.atlassian.net https://*.jira.com` is set on every `/embedded/*` response by `next.config.mjs`. This is the modern replacement for `X-Frame-Options` and the only thing keeping the iframe load secure against arbitrary sites embedding our dashboard.

## Consequences

**Positive**
- Existing components (InsightCard, BottleneckPanel, AlertsList, TrendsList) and the Tailwind theme are reused unchanged. The embedded dashboard is one new client component plus a server-rendered shell.
- The Next.js dev server still works for local non-iframe smoke tests at `/dashboard` (legacy, unauthenticated, no AP bridge).
- `qsh` canonicalization works through the proxy because paths match exactly. We don't need any header rewriting or path manipulation.
- Tests for the JWT middleware now cover the `context-qsh` allowance explicitly; the lifecycle suite proves it's still rejected for webhooks (canonical qsh only).
- One App Runner service per concern (frontend, backend) — clean ownership and scaling.

**Negative**
- `context-qsh` weakens replay defense for iframe API calls: a captured token is valid against any `/api/*` endpoint until it expires. Mitigations: tokens are short-lived (~60s), HTTPS in production, and the bounded window. Atlassian's design decision; we're matching it.
- Two AWS services (Next.js + FastAPI) instead of one. Slightly more cost and ops surface than a CloudFront-fronted single service. Acceptable for Phase 1; ADR-0012's cost ceiling already covered this.
- The iframe page-load `?jwt=` token is currently ignored on the Next.js side (we don't validate it before serving HTML). The HTML itself contains no tenant data; data fetching happens client-side via `AP.context.getToken()`. So this is intentional and safe — but documented here so a future reader doesn't think it's a bug.

**Neutral**
- The legacy `/dashboard` route (server-rendered, calls FastAPI directly) is kept for local dev convenience. It's not exposed to Atlassian and not deployed publicly. Will likely be removed once `/embedded/dashboard` is verified end-to-end.

## What we explicitly chose NOT to do

- **Did not add the `?jwt=` page-load token to the FastAPI middleware skip-list-for-Next.js.** The FastAPI never sees `/embedded/*`; Next.js serves it. So no skip-list change needed there.
- **Did not introduce a separate "iframe API" with relaxed auth.** Existing `/api/*` accepts `context-qsh`; that IS the iframe API.
- **Did not validate the page-load JWT on the Next.js side.** The page is static HTML; tenant resolution happens via `AP.context.getToken()` on subsequent API calls.

## Notes

If/when we move to a CloudFront-fronted single service later, this ADR is superseded — `baseUrl` would become the CloudFront URL, and the `/api` prefix on FastAPI stays useful as a routing key in the CDN. Path preservation is durable across that future migration.
