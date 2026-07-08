"""Resend-based transactional email service (ADR-0040).

Owns the customer-facing email paths AWS SES could no longer serve after
the 2026-05-28 production-access denial:

  1. Alert delivery to customer admin (channel-type = email; ADR-0037)
  2. Backfill completion / failure / cap-reached notifications
     (ADR-0033 + CLAUDE.md rule #9)
  3. 24h alert-delivery-failure digest (ADR-0037)

API key flow:
- CDK provisions a dedicated AWS Secrets Manager Secret at
  /flow-intelligence/{env}/resend_api_key (separate from `app_secrets` per
  ADR-0040 § Decision — vendor isolation > co-location for rotation +
  blast-radius).
- App Runner exposes the secret's ARN as the RESEND_API_KEY_SECRET_ARN
  env var.
- This module fetches the actual key value once at startup via boto3
  (one Secrets Manager call per process), caches it on the resend SDK
  module, and reuses across requests. Fail-closed: a missing or empty
  ARN env var raises `ResendConfigError` at first-call time so the
  problem surfaces immediately rather than silently no-op-ing.

Dry-run:
- RESEND_DRY_RUN=1 short-circuits every send to a logged no-op that
  still returns True. For local dev without credentials; tests use
  `_set_initialized_for_tests` + a mock on `resend.Emails.send`
  instead, which is more deterministic.

Test seam follows the standard mock convention (see
`test_resend_service.py` for the pattern). Tests call
`_set_initialized_for_tests(True)` to
bypass the secret fetch, then patch `resend.Emails.send` for the
specific case under test.
"""

from __future__ import annotations

import os
import time
from typing import cast

import boto3  # type: ignore[import-untyped]
import resend
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Tenant

logger = get_logger(__name__)


# ----- Typed exceptions ----------------------------------------------------


class ResendConfigError(RuntimeError):
    """Raised when Resend can't be initialized (missing secret ARN, secret
    fetch failure, empty secret value). Distinct from ResendDeliveryError so
    callers can tell startup-config bugs from per-send transport problems."""


class ResendDeliveryError(RuntimeError):
    """Raised on a 4xx/5xx response from Resend. The wrapped exception
    carries the Resend error code + message when available. Used internally;
    callers see `_send_email` returning False + a logged warning, matching
    the SES service's behavior so the migration is a one-line swap."""


# ----- SDK initialization (lazy, once per process) -------------------------


_initialized = False


def _ensure_initialized() -> None:
    """Fetch the Resend API key from Secrets Manager once and configure the
    SDK module-level api_key. Subsequent calls no-op via the `_initialized`
    flag.

    Reads the secret ARN from RESEND_API_KEY_SECRET_ARN. Raises
    ResendConfigError if the env var is missing/empty or the secret fetch
    fails — fail-closed so misconfiguration surfaces at send time, not
    silently as zero customer emails.
    """
    global _initialized
    if _initialized:
        return

    arn = os.environ.get("RESEND_API_KEY_SECRET_ARN", "").strip()
    if not arn:
        raise ResendConfigError(
            "RESEND_API_KEY_SECRET_ARN env var is not set. CDK provisions "
            "this in compute_stack.py — verify the latest deploy ran and the "
            "ResendApiKey Secret has been populated with the actual key value."
        )

    try:
        settings = get_settings()
        client = boto3.client("secretsmanager", region_name=settings.aws_region)
        resp = client.get_secret_value(SecretId=arn)
    except (ClientError, BotoCoreError) as exc:
        raise ResendConfigError(
            f"Failed to fetch Resend API key from Secrets Manager: {exc}"
        ) from exc

    value = (resp.get("SecretString") or "").strip()
    if not value:
        raise ResendConfigError(
            "Resend API key secret is empty. Operator must write the key "
            "value into the Secret via AWS Console — see runbook "
            "§Transactional email."
        )
    # The CDK-provisioned Secret ships with a self-documenting placeholder
    # value (see infra/stacks/data_stack.py:_RESEND_PLACEHOLDER). Detect it
    # explicitly — passing the placeholder to Resend would 401 with a less
    # actionable error, and a silent "looks like an API key" check has been
    # the source of subtle deploys-but-doesn't-actually-work bugs in this
    # project before.
    if value == "REPLACE_WITH_REAL_RESEND_API_KEY_VIA_AWS_CONSOLE":
        raise ResendConfigError(
            "Resend API key secret still holds the CDK-provisioned "
            "placeholder. Operator must overwrite it with the real key from "
            "resend.com/api-keys — see runbook §Transactional email."
        )

    resend.api_key = value
    _initialized = True
    logger.info("resend_initialized")


