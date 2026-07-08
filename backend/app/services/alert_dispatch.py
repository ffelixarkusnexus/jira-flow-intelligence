"""Alert dispatch orchestration (ADR-0037 phase 3).

Takes the alerts a tier sweep / ticket-event eval just produced, resolves each
rule's effective destinations, applies the anti-spam cooldown, dispatches via
the channel dispatcher (email = SES, slack/teams = incoming webhook), and
records every attempt in `alert_fires` (delivered | failed | skipped_cooldown)
for the cooldown check + the 24h failure digest.

Destination resolution (ADR-0037 outcome #2): a rule's explicit bindings
override the tenant defaults — if a rule has any `alert_rule_destinations`
rows, those are its destinations; otherwise the tenant-default destinations
apply. No destinations anywhere → in-product surface only (non-breaking
default).

Cooldown (outcome #3): per (rule, destination), effective cooldown =
max(override_cooldown_seconds or 3600, 300). The 300s floor is the hard
ceiling — no destination fires more than once per 5 minutes regardless of
config. Checked against the last *delivered* fire.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.core.tenant_context import TenantContext
from app.db.models import (
    Alert,
    AlertDeliveryDestination,
    AlertFire,
    AlertRuleDestination,
    Issue,
    Tenant,
)
from app.services import resend_service
from app.services.alert_messages import (
    RenderedMessage,
    render_alert_group,
    render_test_message,
)
from app.services.webhook_dispatcher import post_webhook

logger = get_logger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 3600
_HARD_CEILING_SECONDS = 300  # no destination fires more than once per 5 min
_PAUSE_THRESHOLD = 5  # consecutive failures before a destination is auto-paused
_DIGEST_INTERVAL_SECONDS = 86_400  # failure digest at most once per 24h


def _effective_destinations(
    session: Session, tenant_id: str, rule_id: str
) -> list[tuple[AlertDeliveryDestination, int | None]]:
    """(destination, override_cooldown_seconds) pairs for a rule. Explicit rule
    bindings override tenant defaults; only active destinations."""
    bound = list(
        session.execute(
            select(
                AlertDeliveryDestination,
                AlertRuleDestination.override_cooldown_seconds,
            )
            .join(
                AlertRuleDestination,
                (AlertRuleDestination.tenant_id == AlertDeliveryDestination.tenant_id)
                & (AlertRuleDestination.destination_id == AlertDeliveryDestination.id),
            )
            .where(
                AlertRuleDestination.tenant_id == tenant_id,
                AlertRuleDestination.alert_rule_id == rule_id,
                AlertDeliveryDestination.status == "active",
            )
        ).all()
    )
    if bound:
        return [(d, cd) for d, cd in bound]

    defaults = list(
        session.scalars(
            select(AlertDeliveryDestination).where(
                AlertDeliveryDestination.tenant_id == tenant_id,
                AlertDeliveryDestination.is_tenant_default.is_(True),
                AlertDeliveryDestination.status == "active",
            )
        ).all()
    )
    return [(d, None) for d in defaults]


def _in_cooldown(
    session: Session,
    tenant_id: str,
    rule_id: str,
    destination_id: str,
    override_cooldown: int | None,
    now: datetime,
) -> bool:
    cooldown = max(
        override_cooldown if override_cooldown is not None else _DEFAULT_COOLDOWN_SECONDS,
        _HARD_CEILING_SECONDS,
    )
    cutoff = now - timedelta(seconds=cooldown)
    last = session.scalar(
        select(AlertFire.fired_at)
        .where(
            AlertFire.tenant_id == tenant_id,
            AlertFire.alert_rule_id == rule_id,
            AlertFire.destination_id == destination_id,
            AlertFire.status == "delivered",
        )
        .order_by(AlertFire.fired_at.desc())
        .limit(1)
    )
    return last is not None and last > cutoff


def _record_fire(
    session: Session,
    tenant_id: str,
    rule_id: str,
    destination_id: str,
    now: datetime,
    status: str,
    detail: str | None,
) -> None:
    session.add(
        AlertFire(
            tenant_id=tenant_id,
            alert_rule_id=rule_id,
            destination_id=destination_id,
            fired_at=now,
            status=status,
            detail=detail,
        )
    )


def _deliver(dest: AlertDeliveryDestination, msg: RenderedMessage) -> tuple[bool, str | None]:
    cfg = dest.config or {}
    if dest.type == "email":
        address = cfg.get("address")
        if not address:
            return False, "destination missing email address"
        ok = resend_service.send_alert_email(to=address, subject=msg.subject, body_text=msg.body)
        return ok, None if ok else "email send failed (SES)"
    # slack | teams
    url = cfg.get("webhook_url")
    if not url:
        return False, "destination missing webhook_url"
    res = post_webhook(url, msg.body, channel_type=dest.type)
    return res.ok, res.detail


def dispatch_alerts(
    session: Session,
    ctx: TenantContext,
    alerts: Sequence[Alert],
    *,
    now: datetime | None = None,
) -> int:
    """Dispatch freshly-triggered alerts to their effective destinations.
    Returns the number of successful deliveries. Records every attempt in
    alert_fires. Never raises — a single destination failure is recorded and
    the loop continues."""
    if not alerts:
        return 0
    now = now or utcnow()

    # ADR-0046 follow-up — fetch the full Tenant row so url_helpers can
    # apply the display_url-first preference. Previously this pulled
    # `Tenant.base_url` directly, which is Atlassian's canonical
    # `cloud-{uuid}` form and does NOT route to the user-facing site —
    # every ticket link and dashboard CTA built from it went to
    # Atlassian's "Page unavailable" page until the 2026-06-08 fix.
    tenant = session.scalar(select(Tenant).where(Tenant.client_key == ctx.tenant_id))
    if tenant is None:
        # Defensive — middleware lazy-upserts the tenant on first FIT,
        # so this branch should never fire in production. Bail out
        # rather than render a malformed URL.
        return 0
    # Batch-resolve issue summaries for the per-issue alerts.
    issue_ids = {a.issue_id for a in alerts if a.issue_id}
    summaries: dict[str, str | None] = {}
    if issue_ids:
        rows = session.execute(
            select(Issue.id, Issue.summary).where(
                Issue.tenant_id == ctx.tenant_id, Issue.id.in_(issue_ids)
            )
        ).all()
        summaries = {row[0]: row[1] for row in rows}

    # ADR-0037 v1 grouping (pulled forward from v2 after E2E surfaced the
    # un-grouped UX as unusable — 24 stuck tickets crossing threshold in one
    # sweep produced 24 separate Slack messages). Bucket alerts by
    # (rule_id, destination_id) so one bucket = one rendered message that
    # lists every affected ticket. Cooldown / failure tracking still record
    # one alert_fires row per alert (preserves the audit trail + per-alert
    # idempotency); only the OUTBOUND dispatch is collapsed.
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for alert in alerts:
        destinations = _effective_destinations(session, ctx.tenant_id, alert.rule_id)
        if not destinations:
            continue
        for dest, override_cd in destinations:
            key = (alert.rule_id, dest.id)
            if key not in buckets:
                buckets[key] = {
                    "dest": dest,
                    "override_cooldown": override_cd,
                    "rule_type": alert.rule_type,
                    "alerts": [],
                }
            buckets[key]["alerts"].append(alert)

    delivered = 0
    for (rule_id, dest_id), bucket in buckets.items():
        dest = bucket["dest"]
        bucket_alerts = bucket["alerts"]
        if _in_cooldown(session, ctx.tenant_id, rule_id, dest_id, bucket["override_cooldown"], now):
            for a in bucket_alerts:
                _record_fire(
                    session, ctx.tenant_id, a.rule_id, dest_id, now, "skipped_cooldown", None
                )
            session.flush()
            continue
        # ADR-0046 follow-up: feed the renderer the user-facing site URL
        # via url_helpers (display_url-first), NOT tenant.base_url
        # directly (canonical `cloud-{uuid}` form, doesn't route).
        # Project-dashboard URL likewise goes through the named helper.
        from app.services.url_helpers import (
            pick_primary_project_key,
            project_dashboard_url,
            tenant_site_url,
        )

        site_url = tenant_site_url(tenant)
        dash_url = project_dashboard_url(tenant, pick_primary_project_key(session, ctx.tenant_id))
        msg = render_alert_group(
            rule_type=bucket["rule_type"],
            channel=dest.type,
            alerts=bucket_alerts,
            base_url=site_url,
            issue_summaries=summaries,
            project_dashboard_url=dash_url,
        )
        ok, detail = _deliver(dest, msg)
        for a in bucket_alerts:
            _record_fire(
                session,
                ctx.tenant_id,
                a.rule_id,
                dest_id,
                now,
                "delivered" if ok else "failed",
                detail,
            )
        # Flush after each bucket so subsequent cooldown checks in this same
        # call see prior fires (correctness fix; the un-flushed pending fires
        # were why the 5-min hard ceiling silently failed to engage when many
        # alerts of the same rule fired in one sweep — pre-grouping bug).
        session.flush()
        if ok:
            delivered += len(bucket_alerts)
        else:
            _maybe_pause(session, ctx.tenant_id, dest)

    session.commit()
    return delivered


def send_test_to_destination(
    session: Session,
    dest: AlertDeliveryDestination,
    *,
    now: datetime | None = None,
) -> bool:
    """Send a clearly-labeled test message to a destination (ADR-0037 outcome
    #5, Settings → Send test). Bypasses cooldown. Stamps last_test_at/status."""
    now = now or utcnow()
    msg = render_test_message(dest.type, dest.name)
    ok, detail = _deliver(dest, msg)
    dest.last_test_at = now
    dest.last_test_status = "ok" if ok else f"failed: {detail}"
    session.commit()
    return ok


def _maybe_pause(session: Session, tenant_id: str, dest: AlertDeliveryDestination) -> None:
    """Auto-pause a destination after `_PAUSE_THRESHOLD` consecutive failures
    (no delivered fire in between). Matches the promise in the failure-digest
    copy. A paused destination is `status='disabled'` and stops being used
    until the customer re-enables it in Settings."""
    last_delivered = session.scalar(
        select(AlertFire.fired_at)
        .where(
            AlertFire.tenant_id == tenant_id,
            AlertFire.destination_id == dest.id,
            AlertFire.status == "delivered",
        )
        .order_by(AlertFire.fired_at.desc())
        .limit(1)
    )
    count_stmt = (
        select(func.count())
        .select_from(AlertFire)
        .where(
            AlertFire.tenant_id == tenant_id,
            AlertFire.destination_id == dest.id,
            AlertFire.status == "failed",
        )
    )
    if last_delivered is not None:
        count_stmt = count_stmt.where(AlertFire.fired_at > last_delivered)
    consecutive = session.scalar(count_stmt) or 0
    if consecutive >= _PAUSE_THRESHOLD:
        dest.status = "disabled"
        logger.warning(
            "alert_destination_auto_paused",
            destination_id=dest.id,
            consecutive_failures=consecutive,
        )


def send_failure_digest_if_due(
    session: Session, ctx: TenantContext, *, now: datetime | None = None
) -> bool:
    """Send the 24h batched alert-delivery-failure digest to the tenant admin,
    at most once per 24h, only if there were failures in the window (ADR-0037
    outcome #4 / CLAUDE.md rule #9). Rides the daily sweep. Returns True if a
    digest was sent."""
    now = now or utcnow()
    tenant = session.scalar(select(Tenant).where(Tenant.client_key == ctx.tenant_id))
    if tenant is None or not tenant.admin_contact_email:
        return False
    if (
        tenant.last_failure_digest_at is not None
        and (now - tenant.last_failure_digest_at).total_seconds() < _DIGEST_INTERVAL_SECONDS
    ):
        return False

    cutoff = now - timedelta(seconds=_DIGEST_INTERVAL_SECONDS)
    rows = list(
        session.execute(
            select(AlertFire, AlertDeliveryDestination)
            .join(
                AlertDeliveryDestination,
                (AlertFire.tenant_id == AlertDeliveryDestination.tenant_id)
                & (AlertFire.destination_id == AlertDeliveryDestination.id),
            )
            .where(
                AlertFire.tenant_id == ctx.tenant_id,
                AlertFire.status == "failed",
                AlertFire.fired_at >= cutoff,
            )
            .order_by(AlertFire.fired_at.desc())
        ).all()
    )
    if not rows:
        return False

    # Group by destination — rows are newest-first, so the first seen per
    # destination carries the latest error + timestamp.
    grouped: dict[str, dict[str, str]] = {}
    for fire, dest in rows:
        if dest.id in grouped:
            continue
        grouped[dest.id] = {
            "destination_name": dest.name,
            "channel": dest.type,
            "error_short": fire.detail or "delivery failed",
            "last_failed_at_human": fire.fired_at.strftime("%Y-%m-%d %H:%M UTC"),
        }

    # ADR-... ADR-0046 follow-up: customer-facing URL routing — go through
    # `url_helpers.project_dashboard_url` so the user-facing site URL
    # (`display_url` first, then `base_url`) is used, NOT the canonical
    # `cloud-{uuid}` form that doesn't route. Pre-fix this was
    # `tenant.base_url or ""` which sent customers to a broken page.
    from app.services.url_helpers import (
        pick_primary_project_key,
        project_dashboard_url,
    )

    dash_url = project_dashboard_url(tenant, pick_primary_project_key(session, tenant.client_key))
    ok = resend_service.send_failure_digest_email(
        to=tenant.admin_contact_email,
        admin_first_name="there",
        failure_count=len(rows),
        failures=list(grouped.values()),
        project_dashboard_url=dash_url,
        pause_threshold=_PAUSE_THRESHOLD,
    )
    if ok:
        tenant.last_failure_digest_at = now
        session.commit()
    return ok
