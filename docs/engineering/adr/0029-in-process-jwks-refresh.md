# 0029 — In-process JWKS refresh on `kid not in cache`

- **Status:** accepted
- **Date:** 2026-05-22
- **Decision-makers:** the maintainer
- **Tags:** #forge #auth #reliability #post-incident

## Context and problem statement

Per ADR-0019 the backend validates Forge Invocation Tokens against Atlassian's JWKS. App Runner runs with a VPC connector and no NAT in dev, so the container bakes Atlassian's JWKS at build time (`backend/Dockerfile` Stage runtime `curl` of `https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json`) and `_StaticFileJwkResolver` reads it at startup. When Atlassian rotates a signing key, FITs validate against a `kid` we do not have and the middleware returns 401 with `JWKS lookup failed: kid <id> not in cached JWKS at /app/forge_jwks.json; redeploy to refresh`.

This is not theoretical. It has now happened twice in the customer-visible surface:

- **2026-05-19** — Atlassian rotated the FIT signing key during the v6.0.0 Marketplace listing review. An Atlassian reviewer hit the 401 during functional testing. Recovery was a manual empty-commit-and-push (full deploy ~13 min). Recorded in `docs/engineering/runbook.md` under "Forge JWKS rotated — backend now rejects all FITs" with the note: *"the underlying JWKS-staleness vulnerability is still latent — the proactive-refresh TODO captured in `docs/security-review/security-self-assessment.md` is the durable fix."*
- **2026-05-22** — Two days after Marketplace approval (2026-05-20), Atlassian rotated again. A paying-customer dashboard hit the 401: `Could not load dashboard: Backend /api/alerts?limit=50&project_key=SCRUM 401: {"detail":"JWKS lookup failed: kid forge/invocation-token/wf-4a80f629-3c41-4eac-b0bc-0fec5163b305 not in cached JWKS at /app/forge_jwks.json; redeploy to refresh"}`. Same recovery procedure. The post-approval-bundled-deploy TODO is now overdue and load-bearing for customer trust.

The latent failure mode is structural: the **only** signal that JWKS is stale is a 401 fired when a user (or reviewer) hits a protected endpoint. There is no scheduled rebuild and no in-process refresh. Today's recovery is human-in-the-loop: someone has to notice the 401, identify it as a JWKS rotation, and push the empty commit. Mean time to recovery ≥ 15 minutes assuming someone is online. During an Atlassian listing review or for a customer in a different timezone, that window is multi-hour.

Prod runs with `nat_gateway=True` (`infra/stacks/_config.py:83`), so the backend has public-internet egress at runtime via the NAT Gateway. This was not true of the original dev shape but it has been true of prod since Phase 1; the bake-at-build-time pattern was carried over from dev without re-examining whether prod could refresh at runtime.

## Considered options

- **A. In-process refresh on `kid not in cache` + retry.** When the static resolver misses a kid, fetch the live JWKS from Atlassian once (rate-limited), merge any new keys into the cache, retry the lookup.
- **B. Scheduled empty-commit cron in GitHub Actions.** Weekly/daily cron pushes an empty commit, triggering the existing deploy pipeline, which re-bakes the JWKS.
- **C. Both A and B as defense in depth.**
- **D. Status quo + faster human recovery.** Keep the manual redeploy path, add a CloudWatch alarm on the 401-with-JWKS-message log pattern so we're paged on rotation.

## Decision

**Option A — in-process refresh on `kid not in cache` + retry.**

Self-healing within a single request. No customer-visible outage during rotation, no deploy required, no recurring CI cost, no on-call page.

Implementation lives in `_StaticFileJwkResolver.get_signing_key_from_jwt`:

1. Cache hit → return the key (unchanged steady-state path).
2. Cache miss → take a per-resolver lock, re-check (another thread may have just refreshed), attempt a single live `urllib.request.urlopen(JWKS_URL, timeout=5s)`, merge any new keys into the cache, re-check.
3. Still missing → raise `PyJWKClientError` with a message naming both the file path and the live URL that was tried.

The refresh attempt is **rate-limited to once per 60 seconds per process** via a `_last_refresh_attempt` monotonic-clock timestamp. This bounds load on Atlassian's JWKS endpoint if we ever see a malformed-kid storm (broken or hostile client repeatedly presenting tokens with unknown kids). The 60s cap is a tradeoff: long enough that a sustained bad-kid request rate cannot DDoS Atlassian on our behalf, short enough that the second rotation event of the day still self-heals within a minute.

The fast-path cache check happens outside the lock (CPython single-key dict reads are atomic), so steady-state cost is unchanged. Only cache-miss paths acquire the lock.

## Consequences

