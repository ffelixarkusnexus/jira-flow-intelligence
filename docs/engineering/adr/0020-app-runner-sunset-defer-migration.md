# 0020 — App Runner sunset: stay through Phase 2, plan migration in Phase 3

- **Status:** accepted
- **Date:** 2026-05-02
- **Decision-makers:** the maintainer
- **Tags:** #infra #aws #strategy

## Context and problem statement

On 2026-05-02 AWS notified us that App Runner is closing to new customers
on **2026-04-30** (two days before the email — we are grandfathered as
existing customers since the dev + prod stacks were already deployed).
Existing customers retain access; AWS commits to security patches and
defect fixes but **no new features**. AWS recommends migrating to
**Amazon ECS Express Mode** as the successor.

ADR-0012 picked App Runner specifically for its "containers without an
ALB" simplicity and the absence of operational surface area at our
scale. That premise is unchanged today, but a closed-to-new-customers
service is on a deprecation glide path. AWS hasn't published a full
sunset date, but historically that's 12-24 months out from this kind of
notice.

The choice today is when to migrate, not whether.

## Considered options

- **A. Stay on App Runner indefinitely.** Acceptable while AWS supports
  it. Risk compounds the longer we wait; eventually a forced migration
  on AWS' timeline.
- **B. Migrate now, before the first production Jira install.** Forces a
  detour through new infra at the worst possible moment — fresh
  customer demo, multiple things still settling.
- **C. Stay through Phase 2; plan a Phase 3 migration once the Forge
  install is stable.** Defers migration cost; lets us pick a destination
  with more information (ECS Express Mode maturity, AWS roadmap signals).
- **D. Migrate to a non-AWS host (Fly.io, Render).** Out of scope per
  ADR-0012's AWS-first stance for this customer base.

## Decision

**Option C.** Stay on App Runner through Phase 2 (company Jira install
+ first weeks of real-data operation). Open Phase 3 with a migration
ADR that picks a target (most likely ECS Express Mode if its CDK
support and console UX match App Runner's; otherwise classic Fargate).

## Consequences

**Positive**
- Zero disruption to the in-flight Phase 2 install. We don't multiply
  risk during the customer-facing rollout.
- Our existing deploy works exactly as documented (`backend/Dockerfile`,
  `infra/stacks/compute_stack.py`, `.github/workflows/deploy.yml`).
  AWS guarantees security patches and bug fixes.
- We get more signal on ECS Express Mode before committing — it's new
  enough that CDK support and operational maturity are still catching
  up to App Runner's.

**Negative**
- We carry technical debt with no fixed retirement date. If AWS
  announces a sunset deadline (12-24mo typical), Phase 3 becomes
  time-pressured.
- New AWS hires / contributors won't be able to spin up a *new*
  App Runner service in their own account to learn this codebase —
  they'd need to mirror our existing deploy or work in our account.

**Neutral**
- The CDK `compute_stack.py` will need rewriting to whatever the
  successor is. The container image, Dockerfile, and FORGE_APP_ID +
  database wiring stay portable. The bake-the-JWKS-into-the-image
  pattern (no NAT) carries forward if the successor still has the
  VPC-connector-without-NAT limitation; with a NAT-enabled posture
  we can drop that.

## Phase 3 migration scope (sketch — not committed)

When we open Phase 3, the migration ADR should cover:

- Pick the target (ECS Express Mode vs Fargate vs Lambda — Lambda likely
  out, see analysis in conversation log of 2026-05-02).
- Whether to add a NAT Gateway as part of the migration so the JWKS
  fetch can go live again (current bake-into-container pattern is a
  workaround; with public egress we can use `PyJWKClient` directly).
- Rolling-deploy strategy: dev first, soak, then prod cutover. Forge
  manifest baseUrl change must follow the new App Runner URL — or
  introduce a custom domain (CloudFront or App Runner custom domain →
  successor) so the Forge manifest doesn't have to change on infra
  swaps.

## Notes

This decision sits squarely in ADR-0012's "we accept this risk; if AWS
deprecates we have a re-platform on the roadmap" clause. Triggering the
re-platform is the next step, not the current one.
