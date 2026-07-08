# 0030 — Operational alerting: SNS, log-pattern + 4xx alarms, Route 53 health check

- **Status:** accepted
- **Date:** 2026-05-22
- **Decision-makers:** the maintainer
- **Tags:** #observability #ops #post-incident

## Context and problem statement

`ObservabilityStack` (`infra/stacks/observability_stack.py`) defines three CloudWatch alarms (billing, App Runner 5xx, App Runner p95 latency) and a metric-filter-fed dashboard. The alarms transition between OK / ALARM / INSUFFICIENT_DATA as expected — and that is all they do. **None of them have `alarm_actions=...` wired**, so a breach is visible only to someone who opens the CloudWatch console.

This is what caused the 2026-05-22 customer-facing incident to be discovered by the customer instead of by us. Atlassian rotated their FIT signing key two days after Marketplace approval; every authenticated request started returning 401; nobody noticed until the human owner happened to load the dashboard and saw the error. The 2026-05-19 prior occurrence was caught only because an Atlassian reviewer flagged it during functional testing. Two incidents, zero automated detections.

A further gap in the alarm catalog: today's failure mode was **401**, not 5xx. The existing `Backend5xxAlarm` would never have fired even if it had been wired to email, because App Runner buckets 401 responses under `4xxStatusResponses`. There is no alarm on 4xx, and no alarm on the specific "FIT validation failed" log pattern that the middleware emits when JWKS lookup fails.

A third gap: every existing alarm is downstream of App Runner emitting metrics. If App Runner itself is *down* — service stopped, container crash-looping, networking broken — no metrics flow, the alarms stay in INSUFFICIENT_DATA / OK, and nothing pages. We have no external check that proves the customer-visible URL is actually reachable.

The right time to close all three gaps is the same session that landed ADR-0029. The in-process JWKS refresh is the structural fix; this ADR is the safety net behind it ("what if the safety net itself breaks?") and the broader catch-all for future failure modes we haven't predicted.

## Considered options

- **A. SNS topic + email + new alarms (JWKS pattern, 4xx) + Route 53 health check.** Wire the existing alarm catalog to SNS, plug the obvious holes, add an external uptime probe. All AWS-native, all CDK.
- **B. Adopt Sentry / Datadog / similar third-party APM.** Application-level exception aggregation with stack traces, deduplication, release tagging.
- **C. AWS CloudWatch Synthetics canary** instead of Route 53 health check for the external probe.
- **D. Status quo + a runbook reminder to "check the dashboard daily."** Manual discipline.

## Decision

**Option A** for all three gaps.

### A.1 — SNS topic + email subscription

One topic per env (`flow-intelligence-{env}-alerts`), one email subscription to `alerts@example.com`. Adding SMS / Slack / additional emails later is a one-line CDK change against the same topic — no other plumbing changes. Free up to 1k emails/mo (well above expected volume).

All existing alarms (billing, 5xx, latency) gain `alarm.add_alarm_action(SnsAction(topic))`. The CDK assertion test `test_observability_all_alarms_have_actions` walks the synthesized template and fails the build if any alarm lacks `AlarmActions` — this is a regression guard, not just a one-time fix.

**Authenticated-unsubscribe enforcement.** Discovered the hard way 2026-05-22 within minutes of this stack landing in prod: a Google Workspace inbox auto-unsubscribed within seconds of confirming the subscription, with zero alarm emails ever reaching the inbox even though SNS-side delivery metrics showed `NumberOfNotificationsDelivered=1`. The cause: AWS SNS embeds a one-click unsubscribe URL (plain HTTP GET, no auth, no confirmation page) in every notification email; Gmail/Workspace, Mimecast, Defender for O365, and Proofpoint all pre-fetch every URL in inbound mail for safety scanning, and that pre-fetch is indistinguishable from a human click. Repeated twice on this account before diagnosis.

The fix is the per-subscription `AuthenticateOnUnsubscribe=true` attribute, set as a parameter to the SNS `Subscribe` API call. When true, the unsubscribe URL in notification emails requires a SigV4-signed request — anonymous GETs (the scanner pre-fetch, an unauthenticated email-client click) return AccessDenied and the subscription stays alive. Only the topic owner or the subscription owner, authenticated via AWS credentials, can unsubscribe.