def _set_initialized_for_tests(initialized: bool) -> None:
    """Test seam — let tests bypass the Secrets-Manager fetch by marking the
    module as already-initialized. Tests then patch `resend.Emails.send`
    directly for the specific case under test."""
    global _initialized
    _initialized = initialized


# ----- Display helpers ------------------------------------------------------


def _first_name_from_email(email: str | None) -> str:
    """`casey.lee@example.com` → "Casey". Empty → "there"."""
    if not email:
        return "there"
    local = email.split("@", 1)[0]
    for sep in (".", "_", "-", "+"):
        if sep in local:
            local = local.split(sep, 1)[0]
            break
    return local.capitalize() if local else "there"


def _format_count(n: int | None) -> str:
    if n is None:
        return "0"
    return f"{n:,}"


def _site_name(tenant: Tenant) -> str:
    """Bare hostname for subject lines + body prose ("backfill failed on
    my-site.atlassian.net"). Goes through `url_helpers.tenant_site_url`
    so the display_url-first preference is single-sourced and the
    `cloud-{uuid}` form never leaks into customer-facing text.

    Special case: when both display_url and base_url are unset, the
    helper falls back to "https://atlassian.net" which would render as
    a bare "atlassian.net" — uglier than the prior "your site" copy
    and factually misleading (it's NOT atlassian.net). Preserve the
    human "your site" fallback for that case only.
    """
    if not tenant.display_url and not tenant.base_url:
        return "your site"
    from app.services.url_helpers import tenant_site_url

    site = tenant_site_url(tenant)
    return site.removeprefix("https://").removeprefix("http://")


# ----- Failure-mode mapping -------------------------------------------------

_FAILURE_SENTENCES: dict[str, str] = {
    "rate_limit": (
        "Jira temporarily rate-limited our requests. This is a Jira-side "
        "throttle, not your install — usually clears within an hour."
    ),
    "network": "A network issue interrupted the sync between Jira Flow Intelligence and Jira.",
    "auth": (
        "We couldn't authenticate to Jira. This usually means Jira Flow Intelligence "
        "needs to be re-consented in your Jira site's Apps settings."
    ),
    "jira_5xx": "Jira's API returned a server error during the run.",
    "platform": ("An issue with Atlassian's Forge platform prevented the run from completing."),
    "unknown": "An unexpected error occurred. We've logged it on our side.",
}


def _classify_failure_mode(error: str | None) -> str:
    if not error:
        return "unknown"
    e = error.lower()
    if "429" in e or ("rate" in e and "limit" in e):
        return "rate_limit"
    if "401" in e or "403" in e or "unauthorized" in e or "forbidden" in e:
        return "auth"
    if "500" in e or "502" in e or "503" in e or "504" in e:
        return "jira_5xx"
    if "timeout" in e or "network" in e or "connection" in e:
        return "network"
    if "queue" in e or "consumer" in e or "forge" in e:
        return "platform"
    return "unknown"


# ----- Public address constants ---------------------------------------------

ALERT_FROM_ADDRESS = get_settings().alert_from_address

BACKFILL_HARD_CAP = 50_000


# ----- Core Resend send helper --------------------------------------------


