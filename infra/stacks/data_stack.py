"""RDS Postgres + Secrets Manager.

Phase 1 RDS shape (ADR-0012):
- db.t4g.micro Single-AZ, KMS-at-rest, automated backups (1d dev / 7d
  staging / 14d prod). publicly_accessible=False — even though the
  instance is in a public subnet, AWS won't assign a public DNS name
  and the security group restricts ingress to the App Runner SG.
- Master credentials live in Secrets Manager with rotation disabled in
  Phase 1 (rotation requires a Lambda inside the VPC; deferred).
- IAM database authentication is enabled for future use.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import RemovalPolicy, Stack, Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from stacks._config import EnvConfig


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        vpc: ec2.IVpc,
        rds_security_group: ec2.ISecurityGroup,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("env", cfg.name)
        Tags.of(self).add("app", "flow-intelligence")

        # Master credentials. We let RDS generate the password and store it
        # in Secrets Manager — the value is then injected into App Runner
        # via the secret ARN, so no plaintext password ever lives in IaC.
        self.db_credentials = rds.DatabaseSecret(
            self,
            "DbCredentials",
            username="flow_admin",
        )

        instance_class, instance_size = cfg.rds_instance_class.removeprefix("db.").split(".")
        self.db = rds.DatabaseInstance(
            self,
            "Db",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_13,
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass[instance_class.upper()],
                ec2.InstanceSize[instance_size.upper()],
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[rds_security_group],
            credentials=rds.Credentials.from_secret(self.db_credentials),
            allocated_storage=cfg.rds_allocated_storage_gb,
            multi_az=cfg.rds_multi_az,
            backup_retention=_days(cfg.rds_backup_retention_days),
            storage_encrypted=True,
            publicly_accessible=False,
            iam_authentication=True,
            removal_policy=(RemovalPolicy.DESTROY if cfg.name == "dev" else RemovalPolicy.SNAPSHOT),
            deletion_protection=cfg.name == "prod",
            database_name="flow",
        )

        # The Atlassian shared-secret-per-tenant lives in the application
        # database (per ADR-0014/ADR-0015). Phase 2 will move secrets that
        # belong to the platform itself (Anthropic API key, etc.) here.
        self.app_secrets = sm.Secret(
            self,
            "AppSecrets",
            description="Application-level secrets (Anthropic API key, etc.)",
            secret_object_value={
                # placeholder — operator updates the value out-of-band
                "anthropic_api_key": _placeholder_string(""),
            },
        )

        # ADR-0040: Resend API key for the customer-facing transactional
        # email service. Separate Secret resource (not co-located in
        # `app_secrets`) for vendor isolation — a single bad write or
        # rotation on app_secrets shouldn't be able to take down email
        # delivery, and vice versa. Per-Secret cost (~$0.40/mo) is trivial;
        # blast-radius separation is worth it.
        #
        # `secret_name` is the human-readable lookup path operators use
        # when writing the value via AWS Console
        # (Secrets Manager → flow-intelligence/{env}/resend_api_key).
        # On first deploy the value is an empty placeholder; the backend
        # fails closed at startup until an operator writes the real key.
        # NOTE on the placeholder: CloudFormation rejects a Secret with both
        # `SecretString` and `GenerateSecretString` set ("Can only specify
        # either SecretString or GenerateSecretString"). CDK's `sm.Secret`
        # treats an empty `secret_string_value` as "no value provided" and
        # falls back to the default `GenerateSecretString: {}`, which then
        # collides with the empty `SecretString` it also emits. The fix is
        # to pass a non-empty placeholder string; CDK then omits the
        # `GenerateSecretString` block entirely. The placeholder is
        # intentionally human-flagged so an operator who reads it via the
        # AWS Console immediately knows it must be replaced. The backend's
        # `_ensure_initialized` also detects this exact marker and raises
        # `ResendConfigError` with a clear message rather than passing the
        # placeholder to Resend (which would 401).
        self.resend_api_key_secret = sm.Secret(
            self,
            "ResendApiKey",
            secret_name=f"flow-intelligence/{cfg.name}/resend_api_key",
            description=(
                "Resend API key for customer-facing transactional email "
                "(ADR-0040). Backend fetches at startup via "
                "RESEND_API_KEY_SECRET_ARN env var. Operator must write the "
                "real key value here before the first deploy succeeds — see "
                "runbook §Transactional email."
            ),
            secret_string_value=_placeholder_string(_RESEND_PLACEHOLDER),
        )


# Sentinel value used as the initial Secret value when CDK provisions the
# Resend API key Secret. The backend recognizes this and refuses to use
# it — operator must overwrite via AWS Console / `put-secret-value` before
# customer-facing email actually works.
_RESEND_PLACEHOLDER = "REPLACE_WITH_REAL_RESEND_API_KEY_VIA_AWS_CONSOLE"


def _days(n: int) -> rds.Duration:  # type: ignore[name-defined]
    from aws_cdk import Duration

    return Duration.days(n)


def _placeholder_string(value: str):
    from aws_cdk import SecretValue

    return SecretValue.unsafe_plain_text(value)
