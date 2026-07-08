from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # `database_url` is the canonical connection string the app uses. In dev
    # it stays at the SQLite default. In production (App Runner / RDS), it's
    # composed below from the parts injected as separate env vars (the user
    # and password come from Secrets Manager, host/port from plain vars).
    # App Runner doesn't do shell-style ${VAR} expansion, so we can't build
    # the URL at deploy time — we compose at app startup.
    database_url: str = "sqlite:///backend/data/flow.db"

    database_user: str = ""
    database_password: str = ""
    database_host: str = ""
    database_port: int = 5432
    database_name: str = "flow"

    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_jql: str = "ORDER BY updated DESC"
    jira_page_size: int = 100
    jira_max_retries: int = 5
    jira_backoff_base_seconds: float = 1.0

    active_statuses: list[str] = ["In Progress", "Review"]
    done_statuses: list[str] = ["Done", "Closed", "Resolved"]
    # Statuses that represent workflow endpoints — work that left the
    # pipeline whether or not it shipped. The CFD excludes these so the
    # chart shows *flow*, not the perpetually-growing pile of finished/
    # rejected tickets. Per-tenant override on the `tenants` row.
    terminal_statuses: list[str] = [
        "Done",
        "Closed",
        "Resolved",
        "Won't Do",
        "Wontfix",
        "Cancelled",
        "Canceled",
        "Rejected",
        "Duplicate",
    ]
    # ADR-0042: opt-in default. Empty list preserves current behavior;
    # a tenant configures their own external-blocking statuses via Settings.
    external_blocking_statuses: list[str] = []

    bottleneck_time_ratio_threshold: float = 1.3
    bottleneck_time_ratio_extra_threshold: float = 1.5
    bottleneck_wip_ratio_threshold: float = 1.2
    bottleneck_throughput_delta_threshold: float = -0.2
    bottleneck_min_score: int = 3

    trend_increase_threshold: float = 1.2
    trend_decrease_threshold: float = 0.8

    default_window_days: int = 7

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # ----- AWS region -----
    # Region for the boto3 Secrets Manager client that resend_service uses
    # to fetch the Resend API key. Credentials come from the standard AWS
    # SDK credential chain (App Runner instance role in prod); no key lives
    # in this config. Set via AWS_REGION.
    aws_region: str = "us-east-1"

    # ----- Resend (ADR-0040) — customer-facing transactional email -----
    # Resend handles the customer-facing email paths SES couldn't serve
    # after the 2026-05-28 production-access denial: alert delivery,
    # backfill notifications, and the 24h failure digest.
    #
    # The API key itself is NOT in this config — `resend_service.py` fetches
    # it from AWS Secrets Manager at startup using the secret ARN exposed via
    # the RESEND_API_KEY_SECRET_ARN env var (provisioned by CDK
    # compute_stack.py + data_stack.py). Settings below cover the operational
    # toggles + sender identity defaults.
    resend_enabled: bool = True
    # Sender identity — every address below must resolve via a domain you have
    # verified in Resend. Replace the example.com defaults with your own
    # verified domain. Set via RESEND_FROM_ADDRESS / RESEND_REPLY_TO /
    # ALERT_FROM_ADDRESS.
    resend_from_address: str = "notifications@example.com"
    # Reply-to header + the support address shown in customer email bodies.
    resend_reply_to: str = "support@example.com"
    # From-address for alert-delivery emails (ADR-0037). A display name is
    # allowed, e.g. "Name <alerts@your-domain.com>".
    alert_from_address: str = "Jira Flow Intelligence Alerts <alerts@example.com>"

    # ----- Atlassian Connect descriptor -----
    # `app_base_url` is the public HTTPS URL Atlassian uses to reach us.
    # Per environment: dev/staging/prod each get their own. Local dev usually
    # uses a tunnel (ngrok / Cloudflare Tunnel) — see runbook.
    app_name: str = "Jira Flow Intelligence"
    app_description: str = (
        "Deterministic flow metrics, multi-signal bottleneck detection, "
        "and threshold/trend alerts from Jira changelogs."
    )
    app_vendor_name: str = "Example"
    app_vendor_url: str = "https://example.com"
    app_base_url: str = "http://localhost:8000"
    app_scopes: list[str] = ["READ"]

    # JWT clock skew tolerance for Atlassian webhook validation.
    jwt_leeway_seconds: int = 30
    # Max age for `iat` to defend against replay (Atlassian guidance: 3 minutes).
    jwt_max_age_seconds: int = 180

    # ----- Forge -----
    # Audience claim every Forge Invocation Token must carry. Set per env via
    # SSM parameter -> App Runner env var (ADR-0019).
    # Empty value disables Forge auth entirely — mid-migration default; once
    # Connect is retired, this becomes required at startup.
    forge_app_id: str = ""

    # When True, exposes /api/dev/seed-demo so a Forge install can populate
    # its own tenant with synthetic data for manual UI exercise. Set on the
    # dev backend env only; never on prod.
    allow_demo_seed: bool = False

    @model_validator(mode="after")
    def _compose_database_url(self) -> "Settings":
        # If DATABASE_URL is still the SQLite default AND a host + user have
        # been injected (typical App Runner / Secrets Manager scenario),
        # build the Postgres URL from parts. URL-encode the password — RDS
        # generates passwords containing /, +, =, etc.
        if self.database_url.startswith("sqlite") and self.database_host and self.database_user:
            encoded_pw = quote_plus(self.database_password)
            self.database_url = (
                f"postgresql+psycopg://{self.database_user}:{encoded_pw}"
                f"@{self.database_host}:{self.database_port}/{self.database_name}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