def _send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    from_address: str | None = None,
    reply_to: str | None = None,
) -> bool:
    """Send a plain-text email via Resend. Returns True on success, False on
    skip / transient failure (the caller does not need to handle the boolean;
    surfaced for logging clarity).

    Path-specific From / Reply-to overrides are supported via the per-call
    `from_address` / `reply_to` arguments — see `ALERT_FROM_ADDRESS` for the
    documented exception to the operational defaults.

    Failure modes:
    - Dry-run env var set → log + return True (no network call)
    - ResendConfigError (init failure) → propagates; surfaces misconfig early
    - Resend SDK raises (4xx/5xx, timeout, etc.) → log warning, return False;
      the calling business operation is not rolled back
    """
    settings = get_settings()
    from_addr = from_address or settings.resend_from_address
    reply_addr = reply_to or settings.resend_reply_to

    if not settings.resend_enabled:
        logger.debug("resend_disabled_skipping_send", to=to, subject=subject)
        return False

    if os.environ.get("RESEND_DRY_RUN", "").strip() in ("1", "true", "True"):
        logger.info("resend_dry_run", to=to, subject=subject, from_=from_addr)
        return True

    try:
        _ensure_initialized()
    except ResendConfigError as exc:
        logger.warning(
            "resend_send_failed_config",
            to=to,
            subject=subject,
            error=str(exc),
        )
        return False

    started = time.monotonic()
    try:
        # Cast keeps mypy happy with the SDK's `SendParams` TypedDict — the
        # dict literal matches the shape, but strict TypedDict checks reject
        # `dict[str, Any]`. Runtime semantics are identical.
        params = cast(
            "resend.Emails.SendParams",
            {
                "from": from_addr,
                "to": [to],
                "subject": subject,
                "text": body_text,
                "reply_to": [reply_addr],
            },
        )
        result = resend.Emails.send(params)
    except Exception as exc:  # Resend SDK raises a few different exception types
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "resend_send_failed",
            to=to,
            subject=subject,
            from_=from_addr,
            latency_ms=latency_ms,
            error=str(exc),
            exc_info=False,
        )
        return False

    latency_ms = int((time.monotonic() - started) * 1000)
    message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    logger.info(
        "resend_sent",
        to=to,
        subject=subject,
        from_=from_addr,
        latency_ms=latency_ms,
        message_id=message_id,
    )
    # Breadcrumb for the free-tier-cap monitoring plan documented in the
    # runbook §Transactional email — `resend.send.count` log lines can be
    # grep'd in CloudWatch Logs to estimate monthly volume manually until
    # volume justifies a CloudWatch alarm (planned at >1,000/mo).
    logger.info("resend.send.count", tenant_inferred="unknown")
    return True


# ----- Public API: path #1 (ADR-0037 alert delivery) ----------------------


def send_alert_email(*, to: str, subject: str, body_text: str) -> bool:
    """Deliver a fired-alert message to an email destination (ADR-0037).
    Sent from the configured `alert_from_address` so customers can filter
    alert volume separately from operational mail; reply-to defaults to the
    configured `resend_reply_to`."""
    return _send_email(to=to, subject=subject, body_text=body_text, from_address=ALERT_FROM_ADDRESS)


# ----- Public API: path #3 (ADR-0037 24h failure digest) ------------------


def send_failure_digest_email(
    *,
    to: str,
    admin_first_name: str,
    failure_count: int,
    failures: list[dict[str, str]],
    project_dashboard_url: str,
    pause_threshold: int,
) -> bool:
    """24h batched alert-delivery-failure digest (ADR-0037). Unlike the
    programmatic alerts, this DOES sign off as the product — it's an
    operational customer-relationship email like the ADR-0033 backfill
    emails, so it uses the default From (notifications@) + Reply-to support@."""
    lines = "\n".join(
        f"- {f['destination_name']} ({f['channel']}): {f['error_short']} "
        f"— last failed {f['last_failed_at_human']}"
        for f in failures
    )
    body = (
        f"Hi {admin_first_name},\n\n"
        f"Jira Flow Intelligence tried to deliver {failure_count} alert(s) in the last 24 hours and "
        f"the destination(s) returned errors. The list:\n\n"
        f"{lines}\n\n"
        f"Most common cause is a Slack webhook that was deleted in the workspace, a Teams "
        f"webhook that was revoked, or an email recipient that no longer exists. Open "
        f"Settings → Alert Destinations to inspect and re-configure: {project_dashboard_url}\n\n"
        f"If a destination keeps failing, Jira Flow Intelligence pauses it after {pause_threshold} "
        f"consecutive failures and lets you know in Settings — no more emails from that "
        f"destination until you fix it.\n\n"
        f"The Jira Flow Intelligence team"
    )
    subject = (
        f"Jira Flow Intelligence — {failure_count} alert delivery failures in the last 24 hours"
    )
    return _send_email(to=to, subject=subject, body_text=body)


# ----- Public API: path #2 (ADR-0033 backfill emails) ---------------------


def send_backfill_completion_email(tenant: Tenant, project_key: str | None = None) -> bool:
    """ADR-0033 outcome #4 active-push channel — §1 of customer-copy doc.
    Fires when backfill completes with `processed < cap`.

    `project_key` is the project the customer is most likely to want to
    open — typically resolved by the caller via
    `url_helpers.pick_primary_project_key(db, tenant.client_key)`. When
    None, `project_dashboard_url` falls back to the tenant's Jira root.
    """
    from app.services.url_helpers import project_dashboard_url

    if not tenant.admin_contact_email:
        logger.warning("resend_completion_skipped_no_email", tenant_id=tenant.client_key)
        return False
    n = _format_count(tenant.backfill_processed_issues)
    subject = f"Jira Flow Intelligence — backfill complete ({n} issues)"
    body = f"""Hi {_first_name_from_email(tenant.admin_contact_email)},

Jira Flow Intelligence has finished indexing your Jira history — {n} issues processed and now visible on every flow chart and the bottleneck card.

The dashboard reflects the full picture now; before backfill it was showing recent activity only. Open it at {project_dashboard_url(tenant, project_key)}.

Anything looks off, reply to this email.

The Jira Flow Intelligence team"""
    return _send_email(to=tenant.admin_contact_email, subject=subject, body_text=body)


