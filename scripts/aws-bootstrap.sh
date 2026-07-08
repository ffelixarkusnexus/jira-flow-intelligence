#!/usr/bin/env bash
# scripts/aws-bootstrap.sh — one-time AWS account setup for the deploy workflow.
#
# Run ONCE per AWS account. Idempotent: safe to re-run; existing OIDC provider
# and role are detected and only updated where needed.
#
# What it does:
#   1. Verifies AWS identity matches the expected account
#   2. Creates the GitHub OIDC identity provider (skipped if it exists)
#   3. Creates IAM role `flow-intelligence-deploy` with a trust policy locked
#      to this repo's branches
#   4. Attaches managed policies for CDK to manage every service we use
#   5. Runs `cdk bootstrap` for us-east-1 (creates the CDKToolkit stack —
#      assets bucket, ECR for assets, exec roles)
#   6. Prints the role ARN to paste into GitHub environment secrets
#
# IAM scope (Phase 1 — broad service-level managed policies):
#   - ECR full         (repo + image management)
#   - RDS full         (instance lifecycle)
#   - App Runner full  (service create/update/delete)
#   - VPC full         (subnets + SGs)
#   - Secrets Manager  (read/write app secrets)
#   - CloudWatch v2    (logs + alarms)
#   - CloudFormation   (CDK uses CFN under the hood)
#   - SSM full         (CDK bootstrap qualifier parameter)
#   - IAM full         (CDK creates roles for the services it stands up)
#
# Tightening note: this is broader than necessary. Phase 2 hardening
# (separate ADR) will narrow to a custom policy with only the specific
# resources/actions used by the deploy.

set -euo pipefail

# ---- Config — set these to your own before running -----------------------
EXPECTED_ACCOUNT="<YOUR_AWS_ACCOUNT_ID>"          # your 12-digit AWS account id
REPO="<YOUR_GITHUB_ORG>/jira-flow-intelligence"   # your GitHub owner/repo
ROLE_NAME="flow-intelligence-deploy"
REGION="us-east-1"

# ---- 1. Verify AWS identity ----------------------------------------------
echo "==> 1/5 Verifying AWS identity"
ACTUAL_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ACTUAL_ARN=$(aws sts get-caller-identity --query Arn --output text)
if [ "$ACTUAL_ACCOUNT" != "$EXPECTED_ACCOUNT" ]; then
  echo "ERROR: expected account $EXPECTED_ACCOUNT, got $ACTUAL_ACCOUNT"
  echo "       ($ACTUAL_ARN)"
  echo "       Set the right AWS_PROFILE or aws configure first."
  exit 1
fi
echo "    OK — $ACTUAL_ARN"

# ---- 2. OIDC provider -----------------------------------------------------
OIDC_ARN="arn:aws:iam::${EXPECTED_ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
echo
echo "==> 2/5 GitHub OIDC identity provider"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  echo "    already exists ($OIDC_ARN)"
else
  echo "    creating..."
  aws iam create-open-id-connect-provider \
    --url "https://token.actions.githubusercontent.com" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list \
        "6938fd4d98bab03faadb97b34396831e3780aea1" \
        "1c58a3a8518e8759bf075b76b750d4f2df264fcd" >/dev/null
  echo "    created"
fi

# ---- 3. Deploy role -------------------------------------------------------
echo
echo "==> 3/5 IAM role $ROLE_NAME"
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${OIDC_ARN}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:${REPO}:*"}
    }
  }]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "    already exists; refreshing trust policy"
  aws iam update-assume-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-document "$TRUST_POLICY"
else
  echo "    creating..."
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --description "GitHub Actions deploy role for $REPO" \
    --max-session-duration 3600 >/dev/null
  echo "    created"
fi

# ---- 4. Managed policies --------------------------------------------------
echo
echo "==> 4/5 Attaching managed policies"
POLICIES=(
  "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess"
  "arn:aws:iam::aws:policy/AmazonRDSFullAccess"
  "arn:aws:iam::aws:policy/AWSAppRunnerFullAccess"
  "arn:aws:iam::aws:policy/SecretsManagerReadWrite"
  "arn:aws:iam::aws:policy/CloudWatchFullAccessV2"
  "arn:aws:iam::aws:policy/AWSCloudFormationFullAccess"
  "arn:aws:iam::aws:policy/AmazonVPCFullAccess"
  "arn:aws:iam::aws:policy/IAMFullAccess"
  "arn:aws:iam::aws:policy/AmazonSSMFullAccess"
  "arn:aws:iam::aws:policy/AmazonS3FullAccess"
)
for p in "${POLICIES[@]}"; do
  aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$p"
  echo "    attached: ${p##*/}"
done

# ---- 5. CDK bootstrap -----------------------------------------------------
echo
echo "==> 5/5 Running cdk bootstrap"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../infra"
AWS_ACCOUNT_ID="$EXPECTED_ACCOUNT" AWS_REGION="$REGION" \
  uv run cdk bootstrap "aws://${EXPECTED_ACCOUNT}/${REGION}"

# ---- Done -----------------------------------------------------------------
ROLE_ARN="arn:aws:iam::${EXPECTED_ACCOUNT}:role/${ROLE_NAME}"
echo
echo "==========================================================================="
echo "Done. Bootstrap complete."
echo
echo "Role ARN to paste into GitHub environment secret AWS_DEPLOY_ROLE_ARN:"
echo
echo "    $ROLE_ARN"
echo
echo "GitHub environment vars (per env: dev, staging, prod):"
echo "    AWS_ACCOUNT_ID = $EXPECTED_ACCOUNT"
echo "    AWS_REGION     = $REGION"
echo
echo "Next: Phase 3 — set up GitHub environments at"
echo "    https://github.com/${REPO}/settings/environments"
echo "==========================================================================="