**Positive**
- **Mean time to recovery on JWKS rotation drops from ≥15 minutes (deploy duration) to <1 second** (a single live HTTP fetch on the first 401-eligible request).
- **No human in the loop.** Recovery happens automatically on the next request after rotation, regardless of operator availability or listing-review state.
- **No recurring deploys for the purpose of refreshing JWKS.** Avoids the `App Runner roll + ECR churn + smoke-test minutes` cost of a scheduled-deploy approach (option B).
- **Defense-in-depth disabled by default in dev.** The `live_refresh_url` constructor arg accepts `None` to suppress the refresh — useful for tests and any future environment that genuinely lacks public-internet egress.

**Negative**
- **Worst-case request latency on a cache miss with Atlassian unreachable is bounded by `refresh_timeout_s=5s`.** This blocks the FastAPI worker for that period (sync `urllib.request.urlopen` from within an async middleware). Tradeoff vs. the alternative of a sustained 401 outage is clearly favorable; rotation events are rare and a 5s tail-latency spike is preferable to a multi-minute outage.
- **Adds a load-bearing dependency on the backend being able to reach `https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json`.** Prod's NAT Gateway already provides this; if we ever flip prod back to NAT-off, this fix degrades to "no worse than before" — the static cache still works for non-rotation traffic.
- **Adds one threading.Lock per resolver instance and a small per-instance monotonic-clock check.** Negligible.

**Neutral**
- The original "redeploy to refresh" recovery still works as a manual backstop. We do not remove it; we make it a fallback rather than the only path.
- The 401 error message now names both the file path *and* the live URL that was attempted, so an operator debugging a genuine bad-kid (not a rotation, but a malformed or spoofed token) sees the full picture.

## Pros and cons of the options

### Option A — in-process refresh _(chosen)_
- **Good:** seconds-scale MTTR, no operator involvement, no CI cost, no deploy churn.
- **Good:** the failure surface is contained in one method in one file with a clear test boundary.
- **Bad:** introduces blocking I/O on a cache-miss path inside an async middleware. Bounded by a 5s timeout; happens at most once per minute per process during a rotation event.
- **Bad:** depends on prod retaining `nat_gateway=True`. Documented; the resolver degrades gracefully if disabled.

### Option B — scheduled empty-commit cron
- **Good:** zero Python changes; entirely in GitHub Actions.
- **Bad:** does not handle rotations between cron cycles. A daily cron leaves a worst-case window of just under 24 hours where rotation triggers a customer-facing 401.
- **Bad:** every cron tick costs a full deploy (~13 min App Runner roll), even when nothing has rotated. ECR pushes, App Runner rolling deploys, CloudFront invalidations all happen for no functional reason.
- **Bad:** still requires someone to notice and react if Atlassian rotates twice in a cron window — same human-in-the-loop failure mode as today, just less frequent.

### Option C — both A and B
- **Good:** belt-and-braces; if the in-process refresh path is broken (a bug we haven't caught), the scheduled rebuild still keeps the baked JWKS fresh.
- **Bad:** two surfaces to maintain, two failure modes to debug. Option A is structurally self-healing; the scheduled rebuild adds CI cost without adding meaningful safety once A is in place.
- **Bad:** premature belt-and-braces when we have no evidence A is fragile.

### Option D — alarm-and-react
- **Good:** zero code change to the auth path.
- **Bad:** does nothing to reduce MTTR — still requires a human to receive the page and push a commit. The on-call page is the cost, not the cure.

## What we explicitly chose NOT to do

- **No proactive periodic refresh.** A background timer that re-fetches JWKS every N minutes regardless of demand would also work, but it adds a periodic task lifecycle to a FastAPI app that currently has none (we'd need an `asyncio.Task` or a startup hook). The reactive refresh on cache-miss has the same end-state behavior with less machinery.
- **No async HTTP client (httpx).** Would require making `verify_fit` and the resolver Protocol async, propagating through `ForgeAuthMiddleware`. Sync `urllib` is in the stdlib, bounded by a 5s timeout, and rate-limited to once per minute — the tail-latency cost is small relative to the architectural cost of an async refactor.
- **No JWKS persistence across container restarts.** Refreshed keys are held in memory only. On a fresh App Runner deploy or container restart, we start from the baked file and re-discover any rotated kid on demand. Persisting the live-fetched JWKS to disk would add deploy-time complexity (writable volume) for no end-state benefit — discovery is cheap.
- **No removal of the bake-at-build-time path.** That path remains the cold-start source of truth so a fresh container can validate any FIT signed with a key Atlassian had at the time of the last deploy. The live refresh is purely additive.

## Implementation

Single PR. Files touched:
- `backend/app/forge/fit_auth.py` — extend `_StaticFileJwkResolver` with `_maybe_refresh`, lock, rate-limit.
- `backend/tests/test_fit_auth.py` — add tests for refresh-on-miss, rate-limit, refresh-failure-falls-through, refresh-disabled-path.
- `docs/engineering/runbook.md` — update the "Forge JWKS rotated" section to document the new self-healing behavior and the residual manual recovery path.
- `docs/security-review/security-self-assessment.md` — check off the "Build proactive JWKS refresh" item.

Related ADRs: ADR-0019 (Forge migration), ADR-0016 (predecessor JWT middleware).
