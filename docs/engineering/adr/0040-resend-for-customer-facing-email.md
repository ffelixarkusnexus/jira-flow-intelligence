# 0040 — Resend for customer-facing transactional email (after AWS SES denial)

- **Status:** Accepted (2026-06-03 — approved after the AWS SES denial)
- **Date:** 2026-06-03
- **Decision-makers:** the maintainer; technical drafting by Claude Code
- **Tags:** #email #ses #resend #transactional #vendor-pivot #adr-0033 #adr-0037

## Context

### The triggering event

AWS SES production-access request was DENIED on 2026-05-28. The full denial:

> Thank you for providing us with additional information about your Amazon SES account in the US East (N. Virginia) region. We reviewed this information, but we are still unable to grant your request.
>
> We made this decision because we believe that your use case would impact the deliverability of our service and would affect your reputation as a sender. We also want to ensure that other Amazon SES users can continue to use the service without experiencing service interruptions.

The reasoning is vague ("use case would impact deliverability and your reputation") and AWS did not name a specific concern.

### What this blocks under SES sandbox

In sandbox mode, SES can only send to verified identities. The product's customer-facing email paths all target the customer admin, so all are blocked: backfill notifications (ADR-0033) and alert delivery + the 24h failure digest (ADR-0037).

CLAUDE.md rule #9 (proactive notification) and ADR-0033's requirement for a backfill-complete signal make "drop customer email entirely" not a viable answer — the proactive-push half of the customer experience hard-depends on email reaching the customer admin.

### Why not appeal SES again

A second appeal is poor opportunity-cost. The denial reasoning is vague enough that another appeal has no obvious lever to pull; AWS would likely respond identically. The third-party transactional providers (Resend, Postmark) use a different underwriting model — they assume opted-in transactional sends and ban offenders after the fact, rather than gating production access up-front. That's a faster path to working customer email.

## Decision

**Customer-facing transactional email goes through Resend (free tier).** SES is no longer used — its only remaining verified-identity path was retired with the feature that used it.

### Routing table

| # | Path | Source ADR | Recipient | Service |
|---|---|---|---|---|
| 1 | Alert delivery to customer admin (`channel-type=email`) | ADR-0037 | Customer admin | **Resend** |
| 2 | Backfill completion / failure / cap-reached | ADR-0033 + rule #9 | Customer admin | **Resend** |
| 3 | 24h alert delivery failure digest | ADR-0037 | Customer admin | **Resend** |

### Code shape

