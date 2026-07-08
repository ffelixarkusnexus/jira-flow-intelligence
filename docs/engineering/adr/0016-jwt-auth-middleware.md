# 0016 — JWT auth middleware and skip list

- **Status:** superseded by [ADR-0019](./0019-pivot-to-forge.md) on 2026-05-02
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #security #auth #atlassian

> **Superseded.** Forge sends a Forge Invocation Token (asymmetric RS256 JWT) signed by Atlassian; we validate against a public JWKS, not a per-tenant shared secret. No `qsh`, no `context-qsh`. The fail-closed-middleware-with-skip-list pattern survives; the verification logic is rewritten. See [ADR-0019](./0019-pivot-to-forge.md).

## Context and problem statement

ADR-0011 + 0014 made `tenant_id` mandatory on every business query. ADR-0015 set up the lifecycle endpoints that bring tenants into existence. We now need a per-request mechanism that:

- Verifies every API call comes from a legitimate Atlassian-signed party.
- Resolves which tenant the call is for.
- Binds that tenant to the request so downstream code can read `ctx.tenant_id` without re-parsing the JWT.
- Doesn't gate the few endpoints that must be unauthenticated (descriptor, lifecycle, healthz).

This needs to land before the dashboard iframe (Stream 5) because the iframe page-load passes a JWT in the URL, not a body, and only middleware sees the URL.

## Considered options

- **A. FastAPI dependency on every router** — explicit per-route, but easy to forget on a new route. Failure mode is silent: a route without the dep would be unauthenticated.
- **B. Single ASGI middleware with a skip list** — runs before any route, fail-closed by default. Skip list is allowlist-by-prefix.
- **C. Mark protected routes with a custom `Depends(require_tenant)` and reject elsewhere via separate middleware** — combines explicitness with fail-closed default. More moving parts.

## Decision

**Option B**: a `JWTAuthMiddleware` (Starlette `BaseHTTPMiddleware`) that runs on every HTTP request not matching a path in `SKIP_PATH_PREFIXES`. Failure is 401 with a clear `detail`.

### Skip list

```python
SKIP_PATH_PREFIXES = (
    "/healthz",                  # liveness probe
    "/atlassian-connect.json",   # public descriptor (Atlassian fetches at install)
    "/lifecycle/",               # handles its own auth (ADR-0015)
    "/docs",                     # FastAPI auto-docs
    "/redoc",                    # FastAPI auto-docs
    "/openapi.json",             # OpenAPI schema
)
```

Anything not on this list requires a JWT — no exceptions.

### JWT acceptance

Two transport mechanisms (in priority order):

1. `Authorization: JWT <token>` or `Authorization: Bearer <token>` — server-to-server calls, AP.context.getToken() calls from a mounted iframe.
2. `?jwt=<token>` query parameter — Atlassian's iframe page-load mechanism. The `jwt` parameter is excluded from the qsh canonicalization (handled by `compute_qsh`).

### Verification flow

1. Decode the token without verification to read `iss`. If absent → 401.
2. Look up `tenants` by `iss`. If missing → 401 "Unknown tenant". If `enabled=False` → 401 "Tenant disabled".
3. Detach the tenant ORM object from the lookup session (`db.expunge`) so the closing of that session doesn't expire its attributes. Tenant has no lazy-loaded relationships, so detached access is safe.
4. Compute `expected_qsh` from the request's actual method + path + sorted query (minus `jwt`).
5. Verify the JWT signature with HS256 against the tenant's `shared_secret`, plus `iat` freshness, `exp`, and `qsh` match (per ADR-0015's `verify_token`).
6. Bind `request.state.tenant`. Hand off to `call_next`.

### Middleware ordering

Starlette runs the LAST-added middleware OUTERMOST. We add `JWTAuthMiddleware` FIRST and `CORSMiddleware` SECOND, so CORS ends up outermost. This means:

- OPTIONS preflights are handled by CORS without going through auth.
- CORS headers are added to every response — including 401s from the JWT middleware.

### Testability — app factory

`app.main.create_app(with_jwt_middleware: bool = True, ...)` lets tests build a no-middleware app instance. Router tests use that path and override `current_tenant_context` directly to inject a known tenant. The middleware is exercised in its own suite (`test_jwt_middleware.py`) against a stub protected route. This keeps the two concerns testable in isolation.

## Consequences

**Positive**
- Fail-closed default: a new route added later is automatically protected unless explicitly added to the skip list.
- The skip list is data, easy to audit. Tests assert known skip-listed paths bypass and known protected paths reject.
- Both header-based and query-param-based JWTs are handled, covering API calls and iframe page loads.
- The `qsh` check binds tokens to specific endpoints — a JWT minted for `GET /issues` cannot be replayed against `GET /metrics`.
- The detach-and-bind pattern means downstream code reads `tenant.client_key` etc. without holding a session open.

**Negative**
- The middleware does an unverified-decode to peek at `iss` before signature verification. This creates a tiny timing oracle: an attacker can probe whether a `clientKey` exists by sending a malformed JWT with that iss. Mitigation: 401 in either case; latency difference is small. Acceptable for Phase 1; revisit if it ever shows up in a threat model.
- Two integration test suites must be kept honest about what they cover. `test_jwt_middleware.py` is the auth contract; `test_routers.py` is the routing contract. A bug in the wiring between them (e.g., a route added without the dep AND not on the skip list) is caught only by an end-to-end smoke test that goes through the production app. We add a smoke test for this case in Stream 8 (sandbox install).
- Middleware needs a session to look up the tenant. We open one per request just for this check. At Phase 2 scale we may want a small in-memory cache of `clientKey → shared_secret` with a short TTL.

**Neutral**
- Adding a new skip-listed prefix requires updating `SKIP_PATH_PREFIXES` and adding a test asserting the path bypasses. The pair-update is enforced by code review only.

## Notes

The middleware does not yet accept Atlassian's special `qsh: "context-qsh"` value used for iframe page-load tokens. Stream 5 (frontend embedding) adds that as a per-route allowance on the dashboard route only — supersedes will be a small ADR addendum, not a full new ADR.

If we ever introduce a service-to-service caller that doesn't carry an Atlassian JWT (e.g., a scheduler hitting `/sync` directly), it gets its own auth path and ADR.
