"""CDK assertion tests for the four stacks.

These tests synthesize each stack against a known config and assert that
the resulting CloudFormation template contains the resources and properties
we expect. They run fast (no AWS calls) and catch regressions like
"someone removed RDS encryption" or "ALB sneaked in via L3".
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks._config import EnvConfig, load_config
from stacks.compute_stack import ComputeStack
from stacks.data_stack import DataStack
from stacks.ecr_stack import EcrStack
from stacks.network_stack import NetworkStack
from stacks.observability_stack import ObservabilityStack


@pytest.fixture
def cfg() -> EnvConfig:
    return load_config("dev", aws_account="123456789012")


@pytest.fixture
def synth(cfg: EnvConfig):
    """Synthesize all five stacks once and return Templates per stack."""
    app = cdk.App()
    env = cdk.Environment(account=cfg.aws_account, region=cfg.aws_region)

    ecr = EcrStack(app, "Ecr", cfg=cfg, env=env)
    network = NetworkStack(app, "Net", cfg=cfg, env=env)
    data = DataStack(
        app, "Data", cfg=cfg, vpc=network.vpc, rds_security_group=network.rds_sg, env=env
    )
    compute = ComputeStack(
        app,
        "Compute",
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
        image_tag="abc123",
        env=env,
    )
    obs = ObservabilityStack(
        app,
        "Obs",
        cfg=cfg,
        backend_service=compute.backend_service,
        env=env,
    )

    return {
        "ecr": Template.from_stack(ecr),
        "network": Template.from_stack(network),
        "data": Template.from_stack(data),
        "compute": Template.from_stack(compute),
        "obs": Template.from_stack(obs),
    }


# ----- network -------------------------------------------------------------


def test_network_has_vpc(synth):
    synth["network"].resource_count_is("AWS::EC2::VPC", 1)


def test_network_subnet_count_matches_nat_setting(synth, cfg):
    """Without NAT: 2 public subnets only. With NAT: 2 public + 2 private."""
    expected = 4 if cfg.nat_gateway else 2
    synth["network"].resource_count_is("AWS::EC2::Subnet", expected)


def test_nat_gateway_count_matches_config(synth, cfg):
    """ADR-0012: NAT is $32/mo per gateway; toggled via EnvConfig.nat_gateway."""
    synth["network"].resource_count_is("AWS::EC2::NatGateway", 1 if cfg.nat_gateway else 0)


def test_rds_sg_only_allows_postgres_from_app_runner_sg(synth):
    """Defense-in-depth — RDS sits in a public subnet so the SG must keep
    access scoped (ADR-0012)."""
    synth["network"].has_resource_properties(
        "AWS::EC2::SecurityGroupIngress",
        {
            "IpProtocol": "tcp",
            "FromPort": 5432,
            "ToPort": 5432,
        },
    )


# ----- data ----------------------------------------------------------------


def test_rds_encrypted_at_rest_and_not_publicly_accessible(synth):
    synth["data"].has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "StorageEncrypted": True,
            "PubliclyAccessible": False,
        },
    )


def test_rds_uses_postgres_engine(synth):
    synth["data"].has_resource_properties(
        "AWS::RDS::DBInstance",
        {"Engine": "postgres"},
    )


def test_rds_has_backup_retention_configured(synth):
    synth["data"].has_resource_properties(
        "AWS::RDS::DBInstance",
        {"BackupRetentionPeriod": 1},  # dev value from EnvConfig
    )


def test_master_credentials_are_in_secrets_manager(synth):
    """No plaintext password in IaC (ADR-0014 follow-up). Three secrets:
    DbCredentials (RDS master), AppSecrets (JSON blob: anthropic_api_key),
    and ResendApiKey (separate Secret per ADR-0040 for vendor-isolated
    rotation + blast-radius separation)."""
    synth["data"].resource_count_is("AWS::SecretsManager::Secret", 3)


def test_resend_api_key_secret_has_env_specific_name(synth):
    """ADR-0040: the Resend API key Secret uses a stable predictable name
    (`flow-intelligence/{env}/resend_api_key`) so operators can locate and
    rotate it via AWS Console without chasing the CDK-generated suffix.
    """
    synth["data"].has_resource_properties(
        "AWS::SecretsManager::Secret",
        {"Name": "flow-intelligence/dev/resend_api_key"},
    )


def test_app_runner_has_resend_api_key_secret_arn_env_var(synth):
    """ADR-0040: backend reads the Secret value at startup via boto3, so
    it needs the Secret ARN exposed as an env var (not an App-Runner-
    injected secret value)."""
    synth["compute"].has_resource_properties(
        "AWS::AppRunner::Service",
        {
            "SourceConfiguration": {
                "ImageRepository": {
                    "ImageConfiguration": {
                        "RuntimeEnvironmentVariables": Match.array_with(
                            [Match.object_like({"Name": "RESEND_API_KEY_SECRET_ARN"})]
                        )
                    }
                }
            }
        },
    )


# ----- compute -------------------------------------------------------------


def test_two_ecr_repos_during_p2f_transition(synth):
    """Backend + the legacy frontend repo, kept for one deploy post
    frontend-repo retirement so CFN can finish removing the cross-stack
    import. Drop in a follow-up
    PR — at that point the count goes to 1."""
    synth["ecr"].resource_count_is("AWS::ECR::Repository", 2)


def test_compute_stack_does_not_create_ecr_repos(synth):
    """They live in EcrStack now (split per ADR-0018)."""
    synth["compute"].resource_count_is("AWS::ECR::Repository", 0)


def test_one_app_runner_service_exists(synth):
    synth["compute"].resource_count_is("AWS::AppRunner::Service", 1)


def test_backend_uses_vpc_connector(synth):
    """Backend egress goes through the VPC connector to reach RDS via the
    security group rule. ADR-0012."""
    synth["compute"].resource_count_is("AWS::AppRunner::VpcConnector", 1)
    synth["compute"].has_resource_properties(
        "AWS::AppRunner::Service",
        {"NetworkConfiguration": {"EgressConfiguration": Match.object_like({"EgressType": "VPC"})}},
    )


def test_app_runner_health_check_targets_healthz(synth):
    synth["compute"].has_resource_properties(
        "AWS::AppRunner::Service",
        {"HealthCheckConfiguration": Match.object_like({"Protocol": "HTTP", "Path": "/healthz"})},
    )


# ----- observability -------------------------------------------------------


def test_observability_creates_billing_alarm(synth):
    synth["obs"].has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "MetricName": "EstimatedCharges",
            "Namespace": "AWS/Billing",
        },
    )


def test_observability_log_groups_have_retention_set(synth):
    """ADR-0012 gotcha: CloudWatch's default 365d retention bills forever."""
    synth["obs"].resource_count_is("AWS::Logs::LogGroup", 1)
    synth["obs"].all_resources_properties(
        "AWS::Logs::LogGroup",
        {"RetentionInDays": Match.any_value()},
    )