A topic-policy approach (Deny `sns:Unsubscribe` on the topic resource) was attempted first per a literal reading of the AWS KB article and **rejected by SNS at deploy time** with `Invalid parameter: Policy statement action out of service scope` — `Unsubscribe` is a subscription-scope action, not a topic-scope one, so it cannot appear in an SNS topic policy. The attribute-on-Subscribe approach is the only mechanism that actually works.

`AuthenticateOnUnsubscribe` is **not** settable via `SetSubscriptionAttributes` and **not** exposed as a CloudFormation property on `AWS::SNS::Subscription`. The only way to set it is via the SDK/CLI `Subscribe` call. As a result, the email subscription is **managed out-of-band**, not by CDK. CDK manages the topic and the alarm-action wiring; the recipient list is operational state (see runbook "Operational alerting → First-time setup"). Test `test_observability_creates_alert_topic` asserts `AWS::SNS::Subscription` count == 0 in CDK so a future contributor doesn't reintroduce a CDK-managed subscription that would silently get scanner-unsubscribed.

**Tradeoff this introduces:** legitimate self-service unsubscribe is now impossible. Removing a recipient is a CLI operation (`aws sns unsubscribe --subscription-arn ...`) by someone with topic-owner credentials. Acceptable for ops alerting where we never want a recipient to silently drop out of the rotation; would be wrong for bulk/transactional email.

### A.2 — JWKS-pattern + 4xx alarms

**`ForgeAuthFailures` log metric filter** matches structlog events whose `event` field contains `Forge FIT validation failed` (the exact log line emitted by `ForgeAuthMiddleware` on auth rejection — covers JWKS rotation, malformed tokens, audience mismatches, expired tokens). **Alarm `BackendForgeAuthFailureAlarm`** fires on `>0 in 5min`, single evaluation period. Any single FIT failure pages immediately. This is intentionally tight: with the ADR-0029 in-process refresh, a legitimate JWKS rotation should produce *zero* validation failures. Any occurrence indicates either the refresh broke or a non-rotation auth issue.

**Alarm `Backend4xxAlarm`** on `AWS/AppRunner 4xxStatusResponses > 15` for two consecutive 5-minute periods. This is the softer catch-all for any auth/validation degradation not matched by the JWKS pattern. Threshold tuned to ignore the baseline 4xx noise (probes, scanners) but page on a sustained customer-visible outage.

### A.3 — Route 53 health check (external uptime probe)

Prod only. `HTTPS_STR_MATCH` against `api.example.com/healthz`, search string `"status":"ok"`, 30-second interval, 3-failure threshold. Time to detect: ~90 seconds of consistent failure. Alarm `HealthzAlarm` fires when `HealthCheckStatus < 1` for two consecutive 1-minute periods → SNS. Time to page: ~3 minutes from full backend outage to email.

Dev and staging don't have published DNS names; the customer doesn't hit them, so there's nothing to externally probe.

## Consequences

**Positive**
- **Time to alert on the next JWKS-class incident drops from "until a human notices" (multi-hour) to <5 minutes** for any single failed FIT validation.
- **Time to alert on a full backend outage drops from "until a customer complains" to ~3 minutes** via the Route 53 probe.
- **Silent alarms are forbidden by test**. `test_observability_all_alarms_have_actions` walks every synthesized `AWS::CloudWatch::Alarm` resource and fails CI if any are missing `AlarmActions`. Future alarms cannot regress to silent state without a deliberate test rewrite.
- **Total ops cost: ~$1.10/mo additional** (3 new alarms @ $0.10 = $0.30; Route 53 basic health check $0.50; SNS email free tier). Well within the prod billing alarm threshold.
- **Routing is decoupled from delivery**. Adding SMS, Slack, or PagerDuty later is "subscribe to the topic"; no alarm changes required.

**Negative**
- **One-time bootstrap step required**: AWS SNS emails the subscriber a "Confirm subscription" link on first deploy. Until clicked, alarms transition to ALARM state but no email arrives. Captured in the runbook procedure below. If the deploy happens and we forget to confirm, we're back to silent alarms — a single-point human dependency that this ADR cannot eliminate (AWS requires double opt-in for email).
- **Route 53 health check IPs aren't allowlistable from arbitrary CIDRs.** Currently fine because the backend is public; if we ever IP-restrict it, the check needs the [Route 53 IP ranges](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/route-53-ip-addresses.html) allowlisted.
- **4xx alarm threshold may need tuning** once we have real customer-volume baselines. 15 in 5min is a conservative starting point. If false positives emerge (e.g., a bot scanner), raise it.

