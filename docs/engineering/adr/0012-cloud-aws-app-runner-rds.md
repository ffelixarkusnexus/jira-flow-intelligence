# 0012 — Run on AWS: App Runner + RDS Postgres, single account to start

- **Status:** accepted
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #infrastructure #aws #cost

## Context and problem statement

Atlassian Connect (ADR-0010) requires a public HTTPS endpoint. Our company is healthcare-adjacent (X12 837/835 RCM); existing AWS posture, BAA, and infosec patterns make AWS the right cloud even though no PHI flows through this app. We chose AWS over Fly.io / Vercel after weighing the one-way-door risk of starting outside AWS.

Within AWS, we need to pick the compute shape that minimizes ops while staying compatible with where the system goes (multi-tenant production, Marketplace listing).

## Considered options

| Shape | 2-week test cost | Steady-state monthly | Setup |
|---|---|---|---|
| **App Runner ×2 + RDS t4g.micro** | ~$25–30 | ~$55–65 | Low |
| Fargate + ALB + RDS + NAT GW | ~$30–45 | ~$80–100 | Medium-high |
| Lambda + API GW + RDS Proxy + RDS | ~$15–20 | ~$30–45 | High (Mangum, Next.js SSR adapter) |
| Lightsail container ×2 + Lightsail DB | ~$15 | ~$30 | Lowest, but isolated from IAM/VPC/Secrets |

## Decision

**Two App Runner services (backend + frontend) + RDS for PostgreSQL `db.t4g.micro` Single-AZ + Secrets Manager + CloudWatch Logs + ECR. Single AWS account for Phase 1, region `us-east-1`.**

## Consequences

**Positive**
- App Runner gives auto-HTTPS, autoscale-to-near-zero (pauses paid compute after ~60s idle), VPC connector available when we lock down RDS access. No ALB monthly fee. No NAT Gateway in the simple path.
- RDS `db.t4g.micro` is real Postgres: KMS-at-rest, automated daily backups, point-in-time recovery, IAM auth available. Drop-in upgrade to a larger instance class when we need it.
- Secrets Manager + IAM holds OAuth tokens, Atlassian shared secrets, and (optionally) Anthropic keys. Right place from day one.
- CloudWatch is fine for now; Datadog/Honeycomb can be added later without re-platforming.
- ECR for container images, GitHub Actions OIDC into AWS for deploys (no static AWS keys in CI).

**Negative**
- App Runner has a small cold start (~5–10s after a long idle). Acceptable for an in-Jira iframe; a noticeable hiccup the first time after a quiet weekend. Mitigation: a tiny CloudWatch synthetic at hourly cadence keeps it warm during business hours.
- Single-AZ RDS for cost. Flip to Multi-AZ when an availability SLA warrants it.
- RDS free tier doesn't apply to `db.t4g.micro` after the 12-month grace. Steady-state ~$11/month.
- Single AWS account is fine for Phase 1; we'll move to Organizations + dev/staging/prod accounts once we have a real customer (separate ADR).

**Neutral**
- If/when we outgrow App Runner (custom networking needs, multi-cluster), Fargate is a one-day port. The Dockerfiles work in either.
- `us-east-1` is the cheapest, has every service, and is the default for the AWS ecosystem. Switching regions later is non-trivial; we accept this for the test phase.

## Operational gotchas (documented for the runbook)

- **CloudWatch Logs default retention is 365 days.** Set to 14 days for the test environment; bump per env later.
- **NAT Gateway** is $32/month flat + per-GB. App Runner egress to Atlassian doesn't need a NAT. Don't add VPC + NAT unless we need RDS in a private subnet (we will, in Phase 2; Phase 1 uses RDS publicly with security group + IAM auth).
- **Aurora Serverless v2** floor is 0.5 ACU = ~$43/month idle. Don't fall for "serverless = free when idle." Vanilla RDS is cheaper at our scale.
- **Reserved instances** are not relevant before steady-state.

## Phase 1 cost ceiling

Backend App Runner + Frontend App Runner + RDS `db.t4g.micro` + Secrets + Logs + ECR + minor data transfer = **~$60/month steady, ~$25–30 for the 2-week test window** if traffic stays minimal. Hard cap a CloudWatch billing alarm at $100/month to catch surprises.

## Notes

This ADR locks the compute shape, not the IaC tool. ADR-0013 covers IaC. Multi-account organization is deferred to a future ADR, triggered when an availability SLA or compliance requirement warrants it.
