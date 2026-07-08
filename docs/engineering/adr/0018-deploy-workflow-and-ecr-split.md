# 0018 — Deploy workflow and EcrStack split

- **Status:** accepted
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #ci-cd #aws #infrastructure

## Context and problem statement

ADR-0013 set GitHub Actions + OIDC as the deploy mechanism. ADR-0012 chose App Runner, which requires an image to exist in ECR before the App Runner service is created. The naïve "one big stack" approach hits a chicken-and-egg: CDK can't create the App Runner service until an image exists, and the image can't be pushed until the ECR repo exists.

This ADR records how the deploy workflow resolves this and the small CDK reorg (`EcrStack`) that supports it.

## Considered options

- **A. One big stack, push a placeholder image with AWS CLI before first deploy.** Workable but couples shell scripts to CDK ordering.
- **B. Split ECR into its own stack** so the workflow can deploy ECR first, push images, then deploy the rest.
- **C. Use App Runner's source-repository (CodeStar Connection to GitHub)** instead of ECR. Different deploy paradigm, more setup, doesn't fit the "GitHub Actions runs `cdk deploy`" pattern from ADR-0013.

## Decision

**Option B.** A new `EcrStack` holds the two ECR repositories. The workflow runs in stages:

1. **`cdk deploy EcrStack`** — creates ECR repos (idempotent on subsequent runs).
2. **`docker buildx build && push`** — backend and frontend images, both tagged `:latest` and `:$GITHUB_SHA`. ECR tag mutability is **MUTABLE** for Phase 1 so `:latest` can be reused.
3. **`cdk deploy NetworkStack DataStack ComputeStack ObservabilityStack`** with `-c image_tag=$GITHUB_SHA`. App Runner pulls the image identified by the SHA tag; deployment is fully reproducible.
4. **Smoke test** — poll `${BackendUrl}/healthz` for up to 5 minutes.

Branch → environment mapping (matches ADR-0013):
- `main` → prod
- `develop` → staging
- `feature/**` → dev
- `workflow_dispatch` allows overriding via input.

## Consequences

**Positive**
- First deploy on a fresh AWS account works end-to-end without any pre-step. The workflow itself bootstraps everything.
- The `image_tag` context flowing through CDK means every App Runner deploy points at a specific commit's image — pin-able, rollback-able.
- Splitting ECR also means we can drop+recreate the rest of the infra (e.g., during dev experimentation) without losing image history. ECR repos use `RemovalPolicy.RETAIN` outside dev.
- Caching: GitHub Actions cache (`type=gha`) speeds up Docker rebuilds dramatically — a no-op rebuild is ~30s instead of ~3min.
- One concurrency group per env serializes deploys; rapid-fire pushes don't race.

**Negative**
- Mutable `:latest` tags create a small auditability gap: knowing exactly which image is in App Runner requires inspecting the service's `image_identifier`, not just looking at "what's `:latest` in ECR right now." Phase 2 should switch to IMMUTABLE tags with the workflow always referencing `$GITHUB_SHA`.
- Two `cdk deploy` invocations means a slightly longer pipeline (~30s extra). Acceptable.
- The workflow assumes the `AWS_DEPLOY_ROLE_ARN` secret and `AWS_ACCOUNT_ID` / `AWS_REGION` vars are set in each GitHub environment. Documented in the runbook; first setup is a one-time manual step.

**Neutral**
- A future migration to "single big stack via ECR-pre-create-CLI" or to App Runner's GitHub source path would be a separate ADR.

## What we explicitly chose NOT to do

- **No CodePipeline.** GitHub Actions is the deploy harness (ADR-0013).
- **No `auto_deployments_enabled=True` on App Runner.** Deployments happen only when CI runs `cdk deploy`. This is intentional control: a stray `:latest` push doesn't auto-promote.
- **No `release` trigger.** `push` to a deploy branch is the trigger. Tags can be added later if we adopt SemVer releases.
- **No production approval gate yet.** Phase 1 has one operator (you). Add `environment.protection_rules` in GitHub for prod when there are more reviewers.

## Notes

The `id-token: write` permission is what enables OIDC. The `AWS_DEPLOY_ROLE_ARN` secret must reference a role that trusts `repo:${OWNER}/${REPO}:ref:refs/heads/${BRANCH}` — see the runbook for the trust policy template. The role has these managed policies in Phase 1: `IAMReadOnlyAccess` plus narrow custom policies for ECR, App Runner, RDS, Secrets Manager, CloudFormation, and CloudWatch. Tighten further before public Marketplace listing.