**Neutral**
- Sentry / Datadog remain a future option. The runbook will document "if CloudWatch isn't giving enough signal under customer load, evaluate Sentry next." Premature today (low traffic, no real exception backlog), considered explicitly in the rejected options below.

## Pros and cons of the options

### Option A — SNS + alarms + health check _(chosen)_
- **Good:** AWS-native, no third-party setup, no DSN to manage, no separate billing.
- **Good:** Test-enforced — `test_observability_all_alarms_have_actions` prevents the silent-alarms regression that caused this incident.
- **Good:** Detection latency on the worst case (full backend outage) is ~3 min; on the most likely case (JWKS failure) is <5 min.
- **Bad:** No application-exception aggregation. A FastAPI route raising an unhandled `ValueError` shows up only as a 5xx count + a log line; you have to grep CloudWatch logs to find the stack trace.

### Option B — Sentry / Datadog
- **Good:** Stack-trace aggregation, deduplication, release tagging, error budgets.
- **Bad:** Third-party dep (SDK + account + DSN secret + outbound HTTPS to a non-Atlassian endpoint that we'd have to defend in the security self-assessment).
- **Bad:** Sentry free tier caps at 5k events/mo; paid tier starts at $26/mo. Premature spend for current traffic.
- **Bad:** Doesn't help with the *infrastructure-down* failure mode (Route 53 health check is needed regardless).
- **Reconsider:** when alert volume from Option A becomes ambiguous (many alerts, hard to tell which is real) or when application exceptions become the dominant failure class. Today they aren't.

### Option C — CloudWatch Synthetics canary instead of Route 53 health check
- **Good:** Richer probes (full-browser, multi-step, screenshots, JSON parsing).
- **Bad:** ~3x cost ($1.50/mo vs $0.60/mo).
- **Bad:** More CDK code (~50 lines vs ~15) — IAM role, S3 artifacts bucket, runtime/version selection, inline canary script in JS or Python.
- **Bad:** Overkill for "did /healthz return 200 with `status:ok`". Route 53's `HTTPS_STR_MATCH` does exactly that, natively, cheaper.

### Option D — manual dashboard discipline
- **Good:** zero cost, zero code.
- **Bad:** This is what failed. Discovery latency = "when the owner happens to log in." Unacceptable for a production service with real users.

## What we explicitly chose NOT to do

- **No Sentry yet.** Reconsider when CloudWatch's signal stops being enough.
- **No SMS subscription yet.** Email is sufficient for one human at current scale; SMS is one line of CDK when needed.
- **No Slack webhook yet.** Same reason. Wiring is `sns_subs.UrlSubscription(webhook_url, protocol=https)` + a Lambda transformer for nice formatting, or AWS Chatbot — neither warranted before there are humans on call who live in Slack.
- **No PagerDuty integration.** Single owner; the email is the page. Re-evaluate when there's a team and an on-call rotation.
- **No JWKS refresh-failure alarm separate from `ForgeAuthFailures`.** When the in-process refresh from ADR-0029 fails, the resolver logs a `JWKS live refresh from X failed` warning, but the *user-visible symptom* is the FIT validation failure that follows. The downstream alarm catches both paths with a single signal; adding an upstream "refresh failed" alarm would double-page on the same incident.

## Bootstrap procedure (one-time)

1. Deploy the CDK changes (`cdk deploy ObservabilityStack` in each env).
2. AWS SNS emails `alerts@example.com` a confirmation link per env. **Click each link** (one per env where the obs stack was deployed) to activate the subscription. Unclicked = no emails arrive.
3. Verify: in the CloudWatch console, force one alarm into ALARM state (e.g., `aws cloudwatch set-alarm-state --alarm-name flow-intelligence-prod-backend-4xx --state-value ALARM --state-reason "alerting bootstrap test"`). Confirm an email arrives within ~1 minute. Reset with `--state-value OK`.
4. Capture the test in the runbook section "Operational alerting" so a future "did we wire this up correctly?" question has an authoritative answer.

Related ADRs: ADR-0012 (cost ceiling that introduced the billing alarm), ADR-0029 (in-process JWKS refresh — the structural fix this safety net protects).
