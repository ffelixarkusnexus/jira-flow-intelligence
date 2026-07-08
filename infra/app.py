#!/usr/bin/env python3
"""CDK entrypoint.

Usage (locally):
  cd infra
  uv sync
  cdk synth -c env=dev
  cdk diff  -c env=dev
  cdk deploy -c env=dev -c image_tag=$(git rev-parse --short HEAD)

CI passes -c env=<dev|staging|prod> based on the branch (feature/* -> dev,
develop -> staging, main -> prod) and AWS_ACCOUNT_ID via env var. See ADR-0013.

Stack order (matches deploy.yml):
  1. EcrStack          — created first; deploy workflow then pushes images
  2. NetworkStack      — VPC + security groups (no NAT)
  3. DataStack         — RDS + Secrets Manager
  4. ComputeStack      — VPC connector + App Runner services
  5. ObservabilityStack — alarms + log groups
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks._config import load_config
from stacks.compute_stack import ComputeStack
from stacks.data_stack import DataStack
from stacks.ecr_stack import EcrStack
from stacks.network_stack import NetworkStack
from stacks.observability_stack import ObservabilityStack


def main() -> None:
    app = cdk.App()
    env_name = app.node.try_get_context("env") or "dev"
    aws_account = os.environ.get("AWS_ACCOUNT_ID") or app.node.try_get_context("aws_account")
    image_tag = app.node.try_get_context("image_tag") or "latest"
    cfg = load_config(env_name, aws_account=aws_account)

    cdk_env = cdk.Environment(account=cfg.aws_account, region=cfg.aws_region)
    suffix = f"-{cfg.name}"

    ecr = EcrStack(app, f"FlowIntelligenceEcr{suffix}", cfg=cfg, env=cdk_env)
    network = NetworkStack(app, f"FlowIntelligenceNetwork{suffix}", cfg=cfg, env=cdk_env)
    data = DataStack(
        app,
        f"FlowIntelligenceData{suffix}",
        cfg=cfg,
        vpc=network.vpc,
        rds_security_group=network.rds_sg,
        env=cdk_env,
    )
    compute = ComputeStack(
        app,
        f"FlowIntelligenceCompute{suffix}",
        cfg=cfg,
        vpc=network.vpc,
        vpc_connector_subnets=network.vpc_connector_subnets,
        app_runner_security_group=network.app_runner_sg,
        app_runner_security_group_inactive=network.app_runner_sg_inactive,
        db=data.db,
        db_credentials=data.db_credentials,
        app_secrets=data.app_secrets,
        resend_api_key_secret=data.resend_api_key_secret,
        backend_repo=ecr.backend_repo,
        frontend_repo=ecr.frontend_repo,  # see ComputeStack docstring; drop after this deploy
        image_tag=image_tag,
        env=cdk_env,
    )
    # Alert recipient + prod healthz host are per-deploy config: env var first,
    # then `-c <key>=...` CDK context; otherwise the stack falls back to its
    # documented defaults. Passing nothing keeps the stack default.
    obs_kwargs: dict[str, str] = {}
    alert_email = os.environ.get("ALERT_EMAIL") or app.node.try_get_context("alert_email")
    if alert_email:
        obs_kwargs["alert_email"] = alert_email
    healthz_host = os.environ.get("HEALTHZ_HOST") or app.node.try_get_context("healthz_host")
    if healthz_host:
        obs_kwargs["healthz_host"] = healthz_host
    ObservabilityStack(
        app,
        f"FlowIntelligenceObservability{suffix}",
        cfg=cfg,
        backend_service=compute.backend_service,
        env=cdk_env,
        **obs_kwargs,
    )

    app.synth()


if __name__ == "__main__":
    main()
