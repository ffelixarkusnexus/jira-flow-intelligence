# 0025 — Historical backfill via Forge consumer + queue

- **Status:** superseded by [0032](0032-backfill-browser-loop-supersedes-0025.md) on 2026-05-25 — *delivery mechanism only*; backend state machine, ingest pipeline, and 50k cap from this ADR remain in force.
- **Date:** 2026-05-06
- **Decision-makers:** the maintainer
- **Tags:** #forge #sync #cold-start

## Context and problem statement

The bulk syncJira resolver caps at 200 issues / 30 days because Forge resolver functions hard-time at 25 seconds and we can't paginate beyond two pages safely. That's fine for the Phase-2 demo customer with a 30-day relevant window, but a fresh install on a site with thousands of historical issues lands the user on a half-empty dashboard.

Webhooks cover the steady state going forward, but they don't backfill — `avi:jira:updated:issue` only fires when an issue actually changes, so dormant tickets that haven't been touched in 60+ days never get pulled in.

We need a cold-start path that pulls *all* of the customer's history without the 25s budget.

## Considered options

- **A. Skip backfill; tell customers their history is incomplete.** Disqualifying. Reviewers and customers expect a flow-intelligence tool to show the actual past state of work.
- **B. Backend cron pulling Jira directly.** Same blockers as ADR-0019: backend has no Jira credentials per-tenant. Can't.
- **C. Forge consumer function via `@forge/events.Queue`.** Consumer functions get a 10-minute invocation budget. Producer pushes onto a queue; consumer pulls and processes; if it doesn't finish, it re-pushes a continuation.
- **D. Repeated reconciliation passes lifting the JQL date floor each cycle.** Doesn't solve the cap; just spreads the work across more days. Customer waits weeks to see history.

## Decision

**Option C, shipped 2026-05-06.** Three parts:

### Producer

Two paths push onto the `flow-intelligence-backfill` queue:

1. **Auto on install.** A new `avi:forge:installed:app` trigger (`installResolver`) fires when a customer installs the app. Its only job is `await enqueueBackfill()`.
2. **Manual button.** A `startBackfillResolver` is invoked via `@forge/bridge.invoke('startBackfill')` from the Settings tab. Calls `/backfill/start` to flip status to `running`, then enqueues. Required for installs that pre-date this feature (example-tenant' path) — the install event already fired and won't re-fire on app upgrades.

### Consumer

A single `consumer` config in the manifest binds the queue to `backfillConsumer.backfill`. Each invocation:

1. Iterates `ORDER BY created ASC` Jira search with no time floor, pulling one page (~100 issues) at a time.
2. POSTs each page to `/api/forge/sync/ingest` with `skip_if_stale=true` so re-enqueues during a long backfill don't duplicate work.
3. Reports progress via `/api/forge/sync/backfill/progress` per page (delta + next page token).
4. Hits a per-invocation page cap (`MAX_PAGES_PER_INVOCATION = 25` ≈ 2,500 issues) and re-enqueues the continuation. Forge picks the consumer up again with the new payload.
5. Stops when Jira returns `isLast=true`, no `nextPageToken`, or the per-run hard ceiling (`MAX_TOTAL_ISSUES = 50,000`) is hit. Calls progress with `done: true`.

### Backend state

New columns on `tenants`: `backfill_status` (`pending` | `running` | `completed` | `failed`), `backfill_total_issues`, `backfill_processed_issues`, `backfill_started_at`, `backfill_completed_at`, `backfill_next_page_token`, `backfill_error`. Migration `55fa88564311`.

`/api/forge/sync/state` returns the backfill block alongside `lastSyncedAt`. The Settings tab polls every 5s while `status=running` and renders progress live.

### Why not bigger pages

Jira's `/search/jql` caps `maxResults` at 100. We could potentially go higher with JQL field reduction, but page size doesn't matter much — the consumer's bottleneck is the backend ingest call, which scales linearly with issue count. Keeping pages at 100 makes the math intuitive (every 100 issues = one HTTP round trip).

### What didn't make this release

- **Resumable backfills across consumer crashes.** If the Forge consumer process is terminated mid-batch (very rare), the state stays at the last reported `next_page_token` but no continuation gets enqueued. Manual retry from the Settings tab restarts cleanly. Auto-resume is deferred future work.
- **Per-tenant `MAX_TOTAL_ISSUES` override.** Hardcoded at 50k for now. Big customers can request manual extension; surface the override later.
- **Project-scoped backfill.** A user on a project page might want "just backfill this project." Not worth the complexity for v1; backfilling everything is cheap once the consumer's running.

## Consequences

**Positive.**

- New customers see their full history on first dashboard render (or shortly after — depending on volume, 5-30 minutes).
- Existing customers can fix gaps with one click in Settings.
- Reuses the existing ingest pipeline; the consumer is the only new code path that runs at sync time.
- No new manifest scopes — `read:jira-work` covers it.

**Negative.**

- One more place where Forge consumer reliability matters. If Atlassian's queue infrastructure has incidents, the backfill stalls; webhooks/reconcile keep current data flowing in the meantime so nothing visible breaks.
- 50k cap means very large enterprise tenants need manual extension. Not blocking for the SMB / mid-market we're targeting first.
- The dashboard fills *gradually* during a backfill — not atomic. Users may briefly see partial data on a refresh mid-pull. Acceptable.

## Multi-AZ RDS deferral (related)

The hardening plan originally listed Multi-AZ RDS as a hardening line item. Per discussion 2026-05-06, we're **deferring Multi-AZ** — the ~$30/mo cost isn't justified while single-AZ + automated daily backups recover within 5-15 min after a zone failure. Single-line config flip (`cfg.rds_multi_az=True`) when an availability SLA requires it.