- `backend/app/services/resend_service.py` — owns all three customer-facing paths. API key fetched from AWS Secrets Manager at startup, cached on the resend SDK module. Typed `ResendConfigError` (init failure) + `ResendDeliveryError` (per-send 4xx/5xx). Dry-run mode via `RESEND_DRY_RUN=1` env var. Test seam via `_set_initialized_for_tests` + `MagicMock` of `resend.Emails.send`.
- Caller sites: `forge_sync.py` (path #2 — `fire_terminal_state_email`) and `alert_dispatch.py` (paths #1 + #3 — `send_alert_email` + `send_failure_digest_email`) import from `resend_service`.

### CDK

- `infra/stacks/data_stack.py`: dedicated `sm.Secret` named `flow-intelligence/{env}/resend_api_key` (separate from `app_secrets`, see "Alternatives considered" below).
- `infra/stacks/compute_stack.py`: App Runner instance role gets `grant_read` on the new Secret; `RESEND_API_KEY_SECRET_ARN` env var injected into the backend service with the Secret's ARN as the value (not the secret value itself — the backend fetches via boto3 at startup).

### Settings

`backend/app/core/config.py` gains `resend_enabled`, `resend_from_address`, `resend_reply_to`. The actual API key value is NOT in this config (it's fetched at runtime from Secrets Manager); only the operational toggles + sender identity defaults live here.

### Free-tier monitoring strategy

A monthly manual check (confirm month-to-date is well under 3,000/mo in the Resend dashboard). The send-path emits a structured `resend.send.count` log line per delivery that can be grep'd in CloudWatch Logs to estimate volume if a manual check ever raises a flag. **Upgrade path is documented in the runbook**: once steady-state monthly volume crosses 1,000/mo, build a CloudWatch alarm on the `resend.send.count` metric and trip at 2,500 to give a buffer before the 3,000 cap. Building the alarm today would alert on a counter that reads near-zero — exactly the kind of premature plumbing CLAUDE.md tells us not to ship.

## Alternatives considered

### A. Postmark — ruled out

$15/month for 10k emails; widely recommended for the "SES-denied → switch to Postmark" path; mature deliverability. Loses on the free-tier comparison: Resend has a free tier (100/day, 3,000/mo) that comfortably covers projected volume; Postmark has no comparable free tier. For our projected volume, the free tier wins. If Resend ever throttles us off the free tier and volume justifies it, Postmark remains a clean second-choice fallback.

### B. Appeal SES denial again — ruled out

The denial reasoning gave no specific lever to address; a re-appeal would be effectively the same submission and likely the same response. Resend's onboarding accepts the use case immediately.

### C. Self-host SMTP / use Google Workspace SMTP — ruled out

Sender reputation is a multi-month investment that we cannot short-circuit. The whole point of using a managed transactional service is that they run the deliverability infrastructure. Self-hosted SMTP would land us in Gmail / Outlook spam folders immediately.

### D. Drop customer-facing email entirely — ruled out

Customers could still get alerts via Slack / Teams (those work end-to-end), but the proactive backfill-completion signal (ADR-0033 / rule #9) requires email. Killing it breaks a documented product surface.

### E. Co-locate the Resend key in `app_secrets` (JSON-blob Secret) — ruled out

`app_secrets` is a single JSON-blob Secret holding multiple operational secrets (e.g. the Anthropic API key). Co-locating Resend would save a trivial per-Secret cost. Rejected: vendor isolation for rotation + blast-radius (one bad write to the JSON blob nukes multiple credentials) is worth the trivial cost.

## Consequences

### Positive

- **Customer-facing email works again.** Resend's free tier covers projected volume; backfill notifications + alert email functional end-to-end.
- **Failure domain is bounded.** Resend handles the customer-facing surface as a single, self-contained service.
- **No new monthly recurring AWS cost.** Free tier; CloudWatch alarms deferred until volume justifies them.
- **Vendor-isolated rotation.** The Resend key lives in its own Secret; rotating it doesn't risk breaking the Anthropic key.
- **Sub-processor disclosure preserved.** Resend is added to the app's privacy-policy sub-processor list in the same change; the Marketplace listing's Privacy & Security section is updated in parallel.

### Negative / honest costs

- **Resend SDK transitive footprint heavier than expected.** `resend==2.30.1` pulls in `requests==2.34.2` + `charset-normalizer==3.4.7` (the SDK uses `requests`, not `httpx` as the project's other HTTP code does). Small extra dependency surface; not enough to justify rolling our own HTTP client against Resend's REST API.
- **Free-tier cap (3,000/mo) is a real ceiling.** Projected volume sits well below at current scale, but the runbook + the `resend.send.count` log breadcrumb are how we'd notice approaching it. Crossing into the paid tier is a configuration change, not a code change.
- **No production sender history on Resend.** Initial deliverability is unknown; could be better or worse than SES sandbox would have been at low volume. Resend's reputation pool is its mitigation, but observe bounce rates in the first weeks.
- **Sub-processor disclosure adds Resend to the privacy policy.** Customers who scrutinize sub-processors (security questionnaires) now have one more vendor to evaluate. Trade-off accepted; the alternative is "customer email doesn't work."

## Verification

- Local: `uv run pytest backend/tests/test_resend_service.py` passes; coverage on `resend_service.py` ≥80% (matches the backend gate, see CLAUDE.md rule #11).
- Local: `cd infra && uv run pytest` passes including assertions for the Resend Secret + env var.
- CI authoritative: post-push, `gh run list` shows backend + infra jobs green.
- Post-deploy: verify (a) Secret value populated in AWS Console; (b) domain verification complete in Resend dashboard; (c) the Marketplace listing's Privacy & Security section updated. First real customer-facing send is gated on (a), (b), (c) all green.

## Related

- **Builds on:** ADR-0033 (backfill notifications), ADR-0037 (alert delivery destinations) — the ADRs whose email paths are affected.
- **Cross-references:** CLAUDE.md rule #9 (proactive notification — why path #2 can't regress); rule #11 (don't cheat — applies to keeping the coverage gate green on the new module).
