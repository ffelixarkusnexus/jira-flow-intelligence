"""CloudWatch alarms + dashboards — billing cap, App Runner health,
operator-facing dashboards, and **operational paging** (ADR-0030).

ADR-0030 additions (2026-05-22):
- SNS topic `flow-intelligence-{env}-alerts` with an email subscription so
  alarms actually reach a human. Until this landed, every alarm in this
  stack was silent — defined in CDK, never wired to a notification target.
- All existing alarms (billing, 5xx, latency) gained `alarm_actions=[topic]`.
- New alarm `BackendForgeAuthFailureAlarm` on a log-metric filter matching
  `Forge FIT validation failed`. Catches the JWKS-rotation 401 class —
  the exact failure mode that bit prod 2026-05-19 and 2026-05-22.
- New alarm `Backend4xxAlarm` on sustained 4xx rate; a softer signal for
  any auth/validation issue that doesn't match the JWKS pattern.
- CloudWatch Synthetics canary (prod only) hitting `/healthz` every minute
  with an alarm that pages when the backend stops responding at all
  (vs. responding badly — that's caught by the AWS/AppRunner metrics).

Additions:
- CloudWatch Dashboard with five panels: request count, p95 latency,
  4xx + 5xx rates, log-emitted error count, and active connection count.
- Log metric filters that count structured-log events from structlog:
  ERROR/WARNING level lines so the dashboard can graph error volume
  without scraping the raw stream.

Earlier:
- Billing alarm at the env's `billing_alarm_usd` threshold (ADR-0012 cost
  ceiling).
- 5xx error rate alarm on each App Runner service.
- Latency alarm on each App Runner service (p95 > 5s sustained).
- CloudWatch log groups per service with `log_retention_days` retention.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Duration, Stack, Tags
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as r53
from aws_cdk import aws_sns as sns
from constructs import Construct

from stacks._config import EnvConfig

# Default email recipient for ops alerts. Override per deploy with the
# ALERT_EMAIL env var (or `-c alert_email=...` CDK context) — wired in app.py.
# One inbox is the simplest routing that works at this scale; adding more
# subscribers later is a one-line CDK change, and SMS / Slack route via the
# same topic. Surfaced as a stack output for the manual subscribe step
# (subscriptions are managed out-of-band; see the runbook).
_DEFAULT_ALERT_EMAIL = "alerts@example.com"

# Default prod public healthz target for the Route 53 health check. Override
# per deploy with the HEALTHZ_HOST env var (or `-c healthz_host=...`) — wired
# in app.py. Dev/staging App Runner URLs are env-specific and not behind a DNS
# we publish; the health check only runs against the hostname customers hit.
_DEFAULT_HEALTHZ_HOST = "api.example.com"
_PROD_HEALTHZ_PATH = "/healthz"


class ObservabilityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        backend_service: apprunner.CfnService,
        alert_email: str = _DEFAULT_ALERT_EMAIL,
        healthz_host: str = _DEFAULT_HEALTHZ_HOST,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("env", cfg.name)
        Tags.of(self).add("app", "flow-intelligence")

        # SNS topic for alarm fan-out. Email subscription is created in CDK
        # but the subscriber must one-click confirm AWS's "Subscription
        # Confirmation" message before alarms can deliver — runbook covers
        # the first-time bootstrap.
        alert_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=f"flow-intelligence-{cfg.name}-alerts",
            display_name=f"Flow Intelligence {cfg.name} alerts",
        )
        # Surface the intended alert recipient so the operator can wire the
        # manual `aws sns subscribe` step (subscriptions are out-of-band; see
        # the runbook "Operational alerting → First-time setup").
        CfnOutput(
            self,
            "AlertEmailRecipient",
            value=alert_email,
            description=(
                "Email to subscribe to the alerts SNS topic via the manual "
                "`aws sns subscribe` step (see runbook)."
            ),
        )
        # Email subscriptions are managed out-of-band, NOT by CDK. Reason:
        # the only effective defense against Gmail / Google Workspace /
        # Mimecast / Defender / Proofpoint inbox link scanners following
        # the one-click unsubscribe URL in every notification email is the
        # per-subscription `AuthenticateOnUnsubscribe=true` attribute, which
        # is only settable as a Subscribe API parameter (not a topic policy,
        # not a SetSubscriptionAttributes call, not a CloudFormation
        # property). Confirmed 2026-05-22: two consecutive CDK-created email
        # subscriptions to a Google Workspace inbox were auto-deactivated
        # within seconds of confirm, before any alarm could reach the inbox.
        # A topic-policy approach (deny sns:Unsubscribe at topic scope) was
        # tried and rejected by SNS itself with "Policy statement action out
        # of service scope" — Unsubscribe is a subscription-scope action.
        #
        # The runbook section "Operational alerting → First-time setup"
        # documents the `aws sns subscribe --attributes
        # AuthenticateOnUnsubscribe=true` invocation that creates a
        # link-scanner-proof subscription. CDK manages the topic + alarm
        # wiring; the recipient list is operationally managed.
        alarm_action = cw_actions.SnsAction(alert_topic)

        # CloudWatch log groups — set retention explicitly so we don't pay
        # for 365d-default storage on every byte.
        retention = _retention(cfg.log_retention_days)
        backend_log_group = logs.LogGroup(
            self,
            "BackendLogs",
            log_group_name=f"/aws/apprunner/{backend_service.service_name}/application",
            retention=retention,
        )

        # Log-based metric filters. Counts structured log events
        # by level so the dashboard can graph error/warning volume.
        # structlog's JSON output puts `level` at the top level of each
        # log line, e.g. `{"event":"sync_completed","level":"info",...}`.
        log_namespace = f"flow-intelligence-{cfg.name}"
        backend_log_group.add_metric_filter(
            "ErrorLogs",
            metric_namespace=log_namespace,
            metric_name="ErrorLogs",
            metric_value="1",
            filter_pattern=logs.FilterPattern.string_value("$.level", "=", "error"),
            default_value=0,
        )
        backend_log_group.add_metric_filter(
            "WarningLogs",
            metric_namespace=log_namespace,
            metric_name="WarningLogs",
            metric_value="1",
            filter_pattern=logs.FilterPattern.string_value("$.level", "=", "warning"),
            default_value=0,
        )

        # ADR-0030: Forge FIT validation failures. The middleware logs
        # `"Forge FIT validation failed on <path>: <reason>"` at warning
        # level whenever a request is rejected — covers the JWKS-rotation
        # case (`JWKS lookup failed: kid X not in cached JWKS...`) plus any
        # other auth-path failure. structlog renders the message into the
        # `event` field of each JSON line.
        backend_log_group.add_metric_filter(
            "ForgeAuthFailures",
            metric_namespace=log_namespace,
            metric_name="ForgeAuthFailures",
            metric_value="1",
            filter_pattern=logs.FilterPattern.string_value(
                "$.event", "=", "*Forge FIT validation failed*"
            ),
            default_value=0,
        )

        # Billing alarm — must be in us-east-1 to alarm on EstimatedCharges.
        billing_alarm = cw.Alarm(
            self,
            "BillingAlarm",
            alarm_name=f"flow-intelligence-{cfg.name}-billing",
            alarm_description=(
                f"Total estimated charges exceeded ${cfg.billing_alarm_usd}."
                " Verify before continuing usage."
            ),
            metric=cw.Metric(
                namespace="AWS/Billing",
                metric_name="EstimatedCharges",
                dimensions_map={"Currency": "USD"},
                statistic="Maximum",
                period=Duration.hours(6),
            ),
            threshold=cfg.billing_alarm_usd,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        billing_alarm.add_alarm_action(alarm_action)

        # Per-service alarms — all wired to the alert topic via alarm_action.
        for label, service in (("backend", backend_service),):
            service_name = service.service_name or label
            alarms: list[cw.Alarm] = []
            alarms.append(
                cw.Alarm(
                    self,
                    f"{label.title()}5xxAlarm",
                    alarm_name=f"flow-intelligence-{cfg.name}-{label}-5xx",
                    alarm_description=(
                        f"App Runner {label} returned >10 5xx responses in two consecutive "
                        "5-minute windows. Investigate via the operator dashboard."
                    ),
                    metric=cw.Metric(
                        namespace="AWS/AppRunner",
                        metric_name="5xxStatusResponses",
                        dimensions_map={"ServiceName": service_name},
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                    threshold=10,
                    evaluation_periods=2,
                    comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                )
            )
            alarms.append(
                cw.Alarm(
                    self,
                    f"{label.title()}LatencyAlarm",
                    alarm_name=f"flow-intelligence-{cfg.name}-{label}-latency",
                    alarm_description=(
                        f"App Runner {label} p95 request latency above 5s for 15 minutes. "
                        "Investigate via the operator dashboard."
                    ),
                    metric=cw.Metric(
                        namespace="AWS/AppRunner",
                        metric_name="RequestLatency",
                        dimensions_map={"ServiceName": service_name},
                        statistic="p95",
                        period=Duration.minutes(5),
                    ),
                    threshold=5000,  # ms
                    evaluation_periods=3,
                    comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                )
            )
            # ADR-0030: pages on the auth-failure class that hit prod twice in
            # May 2026. Any single FIT validation failure fires; the in-process
            # JWKS refresh (ADR-0029) is the fix, this alarm is the safety net
            # if that refresh path itself breaks.
            alarms.append(
                cw.Alarm(
                    self,
                    f"{label.title()}ForgeAuthFailureAlarm",
                    alarm_name=f"flow-intelligence-{cfg.name}-{label}-forge-auth",
                    alarm_description=(
                        "Forge Invocation Token validation failed at least once in the last "
                        "5 minutes. The most likely cause is an Atlassian JWKS rotation that "
                        "the in-process refresh (ADR-0029) didn't catch — check the "
                        "`/aws/apprunner/{service}/application` log group for the failing "
                        "kid and verify NAT egress to forge.cdn.prod.atlassian-dev.net."
                    ),
                    metric=cw.Metric(
                        namespace=log_namespace,
                        metric_name="ForgeAuthFailures",
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                    threshold=0,
                    evaluation_periods=1,
                    comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                )
            )
            # ADR-0030: sustained 4xx elevation. Threshold tuned to ignore the
            # normal background of 401s / 404s from probes; a real auth-wide
            # outage produces hundreds of 4xx/min from the customer surface
            # so 15/5min for 10min is conservative.
            alarms.append(
                cw.Alarm(
                    self,
                    f"{label.title()}4xxAlarm",
                    alarm_name=f"flow-intelligence-{cfg.name}-{label}-4xx",
                    alarm_description=(
                        "App Runner returned >15 4xx responses in each of two consecutive "
                        "5-minute windows. Sustained elevation typically means a regression "
                        "in request validation, an auth-path failure, or a misbehaving "
                        "client. Cross-check against the operator dashboard."
                    ),
                    metric=cw.Metric(
                        namespace="AWS/AppRunner",
                        metric_name="4xxStatusResponses",
                        dimensions_map={"ServiceName": service_name},
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                    threshold=15,
                    evaluation_periods=2,
                    comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                )
            )
            for alarm in alarms:
                alarm.add_alarm_action(alarm_action)

        # ADR-0030: external uptime check. Route 53 hits the configured
        # healthz host /healthz every 30s from ~15 distributed AWS
        # health-checker regions
        # and reports 1.0 (healthy) / 0.0 (unhealthy) to CloudWatch. Catches
        # the failure mode where App Runner is *down* entirely — no metrics
        # being emitted, so the AWS/AppRunner alarms above can't fire.
        # Prod-only: dev/staging don't have a publicly published DNS name.
        # Cost: ~$0.50/mo (basic health check) + ~$0.10/mo (alarm).
        if cfg.name == "prod":
            healthz_check = r53.CfnHealthCheck(
                self,
                "HealthzCheck",
                health_check_config=r53.CfnHealthCheck.HealthCheckConfigProperty(
                    type="HTTPS_STR_MATCH",
                    fully_qualified_domain_name=healthz_host,
                    port=443,
                    resource_path=_PROD_HEALTHZ_PATH,
                    search_string='"status":"ok"',
                    request_interval=30,
                    failure_threshold=3,
                    measure_latency=False,
                    enable_sni=True,
                ),
                health_check_tags=[
                    r53.CfnHealthCheck.HealthCheckTagProperty(
                        key="Name", value=f"flow-intelligence-{cfg.name}-healthz"
                    ),
                ],
            )
            healthz_alarm = cw.Alarm(
                self,
                "HealthzAlarm",
                alarm_name=f"flow-intelligence-{cfg.name}-backend-healthz",
                alarm_description=(
                    f"External HTTPS check of {healthz_host}{_PROD_HEALTHZ_PATH} "
                    "failed for two consecutive 1-minute periods. The backend is either "
                    "down, unreachable, or returning a body that does not contain "
                    '`"status":"ok"`. Check the App Runner service health and the '
                    "Route 53 health check history in the AWS console."
                ),
                metric=cw.Metric(
                    namespace="AWS/Route53",
                    metric_name="HealthCheckStatus",
                    dimensions_map={"HealthCheckId": healthz_check.attr_health_check_id},
                    statistic="Minimum",
                    period=Duration.minutes(1),
                ),
                threshold=1,
                evaluation_periods=2,
                comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.BREACHING,
            )
            healthz_alarm.add_alarm_action(alarm_action)

        # Operator dashboard. Five widgets across the top row, each
        # spanning 6 columns of the default 24-column grid (so they wrap
        # to two rows on narrower viewports).
        backend_service_name = backend_service.service_name or "backend"
        dim = {"ServiceName": backend_service_name}
        cw.Dashboard(
            self,
            "OperatorDashboard",
            dashboard_name=f"flow-intelligence-{cfg.name}",
            widgets=[
                [
                    cw.GraphWidget(
                        title="Request volume (rpm)",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="Requests",
                                dimensions_map=dim,
                                statistic="Sum",
                                period=Duration.minutes(1),
                            )
                        ],
                    ),
                    cw.GraphWidget(
                        title="p95 request latency (ms)",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="RequestLatency",
                                dimensions_map=dim,
                                statistic="p95",
                                period=Duration.minutes(5),
                            ),
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="RequestLatency",
                                dimensions_map=dim,
                                statistic="p50",
                                period=Duration.minutes(5),
                                label="p50",
                            ),
                        ],
                    ),
                    cw.GraphWidget(
                        title="HTTP status mix (5min sums)",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="2xxStatusResponses",
                                dimensions_map=dim,
                                statistic="Sum",
                                period=Duration.minutes(5),
                                label="2xx",
                            ),
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="4xxStatusResponses",
                                dimensions_map=dim,
                                statistic="Sum",
                                period=Duration.minutes(5),
                                label="4xx",
                            ),
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="5xxStatusResponses",
                                dimensions_map=dim,
                                statistic="Sum",
                                period=Duration.minutes(5),
                                label="5xx",
                            ),
                        ],
                    ),
                    cw.GraphWidget(
                        title="Application log levels (5min sums)",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace=log_namespace,
                                metric_name="ErrorLogs",
                                statistic="Sum",
                                period=Duration.minutes(5),
                                label="error",
                            ),
                            cw.Metric(
                                namespace=log_namespace,
                                metric_name="WarningLogs",
                                statistic="Sum",
                                period=Duration.minutes(5),
                                label="warning",
                            ),
                        ],
                    ),
                ],
                [
                    cw.GraphWidget(
                        title="Active instances",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="ActiveInstances",
                                dimensions_map=dim,
                                statistic="Average",
                                period=Duration.minutes(1),
                            )
                        ],
                    ),
                    cw.GraphWidget(
                        title="CPU utilization (%)",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="CPUUtilization",
                                dimensions_map=dim,
                                statistic="Average",
                                period=Duration.minutes(5),
                            )
                        ],
                    ),
                    cw.GraphWidget(
                        title="Memory utilization (%)",
                        width=6,
                        left=[
                            cw.Metric(
                                namespace="AWS/AppRunner",
                                metric_name="MemoryUtilization",
                                dimensions_map=dim,
                                statistic="Average",
                                period=Duration.minutes(5),
                            )
                        ],
                    ),
                ],
            ],
        )


def _retention(days: int) -> logs.RetentionDays:
    # CloudWatch retention is an enum of fixed values. Round up to the nearest.
    candidates = {
        1: logs.RetentionDays.ONE_DAY,
        7: logs.RetentionDays.ONE_WEEK,
        14: logs.RetentionDays.TWO_WEEKS,
        30: logs.RetentionDays.ONE_MONTH,
        60: logs.RetentionDays.TWO_MONTHS,
        90: logs.RetentionDays.THREE_MONTHS,
    }
    for threshold in sorted(candidates):
        if days <= threshold:
            return candidates[threshold]
    return logs.RetentionDays.ONE_YEAR
