# 0037 — Alert delivery destinations: email, Slack, Microsoft Teams via incoming webhooks

- **Status:** Accepted (2026-05-28)
- **Date:** 2026-05-28
- **Decision-makers:** the maintainer; technical approach drafted by Claude Code
- **Tags:** #alerts #notifications #proactive-push #slack #teams #ses

## Locked product outcomes (non-negotiable acceptance criteria)

The technical decision below is constrained to deliver all of them:

1. **Every alert rule can fire to one or more destinations** across email, Slack, Microsoft Teams. Default when no destinations are configured: no push, in-product surface only (non-breaking for existing installs).
2. **Per-alert-rule destination overrides with tenant-wide defaults.** Tenant-wide default destinations set in Settings; individual rules can override or extend.
3. **Anti-spam.** Configurable per-rule cooldown (default 1 hour minimum between fires of the same rule to the same destination). Hard ceiling: no rule fires more than once per 5 minutes to any destination regardless of configuration.
4. **Failure visibility (CLAUDE.md rule #9).** Delivery failures surface in Settings AND a tenant-admin email goes out at most once per 24 hours batching all failures. The customer never has to wonder "did the alert fire?" without an answer.
5. **Test-destination button.** Each configured destination has a "Send test" in Settings that fires a clearly-labeled test message immediately.
6. **No regression on the existing in-product alert surface.** Push delivery is additive.

## Context

### The proactive-notification principle applies here

CLAUDE.md rule #9 (established 2026-05-25): any signal requiring user action must be actively pushed, not passively surfaced. The five customer-configurable alert rule types (`status_duration`, `cycle_time`, `no_activity`, `trend`, `wip_breach` — defined in `forge-prod/frontend/src/components/AlertRulesPanel.tsx`) are exactly such signals, and today they surface only inside the plugin's tabs. That is the passive-surfacing violation this workstream fixes.

### Material finding — alerts are not proactively evaluated today (2026-05-28)

Verified across the repo before drafting: **`alert_service.evaluate_alerts` is only called from the `/api/alerts/evaluate` endpoint (`alerts.py:102`), and nothing invokes that endpoint in production.** Not the ingest path (`forge_sync.py` has no alert call), not the frontend (`requestRemote.ts` has no `evaluate` call — `getAlerts` only *reads* the `alerts` table), not any Forge trigger or scheduledTrigger (`reconcileResolver` does not touch alerts).

**Consequence:** alert rules can be configured but never fire — the `alerts` table is only written by `evaluate_alerts`, which never runs. The in-product surface outcome #6 protects is itself currently dormant.

**Therefore this workstream's first deliverable is wiring proactive evaluation**, not just delivery. This is implied by outcome #1 (a rule must "fire" to reach a destination) and is foundational to everything else here.

### Why this capability is worth closing

Multi-channel proactive push is rare in the analytics-plugin category. Atlassian-native alerts don't push proactively, and time-in-status plugins generally lack configurable WIP-limit alerts entirely. Delivering configurable, multi-channel alert delivery is a concrete capability differentiator.

## Cross-cutting decisions

- **Runtime:** extend FastAPI on App Runner. New routes under `/api/alerts/destinations/*` for CRUD; dispatch happens in the alert-evaluation path.
- **Email:** SES (reuses the ADR-0033 wiring + the configuration-set bounce/complaint routing). Email code ships before SES production access is granted; verify on a real install once approved.
- **Slack:** incoming webhook URL paste, no OAuth in v1. Customer creates the webhook in their workspace, pastes the URL, we POST JSON. Full Slack-App/OAuth deferred to v2.
- **Microsoft Teams:** incoming webhook URL paste via the channel Connectors UI. Same dispatch shape as Slack.
- **Storage:** new tenant-scoped tables (schema below). Tenant scoping per the CLAUDE.md non-negotiable.
- **Customer-copy style:** plain, non-promotional voice. Short plain subject lines; terse actionable bodies; email Reply-To `support@example.com`. Slack/Teams plain-text + light formatting, no over-styled cards in v1.
- **Setup docs:** per-channel "how to set up" pages on the docs site (Slack + Teams especially), linked from the Settings destination dropdown.

## Decision

### A. Proactive evaluation wiring (foundational)

**Evaluation cadence is derived from each rule's threshold — not a fixed global rate.** Maintainer direction 2026-05-28 (challenged and confirmed against the rule-threshold data): the five rule types all threshold at day-to-week scale by default (`status_duration` 24h, `cycle_time` 14d, `no_activity` 7d, `trend` window-based, `wip_breach` `breach_minutes`). Evaluating a day-scale rule hourly is ~168 snapshot computations to detect something that only matters at day granularity and won't be acted on until the next workday — pure waste. A rule the customer sets to a *short* threshold (e.g. "stuck in Code Review > 4h") is the one that genuinely needs frequent checking. So cadence routes by threshold:

- **Two Forge `scheduledTrigger`s** bound to a new **`alertEvalResolver`** (plain async export per the trigger-handler rule; see ADR-0033). Forge fires scheduledTriggers per-install, so the resolver runs per-tenant and `invokeRemote`s the backend with that tenant's FIT — naturally tenant-scoped, mirroring `daily-reconcile`. The resolver passes which tier fired.
  - `daily-alert-eval` (`interval: day`): evaluates rules whose **effective threshold ≥ 24h** (all default-threshold rules land here).
  - `hourly-alert-eval` (`interval: hour`): evaluates **only** rules whose **effective threshold < 24h**. The backend short-circuits immediately (one indexed query: "any enabled rule with sub-24h threshold for this tenant?") for tenants that have none — so a tenant with only default rules does ~zero hourly work. (v2 optimization if even that's measurable at scale: a per-tenant `has_short_threshold_rules` boolean maintained on rule CRUD.)
- **Effective threshold per rule type:** `status_duration` / `cycle_time` / `no_activity` → `threshold_seconds`; `wip_breach` → `breach_minutes`; `trend` → always daily tier (window-based, inherently coarse). Cutoff: `< 24h` → hourly tier, `≥ 24h` → daily tier.
- **New backend route `POST /api/forge/alerts/evaluate-dispatch?tier={daily|hourly}`** (FIT-auth, tenant-scoped): computes the snapshot + insight report (same inputs the existing `/alerts/evaluate` builds), calls `evaluate_alerts` filtered to the tier's rules, then dispatches each newly-triggered alert (§C).
- **Forge `scheduledTrigger` supports `fiveMinute` | `hour` | `day` | `week`** (verified 2026-05-28 against the manifest reference; no cron, 5-min floor). `fiveMinute` is **deferred to v2** — sub-hour thresholds are atypical for flow analytics; hourly is the finest v1 granularity. Document this so a customer setting a 30-min `wip_breach` knows it evaluates hourly in v1.

**Cadence ≠ alert frequency.** The anti-spam cooldown (§D) bounds how often a rule fires *independent of* evaluation cadence — so daily/hourly tiering is a compute-and-correctness decision, not a spam-control one (spam is handled separately). **Detection-latency tradeoff (maintainer-acknowledged):** a daily-tier rule is detected within 0–24h of crossing its threshold, so a "stuck > 1 day" ticket may be stuck ~2 days before the alert is seen. Acceptable for flow analytics (not incident response).

**Three evaluation entry points in v1, each with a distinct role** (maintainer direction 2026-05-28, after challenging the latency tradeoff):

1. **Rule create/update (one-shot, on rule CRUD).** When a customer saves or edits an alert rule (`PUT /api/alerts/rules`), evaluate it immediately against current state. Purpose: instant config-time feedback — pre-existing violations surface the moment the rule is saved, instead of waiting up to a day. **Burst guard:** pre-existing violations populate the *in-product* surface immediately but do NOT burst-push (creating a rule when 20 tickets already qualify must not blast 20 Slack messages). Push delivery flows through the normal cooldown (§D) on the next cycle; the "Send test" button (§F) is the way to verify a destination works without waiting. Note: this catches *existing* violations at config time only — it does not reduce ongoing detection latency for tickets that go stuck *after* the rule exists.

2. **Ticket-event (per-issue, on the issue webhook).** On the existing `jira-issue-changed` → `/api/forge/sync/ingest` path, after ingesting the changed issue, cheaply check *that issue* against the per-issue rules that key on it (`status_duration`, `cycle_time`). Purpose: tighten ongoing detection latency for active tenants (effective latency drops from "next sweep" to "next relevant event," minutes-to-hours during work hours). **It is naturally overnight-aware for free** — no activity → no events → no evals → resumes when the team works again, achieving the "don't evaluate while people sleep" goal without timezone config. **Cost discipline:** evaluate only the *changed* issue against per-issue rules — never a full-tenant snapshot per event, which would reintroduce the over-evaluation cost the daily/hourly tiering exists to avoid. Cost is then proportional to actual activity.

3. **Periodic sweep (daily/hourly, above).** Backstop + the rule types ticket-event can't cover: aggregate rules (`trend`, `wip_breach` — need a snapshot, not a single issue) and absence-of-event rules (`no_activity`, and a silent stuck `status_duration` ticket in a quiet tenant — the condition *is* the silence, so no event fires). This is why the sweep stays as the correctness guarantee, not the workhorse.

Net effect: near-instant detection in active tenants, config-time feedback on rule save, overnight-quiet for free, and the sweep guarantees eventual detection in quiet periods. The 5-min hard ceiling in §D bounds firing across all three entry points.

### B. Schema (additive migration)

```
alert_delivery_destinations
  id                TEXT PK
  tenant_id         TEXT NOT NULL          -- tenant scoping (non-negotiable)
  type              TEXT NOT NULL          -- 'email' | 'slack' | 'teams'
  name              TEXT NOT NULL          -- customer label
  config_json       TEXT NOT NULL          -- email: {"address": ...}; slack/teams: {"webhook_url": ...}
  status            TEXT NOT NULL          -- 'active' | 'disabled'
  created_at        DATETIME NOT NULL
  last_test_at      DATETIME NULL
  last_test_status  TEXT NULL              -- 'ok' | 'failed:<reason>'
  is_tenant_default BOOLEAN NOT NULL        -- part of the tenant-wide default set

alert_rule_destinations               -- per-rule override/extend (outcome #2)
  tenant_id                 TEXT NOT NULL
  alert_rule_id             TEXT NOT NULL
  destination_id            TEXT NOT NULL
  override_cooldown_seconds INTEGER NULL   -- null = use rule/tenant default cooldown
  PRIMARY KEY (tenant_id, alert_rule_id, destination_id)

alert_fires                            -- anti-spam + failure tracking
  id              TEXT PK
  tenant_id       TEXT NOT NULL
  alert_rule_id   TEXT NOT NULL
  destination_id  TEXT NOT NULL
  fired_at        DATETIME NOT NULL
  status          TEXT NOT NULL          -- 'delivered' | 'failed' | 'skipped_cooldown'
  detail          TEXT NULL              -- failure reason for the digest
  INDEX (tenant_id, alert_rule_id, destination_id, fired_at)
```

All additive — no alteration of existing tables. (Migration via Alembic per the coding rules.)

### C. Dispatch architecture

- `evaluate_alerts` already returns the list of newly-triggered `Alert` rows. After evaluation: resolve each rule's effective destination set (rule-level `alert_rule_destinations` ∪ tenant defaults where the rule doesn't opt out), check anti-spam (§D) per `(rule, destination)`, dispatch, record an `alert_fires` row.
- **Dispatchers:** `email_dispatcher.py` (reuses the transactional-email service), and a shared `webhook_dispatcher.py` base for Slack + Teams (both POST JSON to a webhook URL; thin per-channel payload formatting — Slack `{"text": ...}`, Teams a minimal MessageCard/`{"text": ...}`). Library: `httpx` (already a backend dep) with a short timeout + single retry on transient 5xx; webhook 4xx (404/410) is a hard failure recorded for the digest.
- **Custom user-defined arbitrary-URL webhook** is a v2 fast-follow candidate: once the `webhook_dispatcher` base exists, a generic destination type is mechanically cheap. Flagged, not built in v1.

### D. Anti-spam

- Per `(alert_rule_id, destination_id)`: before dispatch, read the most recent `alert_fires.fired_at`. Skip (record `skipped_cooldown`) if within the effective cooldown — `override_cooldown_seconds` if set, else rule/tenant default (3600s). Hard ceiling: skip if within 300s regardless of configured cooldown. With hourly evaluation the ceiling is never naturally reached; it guards the v2 event-driven path.

### E. Failure visibility (outcome #4)

- Each dispatch records `delivered` / `failed` in `alert_fires`. The Settings destination list surfaces `last_test_status` and a "recent delivery failures" view.
- A **daily failure-digest email** (SES, to `tenant.admin_contact_email`) batches the last 24h of `failed` rows, sent at most once per 24h, only when there is at least one failure. Driven off the same hourly eval pass (a once-per-day guard on the digest send).

### F. Test destination (outcome #5)

- `POST /api/alerts/destinations/{id}/test` dispatches a clearly-labeled test message immediately (bypasses cooldown), updates `last_test_at` / `last_test_status`. Wired to a "Send test" button per destination row in Settings.

### G. Frontend (Forge Custom UI)

- New "Alert delivery" section in `SettingsTab.tsx`: destination CRUD (type dropdown email/Slack/Teams + config field + name), tenant-default toggle, per-rule destination override UI on the alert-rule editor, "Send test" buttons, and link-outs to the setup help pages next to the type dropdown ("How to set up Slack delivery →").

### H. Docs-site setup docs

- New help pages (`docs/slack-setup/`, `docs/teams-setup/` or per the existing docs structure): numbered steps + screenshots for creating an incoming webhook in Slack / Teams. Screenshots require creating real webhooks to capture (maintainer-assisted or done during E2E verification).

## Version-bump / deploy analysis (per maintainer gating question)

**No MAJOR bump. The entire Forge surface is MINOR.** Verified against the manifest + the 2026-05-27 empirical version-bump findings (`runbook.md` → "Forge versioning"):

| Change | Forge impact | Bump |
|---|---|---|
| Backend tables / routes / dispatchers | none | n/a |
| `SettingsTab.tsx` destination UI | Custom UI bundle | MINOR |
| Two new `scheduledTrigger`s (`daily-alert-eval`, `hourly-alert-eval`) + `alertEvalResolver` function | scheduledTrigger + function entries | MINOR (observed 2026-05-27) |
| New `/api/alerts/destinations/*` + `/api/forge/alerts/evaluate-dispatch` routes | ride existing `remotes[].backend` | none |

**No new OAuth scope.** The backend's outbound POST to customer webhooks is App Runner egress (not Forge-gated); the new routes use the already-granted backend remote. Consistent with the bundled-deploy posture — this ships as a minor version on the next Forge deploy.

## Scope

**In (v1):** email (SES), Slack (webhook), Teams (Workflows webhook), three evaluation entry points (rule-CRUD one-shot, per-issue ticket-event, daily/hourly sweep — §A), per-rule + tenant-default destinations, anti-spam cooldown + 5-min ceiling, failure visibility (Settings + 24h digest), test button, additive schema migration, Settings UI, setup help pages, **multi-alert grouping per (rule, destination) per sweep** (moved from v2 — see Update 2026-06-01 below).

**Out (v1):** Slack OAuth/bot app; Teams full app/bot; SMS; PagerDuty/Opsgenie; arbitrary user-defined webhook URLs (v2 fast-follow, cheap once the base exists); Discord; mobile push; `fiveMinute` periodic sweep for sub-hour thresholds (v2 — atypical for flow analytics); full-snapshot re-evaluation on every ticket event (explicitly avoided — would reintroduce over-evaluation cost; per-issue targeted eval only).

### Update 2026-06-01 — grouping pulled into v1 + cooldown flush fix

Phase 6 E2E on dev surfaced two issues that together made the v1 UX unusable:

1. **Un-grouped dispatch.** A single sweep crossing threshold on 24 tickets at once produced 24 separate Slack messages. The locked outcome #3 (5-min hard ceiling) was supposed to suppress same-rule re-fires, but with no grouping in v1 the customer-facing burst was still 24 distinct messages. Multi-alert batching was originally a v2 fast-follow (*"add if cheap during dispatch work, else defer"*); the dev observation upgraded it to load-bearing for v1. Implementation: `dispatch_alerts` now buckets alerts by `(rule_id, destination_id)` and renders one grouped message per bucket via `render_alert_group`. Per-alert `alert_fires` rows are still recorded (audit + idempotency); only the outbound dispatch is collapsed.
2. **Cooldown flush bug.** The 5-min hard ceiling silently failed to engage within a single sweep — `_record_fire` added pending rows without flushing, and the next iteration's `_in_cooldown` query (autoflush=False) didn't see them, so every alert sailed through. Fixed by flushing after each bucket's fires. The bug is mostly mooted by grouping (one bucket = one fire-set per rule/dest per sweep), but the flush is kept as a correctness defense.

Customer-copy implication: the approved copy in `customer-copy-adr-0037.md` defines per-alert templates; the grouped render reuses the same vocabulary as a list (e.g. *"24 tickets exceed cycle-time threshold 7 days"* header + a bullet per ticket using the same key/title/duration phrasing). No new copy required.

### Update 2026-06-01 — Teams Workflows replaces the deprecated Connector

Office 365 Incoming Webhook Connectors (the path the original setup-help-pages doc describes) were retired by Microsoft 2026-05-22. The replacement is **Power Automate Workflows webhooks** ("Post to a channel when a webhook request is received" template). Per Microsoft Learn (2026-05-29 revision), the same `{"text": "..."}` payload is accepted — payload shape did NOT change. The operational change that matters: Workflows is meaningfully **slower** than the old connector — first invocation can take 10–20s on cold start. The webhook dispatcher's `httpx` timeout was bumped from 10s to 30s to absorb this (`webhook_dispatcher.py:_TIMEOUT_SECONDS`). Slack remains <1s. The Teams setup help page (`docs/teams-setup`) will need a content refresh to describe the Workflows path instead of the deprecated Connector path — folded into a later screenshot-capture pass.

## Sequencing

1. This ADR → reviewer + maintainer review.
2. Schema migration + proactive evaluation wiring (scheduledTrigger + resolver + evaluate-dispatch endpoint).
3. Email dispatcher + anti-spam + failure tracking + digest.
4. Slack + Teams dispatchers (shared `webhook_dispatcher` base).
5. Settings UI.
6. setup help pages (screenshots gated on creating real webhooks).
7. E2E on a dev install: configure each channel, breach a WIP limit (set limit=1), confirm delivery to all three, verify cooldown + failure paths.

## Consequences

**Positive:** closes the rule #9 violation; activates the dormant alerts feature (the evaluation wiring is independently valuable); a genuine capability differentiator; minor-only Forge impact keeps it inside the bundled-deploy posture.

**Negative / watch:** hourly evaluation latency (acceptable for flow alerts, but document it so customers don't expect real-time); customer-side webhook setup friction (mitigated by the help pages + test button); SES email channel is gated on production access (Slack/Teams are not, so the feature is partially usable even pre-SES-approval); per-tenant hourly backend evaluation adds load (negligible at current scale; revisit if tenant count grows — same posture as ADR-0036's per-tenant cost note).

## References

- CLAUDE.md rule #9 (proactive notification).
- `backend/app/services/alert_service.py` (`evaluate_alerts` — the path being activated + extended).
- `forge-prod/frontend/src/components/AlertRulesPanel.tsx` (the five rule types).
- ADR-0033 (SES proactive notification + the dual proactive/passive principle), ADR-0030 (SNS alerting — separate, operational).
- `docs/engineering/runbook.md` → "Forge versioning" (the minor-vs-major empirical findings this analysis relies on).