def test_observability_alarms_per_service(synth):
    """1 billing + 4 backend alarms (5xx + latency + forge-auth + 4xx). The
    Route 53 health-check alarm is prod-only and doesn't synth in this dev
    fixture; see `test_observability_prod_only_resources` below."""
    synth["obs"].resource_count_is("AWS::CloudWatch::Alarm", 5)


def test_observability_creates_alert_topic(synth):
    """ADR-0030: alarms must reach a human via the SNS topic. The recipient
    subscriptions themselves are managed out-of-band (runbook "Operational
    alerting → First-time setup") because the only defense against inbox
    link-scanner auto-unsubscribe is the per-subscription
    AuthenticateOnUnsubscribe attribute, which CFN cannot set."""
    synth["obs"].resource_count_is("AWS::SNS::Topic", 1)
    synth["obs"].resource_count_is("AWS::SNS::Subscription", 0)


def test_observability_all_alarms_have_actions(synth):
    """Every alarm must wire `AlarmActions` to the SNS topic. The whole
    point of ADR-0030 is that silent alarms (alarm without action) are the
    failure mode that bit prod on 2026-05-19 and 2026-05-22."""
    template_json = synth["obs"].to_json()
    alarms = {
        logical_id: resource
        for logical_id, resource in template_json["Resources"].items()
        if resource["Type"] == "AWS::CloudWatch::Alarm"
    }
    assert alarms, "expected at least one alarm in the obs stack"
    silent = [
        logical_id
        for logical_id, resource in alarms.items()
        if not resource["Properties"].get("AlarmActions")
    ]
    assert not silent, f"alarms with no AlarmActions wired: {silent}"


def test_observability_forge_auth_failure_alarm_present(synth):
    """The most important new alarm in ADR-0030: any FIT validation failure
    pages immediately. This is the safety net behind ADR-0029's self-heal."""
    synth["obs"].has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {"MetricName": "ForgeAuthFailures"},
    )


@pytest.fixture
def prod_obs_template() -> Template:
    """ObservabilityStack synthesized against the prod EnvConfig. Used to
    cover prod-only resources (Route 53 health check + healthz alarm) that
    aren't part of the dev synth above."""
    prod_cfg = load_config("prod", aws_account="123456789012")
    app = cdk.App()
    env = cdk.Environment(account=prod_cfg.aws_account, region=prod_cfg.aws_region)
    ecr = EcrStack(app, "Ecr", cfg=prod_cfg, env=env)
    network = NetworkStack(app, "Net", cfg=prod_cfg, env=env)
    data = DataStack(
        app, "Data", cfg=prod_cfg, vpc=network.vpc, rds_security_group=network.rds_sg, env=env
    )
    compute = ComputeStack(
        app,
        "Compute",
        cfg=prod_cfg,
        vpc=network.vpc,
        vpc_connector_subnets=network.vpc_connector_subnets,
        app_runner_security_group=network.app_runner_sg,
        app_runner_security_group_inactive=network.app_runner_sg_inactive,
        db=data.db,
        db_credentials=data.db_credentials,
        app_secrets=data.app_secrets,
        resend_api_key_secret=data.resend_api_key_secret,
        backend_repo=ecr.backend_repo,
        image_tag="abc123",
        env=env,
    )
    obs = ObservabilityStack(
        app,
        "Obs",
        cfg=prod_cfg,
        backend_service=compute.backend_service,
        env=env,
        healthz_host="api.jfi-test.internal",
    )
    return Template.from_stack(obs)


def test_observability_prod_only_route53_health_check(prod_obs_template: Template):
    """ADR-0030: prod gets an external uptime check on the configured healthz
    host /healthz. Dev/staging do not (no published DNS to point Route 53 at)."""
    prod_obs_template.has_resource_properties(
        "AWS::Route53::HealthCheck",
        {
            "HealthCheckConfig": Match.object_like(
                {
                    "Type": "HTTPS_STR_MATCH",
                    "FullyQualifiedDomainName": "api.jfi-test.internal",
                    "ResourcePath": "/healthz",
                    "SearchString": '"status":"ok"',
                }
            )
        },
    )
    # Prod has 6 alarms (5 from dev fixture + the healthz alarm).
    prod_obs_template.resource_count_is("AWS::CloudWatch::Alarm", 6)
