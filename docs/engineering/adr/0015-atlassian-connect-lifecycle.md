# 0015 — Atlassian Connect lifecycle handling

- **Status:** superseded by [ADR-0019](./0019-pivot-to-forge.md) on 2026-05-02
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #security #atlassian #lifecycle

> **Superseded.** Forge install/uninstall flows through Forge resolvers and product events, not our `/lifecycle/*` endpoints. There is no shared secret to rotate or spoof, so the "tiered auth on `installed`" defense is moot. See [ADR-0019](./0019-pivot-to-forge.md).

## Context and problem statement

Per ADR-0010 we ship as an Atlassian Connect app. Connect's bootstrap is a four-event lifecycle (`installed` / `enabled` / `disabled` / `uninstalled`) plus per-request JWT auth. The lifecycle has subtle security pitfalls — a naive implementation lets an attacker spoof an `installed` webhook to overwrite a real tenant's shared secret. This ADR records the auth model we picked.

## Considered options

- **A. Trust every lifecycle webhook unconditionally** — simplest, completely insecure.
- **B. Require JWT on every lifecycle webhook** — but the FIRST `installed` for a clientKey can't have a JWT (Atlassian doesn't yet have a shared secret).
- **C. Tiered auth: first `installed` open, subsequent `installed` and all other events JWT-authenticated against the existing shared secret.** This is the pattern atlassian-connect-express (ACE) ships.

## Decision

**Option C, with tightening:**

- `installed` for an unknown `clientKey`: accept without JWT. Persist the tenant row with the received `sharedSecret`.
- `installed` for a known `clientKey`: require an `Authorization: JWT <token>` header signed by the **existing** shared secret. The JWT's `iss` must equal the existing `clientKey`. Only after that verification do we rotate the shared secret to the new value in the body.
- `enabled` / `disabled` / `uninstalled`: require valid JWT against the existing shared secret. Unknown `clientKey` returns 401.
- Every JWT must additionally have:
  - Valid HS256 signature (handled by `pyjwt`)
  - `iat` no older than `jwt_max_age_seconds` (180s default; replay defense)
  - `exp` in the future
  - `qsh` matching the canonical hash of the request method + path + (sorted, sans-`jwt`) query
- `uninstalled` performs a hard delete on the `tenants` row. Per ADR-0014's `ondelete="CASCADE"`, all of that tenant's issues, transitions, slices, metrics, alerts, and rules go with it. No tombstones.
- `disabled` flips `tenant.enabled` to `False` but **keeps the row**. Re-enable just toggles back; the shared secret is preserved.

## Consequences

**Positive**
- A spoofed `installed` for an existing tenant cannot rotate the shared secret. The `test_reinstall_without_jwt_is_rejected` test proves this.
- Replay attacks within an attacker's reach (e.g., a sniffed JWT) are bounded by `iat` freshness (3 minutes) and `qsh` (binds the token to one method/path/query combo).
- Uninstall is atomic — a single DB delete cleans up everything. Re-install of the same tenant works without manual cleanup.
- The JWT verification helper (`app.atlassian.jwt_auth.verify_token`) is reusable for the per-request auth middleware (ADR-0016 — to come in a later stream).

**Negative**
- The first-install window is unauthenticated by design. If an attacker observes which `clientKey` is about to install for the first time and races us to it, they could plant a bogus tenant. Mitigation: this is exactly what every Connect app accepts. The attacker would need network-level interception of the actual Atlassian webhook delivery, which is out of scope. Recovery: the legitimate Atlassian re-install attempt would fail the JWT check; admin manually deletes the bogus row from our DB.
- If Atlassian's `uninstalled` webhook fails to reach us (network issue), the tenant row stays and a re-install attempt will hit the "JWT required for existing tenant" branch. Atlassian on re-install generates a new shared secret and has no JWT to send → install fails. Recovery requires an ops action: manually delete the stale `tenants` row. Documented in the runbook.
- Lifecycle endpoints don't go through `current_tenant_context` — they ARE the path through which a tenant context comes into existence. The per-endpoint authentication inside `lifecycle_service` is the contract.

**Neutral**
- The `qsh` check binds tokens to specific endpoints; testing requires hand-rolling JWTs with the right qsh. The `test_lifecycle.py` helper does this and serves as the reference.

## What we explicitly chose NOT to do

- **No public-key crypto.** Atlassian Connect has both shared-secret (HS256) and asymmetric paths. We use shared-secret because it's simpler and what every Connect app uses. The `publicKey` field in the `installed` payload is ignored by us (and deprecated by Atlassian).
- **No tombstone tenants.** Uninstall hard-deletes. If a tenant later wants their data back, they reinstall and re-sync.
- **No retry on lifecycle failures.** Atlassian retries lifecycle webhooks itself; if we're down, they'll redeliver. We respond 4xx for client errors, 5xx for ours, 204 No Content on success — standard.

## Notes

The lifecycle handler URLs (`/lifecycle/installed`, etc.) are referenced in three places: the descriptor (ADR contract with Atlassian), the router decorators (HTTP routing), and the qsh canonicalization in tests/lifecycle_service. Keep them in sync. A future refactor that moves them must update all three.
