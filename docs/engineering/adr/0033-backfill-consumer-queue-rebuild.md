# 0033 — Historical backfill consumer-queue rebuild (replaces the browser-loop from 0032)

- **Status:** accepted (2026-05-25, with maintainer edits applied)
- **Date:** 2026-05-25
- **Decision-makers:** the maintainer (technical decision); product outcomes pre-locked by maintainer direction 2026-05-25
- **Tags:** #forge #sync #cold-start #queue-rebuild #proactive-notification

## Locked product outcomes (non-negotiable acceptance criteria)

Per maintainer direction 2026-05-25 (post-ADR-0032 ship). These are not up for debate in this ADR. The technical decision below is constrained to deliver all of them:

1. **Auto-start on install.** Install event triggers backfill. No user click required.
2. **Background process via the consumer-queue path.** No browser-tab dependency on the customer side.
3. **Customer-visible progress at any time, without keeping a tab open.** Close the tab, walk away, come back hours later, open Settings, see `67% complete` or `done`. Server-side state polled fresh on each visit.
4. **Completion notification — proactive push + passive surface.** Per the new project-wide proactive-notification principle (`CLAUDE.md` rule #9): SES email to the tenant admin's contact email the moment backfill completes, AND a dismissible banner in the dashboard on next visit. Both surfaces, not either-or — the customer who installed and walked away (the very scenario this rebuild targets) gets the email even if they never come back to the dashboard.
5. **Failure visibility — proactive push + passive surface.** Same principle: on *definitive* failure (auto-retries exhausted; not on transient errors), SES email to the tenant admin's contact email, AND retry button + failure state in the Settings UI. Transient errors stay passive (retried silently); definitive failure is loud.
6. **50k cap visibility — proactive push + passive surface.** When backfill completes at exactly N=50,000 (the hard cap), Settings shows a banner: *"Backfill complete. 50,000 issues indexed (our current cap). If your site has more historical issues, contact us at support@example.com to extend."* AND the same message lands in the tenant admin's email. Same proactive-push reasoning — the customer needs to act (contact us) and can't be assumed to come back.

These outcomes exist because [ADR-0032](0032-backfill-browser-loop-supersedes-0025.md) ships a browser-loop that violates outcomes 1–4. The first-impression UX risk on large sites (ADR-0032) makes this load-bearing, not optional polish.

## Context — what we know going in

- **The original consumer-queue path was attempted on 2026-05-06 and abandoned** after a bug cascade (8 deploys, `queue.push()` 400 across v2.21–v2.28). Two distinct bugs were identified and fixed: (a) trigger handlers wrapped in `@forge/resolver` crashed because triggers don't supply `call.functionKey`; (b) downstream queue 400 was a symptom of (a). After both fixes, the queue 400 persisted on the existing install — most-plausible-but-unverified root cause: example-tenant' install never re-accepted the newly-added `consumer` manifest module (Forge auto-upgrade handles Custom UI but not all module types; `forge install --upgrade` requires the original installer's permissions which the developer didn't have). See ADR-0032 → Context for the full git-log reconstruction.
- **Forge primitive verification (2026-05-25):** `@forge/events.Queue` v2.x is still Atlassian's recommended async-events primitive. No platform changes in the Forge changelog between 2026-05-06 and 2026-05-25 related to queue, consumer, trigger, or lifecycle behavior. Consumer function `timeoutSeconds` is now configurable up to **900s (15 min)** — up from the 10-min default that ADR-0025 referenced. The "new consumer module requires admin re-consent" question remains undocumented by Atlassian; treating it as a latent constraint for any future major that adds a NEW consumer module, but moot for the current install set (maintainer dev = re-installable; example-tenant older version = pending uninstall).
- **Install state allows clean cut-over.** Per maintainer confirmation 2026-05-25: the only existing installs are (a) the maintainer's dev tenant under direct control and (b) example-tenant on the older listing version, on track to uninstall in favor of the now-live public listing. No stranded third-party installs to migrate. The queue path ships clean; new installs land on it; existing installs adopt it on next upgrade or reinstall.
- **Backend state machine + ingest pipeline + 50k cap from ADR-0025 are still in production today** (preserved by the ADR-0032 pivot unchanged). The rebuild reuses all of them. Schema migration is unnecessary.
- **Existing operational alerting** (ADR-0030) — CloudWatch alarms wired to SNS + email. The failure-visibility outcome can lean on this for backend-side alerting on backfill failures (e.g. ingest 5xx rate spike, queue-handler errors in CloudWatch logs).

## Considered options

- **A. Restore the original ADR-0025 consumer-queue design with the trigger-handler-shape fix baked in.** The known landmine from 2026-05-06 (`a494f8f`) is already understood and fixable in the same change. Use the current 15-min `timeoutSeconds`. Auto-enqueue on install via `installLifecycleResolver`. Settings UI polls `/api/forge/sync/state` instead of running a JS loop. Completion notification = small dashboard banner on next visit; failure visibility = retry button in Settings.
- **B. Polish the browser-loop without changing the architecture.** Add a beforeunload-prevention library, more aggressive auto-resume, optimistic UI. Doesn't satisfy outcome #2 (background completion) or #3 (close tab, walk away). **Rejected** — violates locked outcomes.
- **C. Defer the rebuild until later.** Doesn't satisfy outcome #1 (auto-start) for any new install today — auto-start on install is a required outcome now, not future polish. **Rejected.**

## Decision

**Option A.** Rebuild the consumer-queue path with the platform constraints we now understand, the trigger-handler-shape fix baked in from the start, and all five locked outcomes as acceptance criteria.

### Implementation plan

**Forge layer.**

- Re-add `@forge/events` package dependency.
- Recreate `forge-prod/src/resolvers/backfill.ts` with two exports:
  - `enqueueBackfill()` — pushes the initial backfill task. Called from `installLifecycleResolver` (auto-start on install — outcome #1) and from a manual `define("startBackfill", ...)` on the dashboard resolver (manual re-trigger from Settings).
  - `backfillConsumer` — the queue consumer function. **Plain async export, NOT wrapped in `@forge/resolver`** — per the 2026-05-06 lesson (commit `a494f8f`). Each invocation: paginate Jira ~100 issues, POST to `/api/forge/sync/ingest` with `skip_if_stale=true`, report progress to `/api/forge/sync/backfill/progress`, re-push continuation with `nextPageToken` until done or 50k cap hit. `timeoutSeconds: 900` (the current maximum, up from ADR-0025's 600) — fewer continuations needed for large backfills.
- Update `forge-prod/manifest.yml`:
  - Add a `consumer:` block binding the `flow-intelligence-backfill` queue to `backfillConsumer`.
  - Add a `function:` entry for `backfillConsumer` with `timeoutSeconds: 900`.
- `installLifecycleResolver` (`forge-prod/src/resolvers/lifecycle.ts`): replace the log-only behavior introduced in ADR-0032's pivot with `await enqueueBackfill()`. Plain async export (this is a trigger handler, not a Resolver — the 2026-05-06 bug is the precedent).
- **Remove the browser-loop path completely.** `runBackfillBatch` dashboard-resolver function deleted. JS loop in `SettingsTab.tsx` deleted. `beforeunload` listener deleted. The Settings UI becomes purely a polling-and-display surface for backend-tracked state.

**Backend layer (changes from existing ADR-0025 state).**

- Existing endpoints unchanged: `/api/forge/sync/backfill/start`, `/api/forge/sync/backfill/progress`, `/api/forge/sync/state`. The consumer hits the same idempotent ingest path with `skip_if_stale=true` that the browser-loop used.
- New columns on `tenants`:
  - `backfill_acknowledged_at` (nullable timestamp) — set when the customer dismisses the completion banner. Used by the Settings UI to suppress the banner after first dismiss.
  - `admin_contact_email` (nullable text) — the address SES sends completion / failure / cap-reached emails to. Population path verified at implementation time: prefer the install lifecycle event payload if Forge exposes the admin's email there (likely via `principal.accountId` → `/rest/api/3/user?accountId=` lookup as the app's authenticated context), with fallback to a one-time Settings UI prompt on first visit if Forge doesn't surface it. **Implementation MUST verify both paths before shipping** — the proactive notification only works if we know where to send it.
- New endpoint: `POST /api/forge/sync/backfill/acknowledge` — sets `backfill_acknowledged_at = utcnow()`. One-line endpoint.
- New endpoint: `PUT /api/forge/sync/admin-email` — updates `admin_contact_email`. Powers the Settings UI fallback path.
- New SES outbound paths:
  - `send_backfill_completion_email(tenant)` — fired by the consumer on the final batch.
  - `send_backfill_failure_email(tenant, error_summary)` — fired only on definitive failure after auto-retries exhausted (transient errors stay silent).
  - `send_backfill_cap_reached_email(tenant)` — fired when the consumer hits `processed == 50000` and stops.
  All three skip silently if `admin_contact_email` is null AND log a warning so the operator can prompt the customer in Settings.
- Migration: `ALTER TABLE tenants ADD COLUMN backfill_acknowledged_at TIMESTAMP NULL` + `ALTER TABLE tenants ADD COLUMN admin_contact_email TEXT NULL`. Both additive, backward-compatible per the standing rule.

**Frontend layer (Custom UI changes).**

- Settings → Historical backfill panel: replace the JS-loop trigger with a polling-driven status display. Poll `/api/forge/sync/state` every 5s while `status=running`, every 30s while `status=pending`, never while `status=completed` or `status=failed` (until user clicks retry). Render progress (`X / Y issues`, percent, estimated time remaining based on rolling average rate). Retry button (outcome #5) hits `/api/forge/sync/backfill/start` again on failure.
- Settings → Admin contact email row: surfaces `admin_contact_email` with an inline edit affordance. If null (we didn't capture it from the install context), shows a prominent "Add an email to be notified when your backfill completes" prompt — this is the fallback path naming for the proactive-notification field.
- Settings → 50k-cap-reached banner (outcome #6): if `state.backfill.status === "completed"` AND `state.backfill.processed_issues >= 50000`, render the cap-reached message inline beneath the progress card with the support@example.com mailto.
- Dashboard → completion banner (outcome #4): on dashboard load, if `state.backfill.status === "completed"` AND `state.backfill.acknowledged_at === null`, render a one-line dismissible banner above the Overview content: *"Historical backfill complete — N issues now available in your dashboard. [Dismiss]"*. Dismiss POSTs to `/api/forge/sync/backfill/acknowledge` and removes the banner. Banner appears once, never again after dismissal. **The banner is the *passive* surface; SES email is the *proactive* surface — both fire on the same event per CLAUDE.md rule #9.**

**Cut-over (clean per install-state).**

- New installs: consumer module is in the manifest at install time → admin consents to it as part of normal scope grant → no re-consent needed → auto-enqueue on install fires → backfill runs in the background → customer can close the tab. Outcome #1 ✓.
- Maintainer's dev install: re-install at will after the rebuild ships to verify end-to-end.
- Example-tenant (older version, browser-loop-completed already): leave alone. When they uninstall + reinstall the new public-listing version, they become a new install for delivery-mechanism purposes. No migration code.
- **No coexistence-of-paths code.** The browser-loop is removed in the same change as the queue rebuild. Old browser-loop code is dead on day one.

### Operational alerting integration

Leverages existing ADR-0030 infrastructure. Backfill failures fire the same SNS-on-elevated-errors path that already exists. Two specific signals worth a dedicated CloudWatch metric filter:

- `backfillConsumer` invocation errors (parsed from Forge resolver-log forwarding if/when that's wired; otherwise from backend log entries when the consumer's HTTP calls to `/ingest` fail repeatedly for the same tenant).
- Stalled-state (status=running, no progress update for >15 min) — backend-side check during the existing daily reconcile job.

Both pages maintainer via the existing alert email. No new alerting infrastructure; just two metric filters added in a follow-up CDK change.

## Consequences

### Positive

- **All six locked outcomes met.** Auto-start, background completion, server-side progress, completion notification (proactive + passive), failure visibility (proactive + passive), 50k cap visibility (proactive + passive) — each maps to a specific code change above.
- **Eliminates the first-impression UX risk** that was the load-bearing driver for this ADR. A new install on a large site installs, walks away, comes back to a completed backfill — AND gets an email letting them know they don't even need to come back to check.
- **Establishes the proactive-notification pattern** as the project default (now CLAUDE.md rule #9). Every "this matters and the user needs to act" surface from this ADR onward gets both a push and a passive render, not just the passive render. Future ADRs reference the rule rather than re-litigating per-feature.
- **Honest about ADR-0025's original design returning.** This isn't a new architecture; it's the original architecture, rebuilt with the platform constraints understood and the trigger-handler-shape bug fixed from the start.
- **Schema delta is minimal.** Two additive columns (`backfill_acknowledged_at`, `admin_contact_email`); no breaking change; preserves all backfill state from the browser-loop era.
- **Browser-loop code is deleted, not deprecated.** No dead code paths to maintain; no risk of someone re-enabling the wrong path in a future refactor.
- **Cut-over is structurally clean.** Per the install state captured in ADR-0032: no stranded customers, no migration code.

### Negative

- **Forge consumer reliability becomes load-bearing for first-install UX.** If Atlassian's queue infrastructure has an incident during a customer's first install, the backfill stalls. Mitigated by: (a) webhooks (ADR-0024) + reconcile keep current data flowing in the meantime so the dashboard isn't blank; (b) the retry button (outcome #5) lets the customer recover once the platform incident clears; (c) alarm-on-stalled-state (above) surfaces to the maintainer.
- **The "consumer module re-consent" latent risk persists for any future major that adds a NEW consumer module.** This rebuild ADDS a consumer module to the v6.x major. Customers consenting to v6.x as part of their major upgrade consent to the consumer — same as any other module. The risk would re-emerge if a future v7.x adds a SECOND consumer module; should be flagged in any such future ADR. Worth a one-line note in `docs/engineering/runbook.md` Marketplace publishing section.
- **50k issue cap is unchanged.** Very large enterprise tenants still need a manual cap-extension request. Acceptable for typical mid-market sites per ADR-0025; revisit if very large tenants become common.
- **Polling cost.** The Settings UI poll at 5s during running adds ~720 backend hits per tenant per hour of a running backfill. Cheap (App Runner handles it trivially), but worth knowing. Mitigation: poll cadence backs off to 30s when `status=pending` and stops entirely when `status` is terminal.

### Neutral

- ADR-0025's backend state machine + ingest pipeline + 50k cap are preserved unchanged (already preserved through ADR-0032; this ADR doesn't alter them either).
- Forge Marketplace versioning impact: adds a `consumer:` manifest module + corresponding `function:` entry. The runbook's major-vs-minor table (`docs/engineering/runbook.md` → "Forge versioning") does **not** explicitly enumerate consumer modules. Most likely a MAJOR bump — consumer modules historically required admin re-consent in 2026-05-06 (the install never re-consenting was the load-bearing failure that drove the ADR-0032 pivot). Verify against `forge deploy --dry-run` on the dev install before the manifest change merges; if MAJOR, admins re-consent on upgrade as normal (per the v5 → v6 licensing precedent). Update the runbook's versioning table with the observed answer either way — it's a documentation gap regardless of which side it falls on.
- The "deploy-and-pray" stress case from 2026-05-06 (8 versions in 5 hours during the original bug cascade) was a process failure, not a platform failure. The trigger-handler-shape bug fix landing on day one of the rebuild prevents the dominant root cause from recurring. The undocumented consumer-re-consent risk is now understood and structurally avoided by the cut-over framing.

## Pros and cons of the options

### Option A — restore the consumer-queue design with platform constraints understood _(chosen)_

- Good: only path that satisfies all five locked outcomes.
- Good: backend / ingest / state machine carry over from ADR-0025 unchanged — minimal blast radius.
- Good: the 2026-05-06 bugs are now understood and fixable in the same change; the cut-over framing avoids the re-consent landmine for the current install set.
- Bad: introduces a Forge platform dependency (consumer/queue infrastructure) for first-install UX. Failure modes documented above.
- Bad: bigger code surface than option B (manifest + new resolver file + UI rewrite of the backfill panel).

### Option B — polish the browser-loop

- Good: zero Forge-platform new surface; uses only the dashboard-invoke pattern that's already proven.
- Bad: cannot satisfy locked outcomes #1, #2, #3, #4. Polish doesn't address the architectural limitation that backfill is tab-resident.
- Rejected.

### Option C — defer until customer scale demands it

- Good: no engineering work needed now; the browser-loop ships and example-tenant' existing install is unaffected.
- Bad: cannot satisfy outcome #1 (auto-start) for any new install today.
- Rejected.

## What this ADR explicitly does NOT decide

- **Real-time progress streaming** via any Forge-provided mechanism instead of backend polling. Polling is the simpler approach and fits the locked outcomes. If Forge exposes a server-sent-events primitive in the future that's cheaper, revisit then.
- **In-app notification via a Forge platform notification API** (if one exists) instead of a dashboard banner. The banner is the cheapest path that closes the loop; richer notification surfaces are future polish.
- **Per-tenant 50k cap override.** Hardcoded for now per ADR-0025. Revisit when a customer asks.
- **Project-scoped backfill** ("just backfill this project"). Same as ADR-0025: not worth the complexity for v1.

## Notes

- **Forge primitive verification source:** [Async Events API](https://developer.atlassian.com/platform/forge/runtime-reference/async-events-api/) (Forge dev portal, last updated June 2025 — no 2026 update found in the [Forge changelog](https://developer.atlassian.com/platform/forge/changelog/) for the period 2026-05-06 to 2026-05-25).
- **Lesson from 2026-05-06 baked in:** trigger handlers (`installLifecycleResolver`, lifecycle uninstall, webhooks, reconcile, this rebuild's install path) are plain async exports — NEVER wrapped in `@forge/resolver`. Commit `a494f8f` is the precedent. The bug surfaces as `Cannot read properties of undefined (reading 'functionKey')` at runtime and was the root cause of the cascade that drove the ADR-0032 pivot.
- **Cost ceiling:** rebuild is engineering time only — no new third-party services. Polling adds negligible App Runner cost (~720 backend hits / tenant / running-hour, far below alarms).
- **Estimated implementation time** (engineering, post-ADR-approval): 1–2 days for the Forge / backend / UI rewrites + tests; +0.5 day for the CDK change adding the two CloudWatch metric filters; +0.5 day for end-to-end verification on the maintainer's dev install. Realistically 3 days of focused work.

Related ADRs: [ADR-0025](0025-historical-backfill-consumer.md) (original consumer-queue design, superseded by 0032 on delivery mechanism; backend state machine + 50k cap from 0025 still in force); [ADR-0032](0032-backfill-browser-loop-supersedes-0025.md) (as-built browser-loop being replaced by this ADR); [ADR-0019](0019-pivot-to-forge.md) (Forge platform context); [ADR-0030](0030-operational-alerting.md) (alerting infrastructure this rebuild leverages for failure visibility).