def send_backfill_failure_email(
    tenant: Tenant,
    error_summary: str | None,
    project_key: str | None = None,
) -> bool:
    """ADR-0033 outcome #5 active-push channel — §2 of customer-copy doc.
    Fires when backfill status flips to `failed`. `project_key` resolves
    to the Settings deep-link's project context (same `pick_primary_
    project_key` resolution as completion email)."""
    from app.services.url_helpers import settings_url

    if not tenant.admin_contact_email:
        logger.warning("resend_failure_skipped_no_email", tenant_id=tenant.client_key)
        return False
    mode_key = _classify_failure_mode(error_summary)
    sentence = _FAILURE_SENTENCES[mode_key]
    subject = f"Jira Flow Intelligence — backfill failed on {_site_name(tenant)}"
    support_email = get_settings().resend_reply_to
    body = f"""Hi {_first_name_from_email(tenant.admin_contact_email)},

Jira Flow Intelligence tried to index your Jira history and the run failed after our automatic retries.

{sentence}

Open Settings inside Jira Flow Intelligence and click Retry to try again — most failures resolve on the second attempt: {settings_url(tenant, project_key)}

If it fails again, email {support_email} and we'll look at it directly.

The Jira Flow Intelligence team"""
    return _send_email(to=tenant.admin_contact_email, subject=subject, body_text=body)


def send_backfill_cap_reached_email(tenant: Tenant) -> bool:
    """ADR-0033 outcome #6 active-push channel — §3 of customer-copy doc.
    Fires when backfill completes with `processed >= 50000` (the hard cap)."""
    if not tenant.admin_contact_email:
        logger.warning("resend_cap_reached_skipped_no_email", tenant_id=tenant.client_key)
        return False
    subject = "Jira Flow Intelligence — backfill complete at 50,000-issue cap"
    support_email = get_settings().resend_reply_to
    body = f"""Hi {_first_name_from_email(tenant.admin_contact_email)},

Jira Flow Intelligence has finished indexing your Jira history, but your site has more than 50,000 historical issues — we capped the initial run at 50,000.

All flow charts and the bottleneck card are populated; older issues beyond the cap are not yet indexed.

Email {support_email} if you want the cap extended for your site — we do this for free, just need to know.

The Jira Flow Intelligence team"""
    return _send_email(to=tenant.admin_contact_email, subject=subject, body_text=body)


# ----- State-transition dispatch (path #2 entry point) --------------------


def fire_terminal_state_email(tenant: Tenant, db: Session | None = None) -> None:
    """Called by the backfill-state machine when status flips to a terminal
    value (`completed` or `failed`). Decides which of the three ADR-0033
    emails to send based on the tenant's current row state.

    `db` is optional for backward compat with test paths; when provided,
    the email's project-scoped URL resolves to the tenant's most-active
    project via `url_helpers.pick_primary_project_key`. When None,
    the URL falls back to the tenant's Jira root (the 2026-06-08 fix
    still routes the customer to a working page, just one click less
    specific than the best-available deep-link).

    Idempotent caveat: callers should only invoke this when the status
    actually transitions to terminal (i.e., not on every progress report).
    See `app/routers/forge_sync.py::backfill_progress` for the call site."""
    from app.services.url_helpers import pick_primary_project_key

    project_key: str | None = None
    if db is not None:
        project_key = pick_primary_project_key(db, tenant.client_key)

    if tenant.backfill_status == "completed":
        if (tenant.backfill_processed_issues or 0) >= BACKFILL_HARD_CAP:
            send_backfill_cap_reached_email(tenant)
        else:
            send_backfill_completion_email(tenant, project_key=project_key)
    elif tenant.backfill_status == "failed":
        send_backfill_failure_email(tenant, tenant.backfill_error, project_key=project_key)
    else:
        logger.warning(
            "fire_terminal_state_email_noop",
            tenant_id=tenant.client_key,
            backfill_status=tenant.backfill_status,
        )


# Touch the module to avoid an unused-import warning in environments where
# the lazy init never fires (tests with mocked sends). The cost is zero.
_ = utcnow
