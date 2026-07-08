# 0032 — Historical backfill ships as a UI-driven browser loop (supersedes 0025's delivery mechanism)

- **Status:** accepted
- **Date:** 2026-05-25
- **Decision-makers:** the maintainer
- **Tags:** #forge #sync #cold-start #post-pivot #as-built-divergence

## Why this ADR exists

[ADR-0025](0025-historical-backfill-consumer.md) (accepted 2026-05-06) chose a Forge consumer-queue delivery mechanism for historical backfill (its Option C). The shipped code, also dated 2026-05-06, does **not** use that mechanism — it uses a UI-driven resolver loop running in the customer's browser tab. A reader consulting ADR-0025 today is misled. Per the project's ADR convention (`docs/engineering/adr/README.md`):

> *"Don't edit a merged ADR's decision text. If the decision changes, write a new ADR that supersedes it and update the old one's status to `superseded by [NNNN](NNNN-...md)`."*

This ADR records the pivot honestly so future-readers can evaluate the as-built without git-archaeology and so that the planned consumer-queue rebuild ([ADR-0033](0033-backfill-consumer-queue-rebuild.md) — forthcoming) has a clean predecessor to reference. ADR-0025's *delivery mechanism* is superseded; its *backend state machine, ingest pipeline, and 50k cap remain valid* and unchanged in the shipped code.

## Context and problem statement (what happened on 2026-05-06)

Initial implementation began the same day ADR-0025 was accepted (commit `1332d88 — feat: historical backfill consumer + Settings panel`). Over the next ~5 hours, 8 deploys (Marketplace versions v2.21–v2.28) failed to fix `queue.push()` returning HTTP 400 from `@forge/events`.

Pulling Forge logs (commit `a494f8f` body) surfaced two distinct bugs:

