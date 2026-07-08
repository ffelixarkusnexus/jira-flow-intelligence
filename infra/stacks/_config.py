"""Per-environment configuration consumed by the CDK stacks.

Read at synth time from `cdk.json` context (`-c env=dev|staging|prod`) and
GitHub Actions environment vars/secrets. Keep this file the single source of
truth for what differs across environments.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvConfig:
    name: str
    aws_account: str | None
    aws_region: str
    rds_instance_class: str
    rds_allocated_storage_gb: int
    rds_multi_az: bool
    rds_backup_retention_days: int
    log_retention_days: int
    billing_alarm_usd: int
    app_runner_cpu: str
    app_runner_memory: str
    # NAT Gateway gives the App Runner VPC connector a path to the public
    # internet (Anthropic API, any external HTTP). $0.045/hr (~$32/mo) +
    # data transfer. Toggle False on dev when not actively testing AI;
    # toggle True before AI work, deploy, then back. Prod stays True
    # because end users see AI explanations on every bottleneck card.
    # See ADR-0012, the runbook section "Toggle the dev NAT Gateway".
    nat_gateway: bool
    # ALLOW_DEMO_SEED gates the /api/dev/seed-demo endpoint. When True,
    # the dev router is mounted and any authenticated install can seed
    # its own tenant with 250 synthetic issues + 5 sprints + a designed
    # Review-stage bottleneck. Security is gated on a per-tenant basis:
    # callers can only seed their own tenant (FIT-auth-bound). Worst case
    # if leaked is a customer install seeding fake data into their own
    # tenant, which they could obviously detect — no cross-tenant impact.
    # Set True on prod during heavy testing periods; flip back to False
    # before publicly inviting new customers.
    allow_demo_seed: bool


_PROFILES: dict[str, EnvConfig] = {
    "dev": EnvConfig(
        name="dev",
        aws_account=None,  # filled by env var at synth
        aws_region="us-east-1",
        rds_instance_class="db.t4g.micro",
        rds_allocated_storage_gb=20,
        rds_multi_az=False,
        rds_backup_retention_days=1,
        log_retention_days=7,
        billing_alarm_usd=50,
        app_runner_cpu="0.25 vCPU",
        app_runner_memory="0.5 GB",
        # CDK keeps NAT True for dev because flipping to False breaks the
        # cross-stack import of private subnet IDs that ComputeStack already
        # consumed. To stop the meter without redesigning the stack: delete
        # the NAT Gateway by hand (`aws ec2 delete-nat-gateway --nat-gateway-id
        # <id>`). CDK will recreate it on the next deploy. See the runbook
        # section "Toggle the dev NAT Gateway".
        nat_gateway=True,
        allow_demo_seed=True,  # dev backend always exposes the seed endpoint
    ),
    "staging": EnvConfig(
        name="staging",
        aws_account=None,
        aws_region="us-east-1",
        rds_instance_class="db.t4g.micro",
        rds_allocated_storage_gb=20,
        rds_multi_az=False,
        rds_backup_retention_days=7,
        log_retention_days=14,
        billing_alarm_usd=100,
        app_runner_cpu="0.25 vCPU",
        app_runner_memory="0.5 GB",
        nat_gateway=True,
        allow_demo_seed=False,  # staging mirrors prod posture
    ),
    "prod": EnvConfig(
        name="prod",
        aws_account=None,
        aws_region="us-east-1",
        rds_instance_class="db.t4g.small",
        rds_allocated_storage_gb=50,
        rds_multi_az=False,  # flip to True before public Marketplace listing
        rds_backup_retention_days=14,
        log_retention_days=30,
        billing_alarm_usd=200,
        app_runner_cpu="0.5 vCPU",
        app_runner_memory="1 GB",
        # Permanent on. End users see AI explanations on every dashboard load.
        nat_gateway=True,
        # False on prod (defense-in-depth alongside the UI environment-type
        # gate in SettingsTab.tsx, which already hides the panel on prod
        # installs). The 2026-05-26→2026-05-27 heavy-testing window where
        # this was True is past; dashboards on the prod Forge install
        # already have real data via webhook sync. Flip to True only for
        # short re-seed windows; flip back the same session.
        allow_demo_seed=False,
    ),
}


def load_config(env: str, *, aws_account: str | None) -> EnvConfig:
    if env not in _PROFILES:
        raise ValueError(f"Unknown env '{env}'; expected one of {sorted(_PROFILES)}")
    base = _PROFILES[env]
    return EnvConfig(
        name=base.name,
        aws_account=aws_account or base.aws_account,
        aws_region=base.aws_region,
        rds_instance_class=base.rds_instance_class,
        rds_allocated_storage_gb=base.rds_allocated_storage_gb,
        rds_multi_az=base.rds_multi_az,
        rds_backup_retention_days=base.rds_backup_retention_days,
        log_retention_days=base.log_retention_days,
        billing_alarm_usd=base.billing_alarm_usd,
        app_runner_cpu=base.app_runner_cpu,
        app_runner_memory=base.app_runner_memory,
        nat_gateway=base.nat_gateway,
        allow_demo_seed=base.allow_demo_seed,
    )
