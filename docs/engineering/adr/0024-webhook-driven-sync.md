# 0024 — Webhook-driven sync + scheduled reconciliation

- **Status:** accepted
- **Date:** 2026-05-06
- **Decision-makers:** the maintainer
- **Tags:** #forge #sync #freshness

## Context and problem statement

Until now the dashboard's only data-freshness mechanism was a manual "Sync Jira" button. This is fine for demos and Phase 2 customers but doesn't satisfy the expectations of teams using a project-page integration:

- Status changes don't reflect on the dashboard until someone clicks Sync.
- Charts can show stale data for hours (or longer if nobody opens the page).
- The bottleneck/alert pipeline runs on whatever the last sync wrote — so alerts can lag actual flow problems by a full day.

We need ongoing, automatic sync. Two infrastructural shapes were considered.

## Considered options

- **A. Polling.** Backend cron polls Jira on a fixed interval. Wastes API quota; doesn't scale per-tenant; requires backend Jira credentials (we don't have them per ADR-0019).
- **B. Forge product event triggers.** Atlassian pushes us `avi:jira:created:issue` / `:updated:issue` / `:deleted:issue` events when issues change. Resolver fetches the full issue via `requestJira` (using the install's already-granted `read:jira-work`) and forwards to the backend ingest endpoint. Plus a daily `scheduledTrigger` for reconciliation safety net.
- **C. Webhooks via the Atlassian Connect REST API.** Pre-Forge approach. Doesn't apply here.

## Decision

**Option B**, shipped on 2026-05-06.

### Architecture

Three resolvers in `forge-prod/src/resolvers/webhooks.ts`, each registered as a separate Forge function in the manifest:

```
avi:jira:created:issue \
avi:jira:updated:issue ─────► issueWebhookResolver ─► fetch issue → POST /api/forge/sync/ingest
                                                       (skip_if_stale=true)

avi:jira:deleted:issue ─────► issueDeletedResolver ─► DELETE /api/forge/sync/issues/{id}

scheduledTrigger (daily) ───► reconcileResolver ────► GET /api/forge/sync/state
                                                       paginate JQL: updated >= last_sync_at
                                                       POST ingest (skip_if_stale=true)
```

Forge's trigger model dispatches one event source to one function key, so the three resolvers can't share a single `define("event-trigger")` handler — they're three Resolver instances exporting three definitions.

### Idempotency

The webhook path sets `skip_if_stale=true` on the ingest request. Backend's `process_issue_payload` checks: if existing row's `updated_at >= payload.fields.updated`, skip the write. Defends against:

- Duplicate event deliveries (Forge's at-least-once semantics).
- Out-of-order arrivals (rare but possible across regions).
- Reconciliation overlap with a webhook firing for the same issue.

The bulk Sync paths (`syncJira` from the dashboard button) leave `skip_if_stale=false` so Force-full still re-processes apparently-unchanged issues — necessary when a schema change adds new fields we want to backfill.

### Reconciliation

`reconcileResolver` runs daily (Forge `scheduledTrigger interval: day`). It pulls everything updated since `tenant.last_sync_at`, with a hard cap of 500 issues per pass. The cap matters because the resolver's 25-second budget can't paginate unbounded backlogs — high-throughput tenants need the backfill consumer, which has a 10-minute budget.

Without `last_sync_at` (fresh install), the reconcile path is a no-op; the user's first manual Sync (or the backfill consumer when it ships) bootstraps the tenant.

### Delete handling

`issueDeletedResolver` calls `DELETE /api/forge/sync/issues/{id}`. The backend endpoint:

- 204 when the issue exists; cascade-drops transitions, slices, sprint membership.
- 204 when the issue does not exist (we never synced it; the delete event still fired). Webhook deliveries for out-of-window issues are normal — silent drop is correct.

### What didn't make this cut

- **Per-event observability.** No metrics on webhook delivery rate, processing latency, or skip ratio yet. Production hardening adds CloudWatch dashboards.
- **Manual reconcile button.** The "Force full" button serves the same purpose with broader scope; not worth a third button.
- **Real-time UI updates.** The dashboard still requires a page reload to see webhook-applied changes. Server-sent events / WebSockets are out of scope here — admins refresh on demand or the next page load picks it up.

## Consequences

**Positive.**

- Status changes reflect within ~30 seconds of the Jira action.
- The dashboard stops being a "sync on demand" tool and starts being a real-time view.
- Daily reconciliation closes the at-least-once-isn't-exactly-once gap.
- No new manifest scopes — zero risk of admin re-grant prompts breaking existing installs.

**Negative.**

- Three more Forge functions in the manifest. Each one's invocations count against the install's daily quota. Atlassian's free tier covers the volume we'd hit even at 100 active customers; revisit only if a single tenant approaches the limit.
- Reconciliation cap of 500 issues/day is a real constraint. Tenants with > 500 issues changing daily need the backfill consumer (manual `forge install` already triggers the resolver, but the consumer's 10-minute budget is required for genuine high-throughput sites).
- The "Sync Jira" mental model goes away. Existing customers who learned to click Sync may be confused by the relabel. Mitigated by the "Last sync: Nm ago" indicator and tooltip copy explaining webhooks.

## Open questions

- **Reconcile cadence.** Daily is the reconcile cadence we use. Bumping to a more frequent cadence when a tenant needs it is a one-line manifest change.
- **Per-event project scoping.** Webhooks fire for *all* projects on the install, not just the project the user is viewing. The backend's tenant-scoped ingest handles this naturally — issues land in their actual project's row regardless of which project page the user has open. No project-page-driven filtering is needed.