1. **Trigger handler shape bug.** Five trigger-style handlers (`lifecycleResolver`, `installLifecycleResolver`, `issueWebhookResolver`, `issueDeletedResolver`, `reconcileResolver`) were wrapped in `@forge/resolver`. Triggers invoke functions directly with `{ payload, context }` and do not supply the `call.functionKey` field that `Resolver.getDefinitions()` destructures. Result: `TypeError: Cannot read properties of undefined (reading 'functionKey')` on every webhook fire. Resolver wrapping is correct for UI invokes (which DO carry `call`) and for queue consumers (manifest's `method:` populates `call.functionKey`), but NOT for triggers.
2. **Downstream queue 400.** When trigger handlers throw on every fire, Forge's platform appears to refuse `queue.push()` requests from the same install. The queue 400 was a *symptom* of bug #1, not an independent failure.

After bug #1 was fixed (commit `a494f8f`), bug #2's 400 persisted on the existing install. Pivot-commit `285a240` body records the most plausible remaining hypothesis:

> *"the customer install never re-accepted the new `consumer` manifest module — Forge's auto-upgrade handles Custom UI changes but not all module types, and `forge install --upgrade` requires the original installer's permissions which I don't have."*

This was not conclusively proven (no Forge platform-team confirmation; no doc explicitly stating consumer modules require re-consent). It was the load-bearing working hypothesis at the time of the pivot. The pivot was triggered by the user (maintainer) instruction quoted in commit `285a240`:

> *"Stop inventing what could be wrong. Base your fixes on evidence."*

After 8 versions of evidence-light fixes, the decision was made to fall back to delivery primitives whose evidence base was strong: the `invoke('name')` pattern used by the dashboard resolver, getContext, syncJira, WIP-limits configuration, and every other resolver-driven feature in the app.

## Considered options (at the time of the pivot)

- **A. Continue the consumer-queue path through the bug cascade.** Rejected. 8 deploys without resolution, third symptom unverified, user instruction explicit ("base on evidence, not invention").
- **B. Pivot to a UI-driven resolver loop using only Forge primitives proven working for this install.** Chosen.
- **C. Revert backfill entirely; tell customers their history is incomplete.** Rejected for the same reason ADR-0025 rejected its Option A — disqualifying for a flow-intelligence tool whose value depends on historical data.

## Decision (as-built)

**Option B, shipped 2026-05-06.** The Forge-side delivery mechanism is a UI-driven resolver loop running in the customer's browser tab. ADR-0025's backend state machine and ingest pipeline are preserved unchanged.

### Forge layer (what changed from ADR-0025)

- New dashboard-resolver function: `runBackfillBatch` (`forge-prod/src/resolvers/dashboard.ts:252`). One invocation processes one ~100-issue page from Jira and POSTs it to `/api/forge/sync/ingest`. Returns `{ processed, nextPageToken?, done, totalSoFar }`. Fits in Forge's 25s resolver budget.
- Settings tab JS loop (`forge-prod/frontend/src/components/SettingsTab.tsx`): `while (!done) await invoke('runBackfillBatch', { nextPageToken })`. Calls `refresh()` after each batch so progress UI updates live.
- `beforeunload` listener attaches while the loop is active. Browser shows its native "Leave site? Changes you made may not be saved." prompt on tab close / refresh. Custom messages are ignored by modern browsers, but the prompt itself triggers — that's the safety net.

### Forge layer (what was removed from ADR-0025's design)

- `forge-prod/src/resolvers/backfill.ts` (queue producer + consumer) deleted (227 lines).
- `@forge/events` package dependency removed.
- `consumer:` block in `forge-prod/manifest.yml` removed.
- `backfillConsumer` function entry in manifest removed.
- Install-event resolver (`installLifecycleResolver` in `forge-prod/src/resolvers/lifecycle.ts`) no longer auto-enqueues a backfill on install. It logs the event and returns.

### Backend layer (unchanged from ADR-0025)

- Tenant state columns (`backfill_status` / `_total_issues` / `_processed_issues` / `_started_at` / `_completed_at` / `_next_page_token` / `_error`) and migration `55fa88564311`.
- `/api/forge/sync/backfill/start`, `/api/forge/sync/backfill/progress`, and `/api/forge/sync/state` endpoints.
- Page size 100; per-run 50,000-issue hard ceiling.
- Skip-if-stale idempotency on `/api/forge/sync/ingest`.

## Consequences

### Positive

- **Uses only Forge primitives proven working for this install.** No reliance on consumer-module re-consent semantics. The `invoke('name')` pattern is exercised by every other feature on the dashboard.
- **Minimal blast radius from the pivot.** The backend state machine, ingest pipeline, and 4 router tests carried over unchanged. The Forge-side delta is bounded to one resolver function + one UI panel.
- **Progress is live and accurate.** Each batch round-trips through the backend, so the Settings tab's polling sees real progress without depending on platform-side queue introspection.
- **Schema is queue-rebuild-ready.** The same tenant state columns work for the browser-loop today and would work for the consumer-queue rebuild ([ADR-0033](0033-backfill-consumer-queue-rebuild.md)) tomorrow without migration churn.

### Negative — UX limitations honestly named

- **Backfill is tab-resident.** The customer must keep the browser tab open during the entire run. Closing or refreshing the tab pauses progress; no background completion.
- **Manual resume after tab close.** The customer has to reopen the page and click again to continue from the last completed batch. The backend state tells them where it stopped, but they trigger the resume manually.
- **No auto-start on install.** The install-event handler used to enqueue automatically. Now it logs only. Admins must perform a deliberate one-time click in Settings as part of setup — by maintainer direction this is intentional (backfill should not fire when an admin clicks Settings out of curiosity), but it does add a setup step.
- **First-impression UX risk on large sites.** On a 50k-issue site, the chained loop can run for hours, and "keep this tab open" is friction at the worst possible moment — a new install's very first run. **This is the load-bearing reason ADR-0033 will revisit the queue path.**
- **Browser's native `beforeunload` prompt is the only safety net.** Modern browsers ignore custom messages — the user sees stock "Leave site?" copy, not a tailored warning that explains the pause/resume model.

### Neutral

- ADR-0025's backend design + 50k cap + state machine + ingest path are preserved. The pivot is strictly Forge-side (delivery mechanism), not full-stack.
- **Install state as of 2026-05-25 (per maintainer).** Two existing installs only: the maintainer's dev tenant (`your-site`, under direct control, re-installable at will) and example-tenant on the *older* listing version (the only third-party install, on track to uninstall in favor of the now-live public listing). No other third-party customers are on the browser-loop path. Example-tenant has already completed its backfill; the browser-loop is operationally relevant for them only until the planned uninstall + reinstall on the public listing version.

## Future path (deliberately not pre-deciding)

A consumer-queue rebuild is the subject of [ADR-0033](0033-backfill-consumer-queue-rebuild.md) — forthcoming, prioritized to land before more installs hit the large-site first-run path. ADR-0033's design space includes:

- Whether the original "install never re-accepted the new consumer module" constraint still binds. **Effectively moot given the install state captured 2026-05-25** (see Consequences → Neutral above): no stranded third-party installs. A fresh install with the consumer block in the manifest from day one consents to it as part of normal install-time scope grant. ADR-0033 should still verify against current Forge docs in one paragraph for the record, but this is no longer a load-bearing design risk.
- Whether Forge's consumer / queue primitives have matured since 2026-05-06 (the original attempt's bugs may be platform issues that have since been fixed; worth a re-check of the Forge changelog and Atlassian community before re-committing).
- **Cut-over strategy collapses to a few lines.** With no stranded third-party installs (maintainer dev = re-installable at will; example-tenant = on track to uninstall + reinstall the public listing), the queue path can ship cleanly: new installs land on it; existing installs adopt it on next upgrade or reinstall. No coexistence-of-paths design weight, no client-side gating by install date, no migration code path.
- Server-side progress reporting that doesn't require the Settings tab to be open (the existing `/api/forge/sync/state` endpoint already serves this; the rebuild just needs to populate it from the consumer rather than the browser-loop).

This ADR explicitly does **not** pre-decide any of the above. Naming the design space here is for context only.

## Notes

- **Pivot commit:** `285a240` — *"fix(backfill): pivot from Forge queue to UI-driven resolver loop"*. Body contains the load-bearing root-cause hypothesis and the user-instruction quote that drove the pivot.
- **Trigger-handler bug fix that preceded the pivot:** `a494f8f` — *"fix(backfill): trigger handlers must be plain async functions, not Resolver-wrapped"*. Worth keeping in mind for any future trigger-style work — this bug had been latent in `lifecycle.ts` since early on but never observed (example-tenant never uninstalled, so the uninstall trigger never fired).
- **Honest-copy follow-up:** `d812c19` — *"fix(backfill): honest copy + beforeunload guard for backfill runs"*. The first pivot commit kept earlier "runs in the background" copy; maintainer caught it and demanded the copy match reality. Also added the `beforeunload` listener.
- **Marketplace version cadence during the bug cascade:** v2.21 through v2.28 in ~5 hours. Worth remembering as a stress case for the auto-publish-on-deploy model (which was working as designed; the deploys themselves were the issue, not the publication mechanism).

Related ADRs: [ADR-0025](0025-historical-backfill-consumer.md) (superseded by this ADR — delivery mechanism only; backend design preserved), [ADR-0019](0019-pivot-to-forge.md) (Forge migration that established the resolver-invoke pattern), [ADR-0033](0033-backfill-consumer-queue-rebuild.md) (consumer-queue rebuild — forthcoming, decision-pending).
