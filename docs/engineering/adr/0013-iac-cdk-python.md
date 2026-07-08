# 0013 — Use AWS CDK (Python) for infrastructure-as-code

- **Status:** accepted
- **Date:** 2026-04-30
- **Decision-makers:** the maintainer
- **Tags:** #infrastructure #ci-cd #tooling

## Context and problem statement

ADR-0012 commits us to AWS. We need IaC from day one because (a) GitHub-environment-per-branch deploy is the org's existing pattern, (b) reproducible infra is required before multi-tenant production, and (c) PR-level review of infra changes alongside app code is non-negotiable.

The org has working CDK projects in **Python** with GitHub Actions OIDC, branch-based deploys, and per-environment secrets/vars. That's the strongest signal of fit.

## Considered options

- **A. AWS CDK (Python)** — first-party AWS, real language, matches existing org projects.
- **B. Terraform** — multi-cloud, broader public module ecosystem (especially HIPAA), graceful state migrations.
- **C. AWS SAM** — Lambda-centric, narrower scope than what we need.
- **D. CloudFormation directly** — no abstraction layer.
- **E. Pulumi** — CDK-like but multi-cloud; we don't need multi-cloud.

C and D are non-starters for the App Runner + RDS + VPC connector + Secrets shape (verbose, hand-wired). E adds multi-cloud surface we don't need. The real choice is A vs B.

## Decision

**AWS CDK in Python, code lives in `infra/` of this repo, deployed via GitHub Actions with OIDC role assumption (no static AWS keys).**

GitHub environments map to CDK stacks: `feature/*` → `dev`, `develop` → `staging`, `main` → `prod`. Per-environment configuration (account ID, region, instance sizes, alarm thresholds) injected via GitHub environment secrets/vars at deploy time.

## Consequences

**Positive**
- Org familiarity: zero training cost. Reviewers, on-call playbooks, and existing internal CDK utilities (if any) port directly.
- Python at the infra layer matches the backend's language. Same tooling (`uv`, `ruff`, `mypy`, `pytest`) extends to infra. No new language at the seam.
- AWS-native: new AWS services (App Runner, Aurora Serverless v2, etc.) historically land in CDK before Terraform's AWS provider.
- L2/L3 constructs collapse the App Runner + RDS + Secrets + VPC plumbing significantly vs. Terraform's HCL modules.
- Type-checked synth catches "passed a string where a `Port` is required" at PR review.
- Unit-testable infra via `aws_cdk.assertions.Template.from_stack(...).has_resource(...)`. We'll pin "App Runner must use VPC connector in prod" as an `assert_resource_properties` test.
- GitHub Actions OIDC + per-environment branch deploys is the org's existing pattern; CDK plugs in directly.

**Negative**
- **CloudFormation is the runtime.** CFN's slow rollouts and stuck-stack pathologies are inherited. Mitigations: keep stacks small (one per concern: networking, data, compute), use `cdk diff` aggressively in CI, accept slower deploys (~3–8 min per stack).
- **Logical ID renames force resource recreation.** Renaming a construct's logical ID without an `overrideLogicalId` or `RenameAspect` recreates the resource. We'll document this in the runbook and gate state-affecting renames behind explicit reviewer sign-off.
- **AWS lock-in.** CDK targets CloudFormation. We accept this — multi-cloud is a non-goal for this project.
- TypeScript is the more popular CDK language; some constructs and examples online are TS-first. Mitigation: the L2/L3 surfaces we care about are equally complete in Python.

**Neutral**
- We commit to CDK for *this app*. A future Jira plugin in the family that's, say, multi-cloud or Lambda-heavy may choose differently — that's a separate ADR for that project.

## What we explicitly choose NOT to do

- **No CDK Pipelines (CodePipeline/CodeBuild).** GitHub Actions runs `cdk deploy`. Reusing the org's existing CI pattern beats introducing a second deploy mechanism.
- **No mixing CDK + Terraform.** One IaC tool per project.
- **No L4 / overly-abstract constructs.** L2 (`aws_apprunner.CfnService`, `aws_rds.DatabaseInstance`) plus our own thin wrappers where it earns its keep. Avoid `ApplicationLoadBalancedFargateService`-style mega-constructs that hide too much.
- **No `cdk bootstrap` per developer machine.** Bootstrap is run once per account by an admin; CI uses the assumed role.

## Repository layout

```
infra/
  app.py                  # CDK entrypoint
  stacks/
    network_stack.py      # VPC (Phase 2), security groups
    data_stack.py         # RDS, Secrets Manager
    compute_stack.py      # App Runner services, ECR repos
    observability_stack.py # CloudWatch alarms, billing alarm
  constructs/             # in-house L3 constructs (when justified)
  tests/                  # snapshot + assertion tests
  cdk.json
  pyproject.toml          # separate deps from backend; aws-cdk-lib + constructs
```

The infra package gets its own `pyproject.toml` so CDK deps don't pollute the application's runtime. Both can coexist via `uv` workspaces.

## CI/CD flow

GitHub Actions workflow `.github/workflows/deploy.yml` (separate from `ci.yml`):

1. Trigger: push to `main` (prod), `develop` (staging), or PR labels (dev).
2. Authenticate: `aws-actions/configure-aws-credentials` with OIDC (no static keys).
3. Build & push backend + frontend container images to ECR (immutable tags by commit SHA).
4. `cdk diff` (PR comment for visibility).
5. `cdk deploy --require-approval=never --all` (only on push to a deploy branch, not on PR).
6. Post-deploy smoke check: `curl /healthz` against the deployed App Runner URL.

Per-env config is injected via GitHub environment vars/secrets:
- `AWS_ACCOUNT_ID`, `AWS_REGION` (vars)
- `AWS_DEPLOY_ROLE_ARN` (secret, the OIDC trust role)
- App-level config (CIDRs, instance class) via CDK `--context`

## Notes

If we ever need a non-AWS resource (Cloudflare DNS, Datadog dashboards), we add a small Terraform sidecar or use the community CDK constructs for that vendor — we don't switch tools wholesale.

Pre-existing org CDK projects: confirm they follow the same `pyproject.toml`-per-stack pattern and lift any internal construct library; otherwise we start clean.
