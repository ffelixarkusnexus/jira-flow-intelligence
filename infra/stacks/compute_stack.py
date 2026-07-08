"""ECR + App Runner services for backend and frontend.

Phase 1 (ADR-0012, ADR-0017):
- One ECR repo per service (backend + frontend).
- One App Runner service per repo, fronted by Atlassian.
- VPC connector wires App Runner egress through the network stack so it can
  reach RDS via the security group rule defined there.
- Secrets are injected via App Runner environment configuration, fetched
  from Secrets Manager at deploy time. The DB connection URL is composed
  from the credentials secret + the RDS endpoint.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Stack, Tags
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from stacks._config import EnvConfig


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        vpc: ec2.IVpc,
        vpc_connector_subnets: list[ec2.ISubnet],
        app_runner_security_group: ec2.ISecurityGroup,
        app_runner_security_group_inactive: ec2.ISecurityGroup,
        db: rds.IDatabaseInstance,
        db_credentials: sm.ISecret,
        app_secrets: sm.ISecret,
        resend_api_key_secret: sm.ISecret,
        backend_repo: ecr.IRepository,
        # Connect-era leftover. Kept on the ComputeStack signature for ONE
        # deploy after the frontend-repo retirement so CFN can finish unwiring
        # the cross-stack import from EcrStack before EcrStack drops the
        # resource. Drop in a follow-up PR.
        frontend_repo: ecr.IRepository | None = None,
        image_tag: str = "latest",
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("env", cfg.name)
        Tags.of(self).add("app", "flow-intelligence")

        # ECR repos are passed in from EcrStack so the deploy workflow can
        # create them, push images, then create App Runner services that
        # reference real images.
        self.backend_repo = backend_repo
        # Touch the legacy frontend repo so CDK keeps the cross-stack export
        # alive on this deploy. The next PR drops both ends together.
        if frontend_repo is not None:
            CfnOutput(self, "LegacyFrontendRepoUri", value=frontend_repo.repository_uri)

        # Touch the inactive App Runner SG too so its cross-stack export
        # from Network stays alive across nat_gateway toggles. Without this,
        # toggling forces Network to drop the export while Compute hasn't
        # yet redeployed to update its import → the deploy that flips
        # nat_gateway always rolls back. See network_stack.py for the
        # active/inactive split.
        CfnOutput(
            self,
            "InactiveAppRunnerSgRef",
            value=app_runner_security_group_inactive.security_group_id,
        )
        self.image_tag = image_tag

        # VPC connector — App Runner uses this for any egress that needs to
        # land inside our VPC (RDS via SG rule). Subnets are decided by the
        # network stack: public when NAT is off, private when NAT is on.
        # Connector name varies by mode so toggling can do create-new-then-
        # delete-old without colliding on App Runner's name uniqueness.
        connector_suffix = "private" if cfg.nat_gateway else "public"
        self.vpc_connector = apprunner.CfnVpcConnector(
            self,
            "VpcConnector",
            subnets=[s.subnet_id for s in vpc_connector_subnets],
            security_groups=[app_runner_security_group.security_group_id],
            vpc_connector_name=f"flow-{cfg.name}-{connector_suffix}",
        )

        # IAM — App Runner instance role. Has read access to the credentials
        # and app secrets only; no broad AWS permissions.
        self.instance_role = iam.Role(
            self,
            "AppRunnerInstanceRole",
            assumed_by=iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
        )
        db_credentials.grant_read(self.instance_role)
        app_secrets.grant_read(self.instance_role)
        # ADR-0040: backend fetches Resend API key at startup via boto3
        # (RESEND_API_KEY_SECRET_ARN env var below). Read-only — the
        # backend never writes; operator rotates via AWS Console.
        resend_api_key_secret.grant_read(self.instance_role)

        # ADR-0033: SES SendEmail permission for proactive-
        # notification emails (backfill completion / failure / cap-reached
        # to tenant admins, plus operational emails to the maintainer).
        # Scoped to `*` because SES itself enforces the verified-identity
        # constraint — IAM allowing send doesn't override SES's "you can
        # only send from verified identities" rule. The only verified
        # identity in this account is example.com (verified
        # 2026-05-26 via SES console with Route 53 auto-publish), so the
        # effective send scope is exactly what we want.
        self.instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail"],
                resources=["*"],
                effect=iam.Effect.ALLOW,
            )
        )

        # IAM — access role for App Runner to pull from ECR.
        self.access_role = iam.Role(
            self,
            "AppRunnerAccessRole",
            assumed_by=iam.ServicePrincipal("build.apprunner.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSAppRunnerServicePolicyForECRAccess"
                )
            ],
        )

        # Backend needs the VPC connector to reach RDS via the security group.
        # FORGE_APP_ID — sourced from SSM Parameter Store per env. Public ID,
        # not a secret, but env-scoped. Operator must
        # `aws ssm put-parameter` once per env before first deploy of this
        # stack; CFN errors clearly if the parameter is missing.
        forge_app_id = ssm.StringParameter.value_for_string_parameter(
            self, f"/flow-intelligence/{cfg.name}/forge_app_id"
        )

        backend_env: dict[str, str] = {
            "FORGE_APP_ID": forge_app_id,
            # structlog emits JSON in prod for CloudWatch Logs Insights
            # queries + the log-metric filters in ObservabilityStack. Dev
            # leaves this unset so logs stay human-readable in `forge tunnel`
            # and `uvicorn --reload` runs.
            "STRUCTLOG_JSON": "1",
        }
        # ALLOW_DEMO_SEED enables /api/dev/seed-demo so an authenticated
        # install can populate its own tenant with synthetic data (250
        # issues + 5 sprints + designed Review-stage bottleneck). FIT-
        # auth-bound — callers can only seed their own tenant, so no
        # cross-customer blast radius.
        #
        # Per-env toggle now lives in EnvConfig.allow_demo_seed (see
        # _config.py). The legacy `-c allow_demo_seed=1` context override
        # is still supported for one-off "enable on prod without editing
        # config" use cases, but the per-env default is the recommended
        # path for sustained testing periods.
        legacy_override = self.node.try_get_context("allow_demo_seed")
        if cfg.allow_demo_seed or legacy_override:
            backend_env["ALLOW_DEMO_SEED"] = "1"

        # ADR-0040: Resend API key SECRET ARN is exposed to the backend
        # as a plain env var (not an App-Runner-injected secret value).
        # The backend reads the ARN and fetches the actual key via
        # boto3.secretsmanager at startup — supports rotation without
        # an App Runner redeploy and matches the design in
        # `resend_service.py::_ensure_initialized`.
        backend_env["RESEND_API_KEY_SECRET_ARN"] = resend_api_key_secret.secret_arn

        self.backend_service = self._make_service(
            construct_id="Backend",
            ecr_repo=self.backend_repo,
            service_name=f"flow-backend-{cfg.name}",
            port="8000",
            cpu=cfg.app_runner_cpu,
            memory=cfg.app_runner_memory,
            db_endpoint=db.db_instance_endpoint_address,
            db_port=db.db_instance_endpoint_port,
            db_credentials_arn=db_credentials.secret_arn,
            app_secrets_arn=app_secrets.secret_arn,
            extra_env=backend_env,
            use_vpc_connector=True,
        )
        # Phase 2 retired the Next.js front door (ADR-0019); the dashboard is
        # now served by Forge from Atlassian's CDN, calling this backend
        # directly via @forge/bridge requestRemote.
        CfnOutput(self, "BackendUrl", value=f"https://{self.backend_service.attr_service_url}")

    def _make_service(
        self,
        *,
        construct_id: str,
        ecr_repo: ecr.IRepository,
        service_name: str,
        port: str,
        cpu: str,
        memory: str,
        db_endpoint: str,
        db_port: str,
        db_credentials_arn: str,
        app_secrets_arn: str,
        extra_env: dict[str, str],
        use_vpc_connector: bool,
    ) -> apprunner.CfnService:
        # App Runner doesn't do shell-style env var expansion, so we can't
        # compose DATABASE_URL at deploy time from the injected secret values.
        # Pass the parts as separate env vars / secrets; the app composes the
        # URL itself in Settings._compose_database_url. See ADR-0017 followup.
        env_vars = [
            apprunner.CfnService.KeyValuePairProperty(name=k, value=v) for k, v in extra_env.items()
        ]
        env_vars.extend(
            [
                apprunner.CfnService.KeyValuePairProperty(
                    name="DATABASE_HOST",
                    value=db_endpoint,
                ),
                apprunner.CfnService.KeyValuePairProperty(
                    name="DATABASE_PORT",
                    value=db_port,
                ),
                apprunner.CfnService.KeyValuePairProperty(
                    name="DATABASE_NAME",
                    value="flow",
                ),
            ]
        )
        env_secrets = [
            apprunner.CfnService.KeyValuePairProperty(
                name="DATABASE_PASSWORD",
                value=f"{db_credentials_arn}:password::",
            ),
            apprunner.CfnService.KeyValuePairProperty(
                name="DATABASE_USER",
                value=f"{db_credentials_arn}:username::",
            ),
            apprunner.CfnService.KeyValuePairProperty(
                name="ANTHROPIC_API_KEY",
                value=f"{app_secrets_arn}:anthropic_api_key::",
            ),
        ]
        return apprunner.CfnService(
            self,
            construct_id,
            service_name=service_name,
            source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                authentication_configuration=apprunner.CfnService.AuthenticationConfigurationProperty(
                    access_role_arn=self.access_role.role_arn,
                ),
                auto_deployments_enabled=False,
                image_repository=apprunner.CfnService.ImageRepositoryProperty(
                    image_identifier=f"{ecr_repo.repository_uri}:{self.image_tag}",
                    image_repository_type="ECR",
                    image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                        port=port,
                        runtime_environment_variables=env_vars,
                        runtime_environment_secrets=env_secrets,
                    ),
                ),
            ),
            instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                cpu=cpu,
                memory=memory,
                instance_role_arn=self.instance_role.role_arn,
            ),
            network_configuration=apprunner.CfnService.NetworkConfigurationProperty(
                egress_configuration=(
                    apprunner.CfnService.EgressConfigurationProperty(
                        egress_type="VPC",
                        vpc_connector_arn=self.vpc_connector.attr_vpc_connector_arn,
                    )
                    if use_vpc_connector
                    else apprunner.CfnService.EgressConfigurationProperty(
                        egress_type="DEFAULT",
                    )
                ),
            ),
            health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                protocol="HTTP",
                path="/healthz",
                interval=10,
                timeout=5,
                healthy_threshold=1,
                unhealthy_threshold=3,
            ),
        )
