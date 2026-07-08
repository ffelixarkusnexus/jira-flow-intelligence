"""VPC + security groups.

Two shapes, gated on `cfg.nat_gateway`:

- **NAT off (cheap default for dev):** Single pair of public subnets
  across two AZs. App Runner VPC connector lives in those public
  subnets; ENIs have no public IP, so they reach RDS via SG rule but
  CANNOT reach the public internet (App Runner docs warn about exactly
  this). AI explanations and other public HTTP fall back to templates.

- **NAT on:** Public subnets for the NAT Gateway + RDS. Private subnets
  (PRIVATE_WITH_EGRESS) for the App Runner VPC connector — the NAT gives
  them outbound internet, RDS still reachable via SG.

To make toggling work in CDK without cross-stack-export pain, we
*always* create both App Runner SGs (`AppRunnerSg` for the public-subnet
variant and `AppRunnerSgPrivate` for the private-subnet variant). RDS
allows ingress from both. Compute picks the one matching the current
state. The unused SG sits there at $0 cost and is the seam that lets
the toggle replace the VPC connector cleanly — App Runner rejects a
connector update where (security_groups,) matches an existing connector,
so the new connector must use a different SG.

Toggling adds/removes a NAT Gateway + private subnets and swaps which
SG the connector references. Brief connector-replacement window. RDS
data not affected. See the runbook section "Toggle the dev NAT Gateway".
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack, Tags
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from stacks._config import EnvConfig


class NetworkStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("env", cfg.name)
        Tags.of(self).add("app", "flow-intelligence")

        subnet_config = [
            ec2.SubnetConfiguration(
                name="public",
                subnet_type=ec2.SubnetType.PUBLIC,
                cidr_mask=24,
            ),
        ]
        if cfg.nat_gateway:
            subnet_config.append(
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            )

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1 if cfg.nat_gateway else 0,
            subnet_configuration=subnet_config,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
        )

        # Subnets the App Runner VPC connector should attach to. Private
        # subnets when NAT is on (gives them public-internet egress);
        # public subnets when off (NAT-less fallback, internet egress dies).
        self.vpc_connector_subnets: list[ec2.ISubnet] = (
            list(self.vpc.private_subnets) if cfg.nat_gateway else list(self.vpc.public_subnets)
        )

        # Both SGs created unconditionally. The active one is picked below
        # via `app_runner_sg`. The other costs nothing and unblocks future
        # toggles without resource-replacement pain.
        sg_public = ec2.SecurityGroup(
            self,
            "AppRunnerSg",
            vpc=self.vpc,
            # Description is verbatim from the original Phase-1 deploy. CFN
            # treats SG descriptions as immutable; touching this string
            # forces SG replacement and breaks the cross-stack export
            # Compute imports.
            description="Egress for App Runner services to RDS / Secrets",
            allow_all_outbound=True,
        )
        sg_private = ec2.SecurityGroup(
            self,
            "AppRunnerSgPrivate",
            vpc=self.vpc,
            description="Egress for App Runner services - private-subnet variant",
            allow_all_outbound=True,
        )

        # Active vs inactive SG for the current state. Compute stack
        # references BOTH so cross-stack exports stay live across toggles —
        # otherwise CDK tries to drop the inactive export on Network's
        # deploy while Compute still imports it (same family of issue we
        # hit with the frontend ECR repo during its retirement).
        if cfg.nat_gateway:
            self.app_runner_sg: ec2.SecurityGroup = sg_private
            self.app_runner_sg_inactive: ec2.SecurityGroup = sg_public
        else:
            self.app_runner_sg = sg_public
            self.app_runner_sg_inactive = sg_private

        # SG for RDS: ingress from EITHER App Runner SG on Postgres port only.
        # Allowing both means a toggle doesn't have to wait on RDS-ingress
        # replacement; only the unused SG's path is dead at any moment.
        self.rds_sg = ec2.SecurityGroup(
            self,
            "RdsSg",
            vpc=self.vpc,
            description="RDS - ingress only from App Runner SG on 5432",
            allow_all_outbound=False,
        )
        self.rds_sg.add_ingress_rule(
            peer=sg_public,
            connection=ec2.Port.tcp(5432),
            description="App Runner (public-variant) to Postgres",
        )
        self.rds_sg.add_ingress_rule(
            peer=sg_private,
            connection=ec2.Port.tcp(5432),
            description="App Runner (private-variant) to Postgres",
        )
