#!/usr/bin/env bash
# scripts/github-environments.sh — create dev/staging/prod environments
# in the GitHub repo and set the AWS deploy vars/secret on each.
#
# Run after scripts/aws-bootstrap.sh. Idempotent — re-running overwrites
# secret/variable values without recreating the environments.
#
# Requires: gh CLI authenticated with admin access to the repo.

set -euo pipefail

# ---- Config — set these to your own before running -----------------------
REPO="<YOUR_GITHUB_ORG>/jira-flow-intelligence"   # your GitHub owner/repo
ACCOUNT_ID="<YOUR_AWS_ACCOUNT_ID>"                 # your 12-digit AWS account id
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/flow-intelligence-deploy"
REGION="us-east-1"
ENVS=(dev staging prod)

# ---- Verify gh auth ------------------------------------------------------
echo "==> Verifying gh auth"
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated. Run: gh auth login"; exit 1; }
echo "    OK"

# ---- Configure each environment ------------------------------------------
for env in "${ENVS[@]}"; do
  echo
  echo "==> Configuring environment: $env"

  # Create (or update) the environment.
  gh api --method PUT "repos/${REPO}/environments/${env}" \
    -H "Accept: application/vnd.github+json" >/dev/null
  echo "    environment ready"

  # Variables (plain config, not encrypted).
  gh variable set AWS_ACCOUNT_ID --env "$env" --body "$ACCOUNT_ID" --repo "$REPO" >/dev/null
  echo "    var:    AWS_ACCOUNT_ID = $ACCOUNT_ID"
  gh variable set AWS_REGION --env "$env" --body "$REGION" --repo "$REPO" >/dev/null
  echo "    var:    AWS_REGION     = $REGION"

  # Secret (encrypted on send via the env's public key).
  gh secret set AWS_DEPLOY_ROLE_ARN --env "$env" --body "$ROLE_ARN" --repo "$REPO" >/dev/null
  echo "    secret: AWS_DEPLOY_ROLE_ARN = (set, encrypted)"
done

# ---- Verify --------------------------------------------------------------
echo
echo "==> Verification"
for env in "${ENVS[@]}"; do
  echo "    [$env]"
  gh variable list --env "$env" --repo "$REPO" | sed 's/^/        /'
  gh secret list --env "$env" --repo "$REPO" | sed 's/^/        /'
done

echo
echo "==========================================================================="
echo "Done. Three environments configured."
echo
echo "Settings page:"
echo "    https://github.com/${REPO}/settings/environments"
echo
echo "Next: Phase 4 — push a feature/* branch to trigger a dev deploy."
echo "==========================================================================="
