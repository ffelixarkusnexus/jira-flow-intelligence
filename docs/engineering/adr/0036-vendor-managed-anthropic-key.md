# ADR-0036: Vendor-managed Anthropic API key (no BYOK), defer per-tenant tracking + caps

- **Status:** Accepted
- **Date:** 2026-05-27
- **Deciders:** the maintainer
- **Related:** ADR-0006 (deterministic engine; AI is text-only), ADR-0011 (privacy and sub-processor disclosure)

## Context

On a Marketplace-published install of the app, no Anthropic API key is requested from the customer. The Anthropic key is sourced exclusively from AWS Secrets Manager (`AppSecret:anthropic_api_key`) and injected as `ANTHROPIC_API_KEY` env into the prod App Runner backend. The call site that uses it:

1. `backend/app/services/ai_explanation.py` — per-dashboard-load AI sentence on the bottleneck card (customer-triggered, runs for every install).

Every install hits the vendor-managed Anthropic account. This is **the intended architecture**, not a leak — but it had never been written down as an explicit decision, and the cost/risk profile is worth documenting before committing to it through scale-up.

### Cost profile (observed)

- Per dashboard-card call (Sonnet 4.6, ~2k input tokens, ~80 output tokens): ~$0.001–$0.005.
- Active customer dashboard usage: ~500–2,500 calls/month.
- Per-tenant Anthropic cost: **~$0.50–$12/month**.

The per-install cost is small enough that per-tenant metering has near-zero payoff today; it becomes worth instrumenting only once aggregate AI spend is large enough that a single tenant could move the total.

### Existing disclosure

- The app's privacy policy lists Anthropic as a sub-processor.
- The security disclosure documents the Anthropic dependency.
- ADR-0006 establishes the AI-is-text-translation-only stance.

Customers consent to vendor-managed AI at install time.

## Decision

1. **Stay with a vendor-managed Anthropic key (Option A).** A single vendor-managed key serves all customer dashboard AI calls. Customers are not asked to supply their own key. Documentation aligns; standard Marketplace pattern.
2. **Defer per-tenant usage tracking + per-tenant soft rate cap (Options B+C bundle) until the trigger condition fires.** No code work today.
3. **Trigger condition for the B+C bundle:** when aggregate AI spend grows enough that per-tenant attribution is worth having — i.e. when a single tenant could plausibly drive a material fraction of the total. Below that, near-zero variable cost is not worth instrumenting.
4. **Operational hedge active today:** Anthropic console billing alerts. This is the cheap, blast-radius-bounded safeguard for now.
5. **BYOK (Option D) is rejected for now.** Setup friction hurts install/adoption for a per-dashboard-sentence feature; revisit only if a security/compliance requirement specifically demands it.

## What B+C will look like when it ships

**B — Per-tenant usage tracking:**

- Backend logs `tenant_id`, model, input/output token counts, and cost estimate alongside every Anthropic call.
- Rolled up into a `metrics_anthropic_usage` table keyed by `(tenant_id, day)`.
- An admin view surfaces top-N tenants by AI spend.
- Lets the maintainer answer "is any single tenant driving disproportionate spend?" with data, not gut.

**C — Per-tenant soft rate cap:**

- Backend counts AI calls per tenant per day.
- Above threshold (initial guess: 200/day per tenant, calibrated from B data), the AI explanation falls back to the **deterministic template** (already present per ADR-0006). Customer-facing experience is graceful — the bottleneck card still renders; only the AI-written sentence is replaced with the deterministic one.
- No hard error, no customer notification, no support ticket.
- Caps blast radius if a single tenant has aberrant usage (e.g., a customer-side dashboard auto-refresh script).

**Estimated work to land both:** ~1.5 days.

## Consequences

### Positive

- Zero engineering work today on a non-urgent issue.
- Decision and trigger condition are documented; a future maintainer won't re-derive this reasoning.

### Negative

- **Single point of failure on the Anthropic key.** If the vendor-managed key is revoked, compromised, or hits a billing block, ALL customer dashboards lose the AI sentence at once. Mitigations: (a) the deterministic fallback per ADR-0006 means the dashboard still renders — only the AI sentence is missing; (b) recovery is "regenerate key, redeploy backend," ~10 min on App Runner. Not catastrophic.
- **No per-tenant attribution until B+C ships.** Until then, the maintainer can't tell which tenant is driving spend. Mitigated short-term by the Anthropic console billing alert.
- **No per-tenant rate cap until C ships.** A single tenant could in principle drive disproportionate Anthropic spend. Mitigated by (a) Forge's own request rate limits at the front-door, (b) the limited surface area (one AI call per dashboard load), and (c) the billing alert giving a known reaction window.

### Trigger to revisit

Any of which fires the B+C work:

1. **Usage trigger (planned):** aggregate AI usage/spend grows to where per-tenant attribution is worth having.
2. **Cost trigger (emergency):** the Anthropic billing alert fires unexpectedly, or monthly Anthropic spend exceeds a set threshold.
3. **Compliance trigger (optional):** a customer explicitly requests BYOK as a security/compliance condition. Revisits D (BYOK), not just B+C.

## References

- `backend/app/services/ai_explanation.py` — the per-dashboard-load AI call path.
- `backend/app/core/config.py` — `anthropic_api_key` field, sourced from `AppSecret:anthropic_api_key`.
- `infra/stacks/compute_stack.py` — `ANTHROPIC_API_KEY` env binding via App Runner instance role.
- The app's privacy policy — Anthropic sub-processor disclosure.
