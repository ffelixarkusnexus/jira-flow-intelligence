"""Alert delivery destination CRUD + per-rule bindings (ADR-0037 phase 4).

Backs the Settings → Alert Destinations UI. Webhook URLs are masked on output
(they're channel-write secrets — anyone with the URL can post to the channel);
the customer re-enters the URL to change it. Email addresses are returned in
full (the customer's own routing address, not a secret).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.db.models import AlertDeliveryDestination, AlertFire, AlertRuleDestination

_FAILURE_WINDOW_SECONDS = 86_400


def mask_config(dtype: str, config: dict[str, Any]) -> dict[str, Any]:
    """Output-safe config: full email address; webhook URL reduced to a
    set-flag + short hint so the UI shows 'configured' without re-exposing it."""
    if dtype == "email":
        return {"address": config.get("address")}
    url = str(config.get("webhook_url") or "")
    # Last 4 chars only — enough to recognize "this is the one I pasted" without
    # re-exposing meaningful webhook-token material (the URL IS the secret).
    return {"webhook_url_set": bool(url), "webhook_url_hint": url[-4:] if url else ""}


def recent_failure_count(
    session: Session, tenant_id: str, destination_id: str, now: datetime
) -> int:
    cutoff = now - timedelta(seconds=_FAILURE_WINDOW_SECONDS)
    return (
        session.scalar(
            select(func.count())
            .select_from(AlertFire)
            .where(
                AlertFire.tenant_id == tenant_id,
                AlertFire.destination_id == destination_id,
                AlertFire.status == "failed",
                AlertFire.fired_at >= cutoff,
            )
        )
        or 0
    )


def list_destinations(
    session: Session, tenant_id: str, *, now: datetime | None = None
) -> list[tuple[AlertDeliveryDestination, int]]:
    now = now or utcnow()
    dests = list(
        session.scalars(
            select(AlertDeliveryDestination)
            .where(AlertDeliveryDestination.tenant_id == tenant_id)
            .order_by(AlertDeliveryDestination.created_at.asc())
        ).all()
    )
    return [(d, recent_failure_count(session, tenant_id, d.id, now)) for d in dests]


def upsert_destination(
    session: Session,
    tenant_id: str,
    *,
    dest_id: str,
    dtype: str,
    name: str,
    config: dict[str, Any] | None,
    is_tenant_default: bool,
    status: str,
) -> AlertDeliveryDestination:
    """Create or update a destination. `config=None` preserves the stored
    config (so toggling is_tenant_default or re-enabling a paused destination
    doesn't require re-pasting the masked webhook URL)."""
    dest = session.get(AlertDeliveryDestination, (tenant_id, dest_id))
    if dest is None:
        dest = AlertDeliveryDestination(
            tenant_id=tenant_id, id=dest_id, created_at=utcnow(), config={}
        )
        session.add(dest)
    dest.type = dtype
    dest.name = name
    if config is not None:
        dest.config = config
    dest.is_tenant_default = is_tenant_default
    dest.status = status
    session.commit()
    return dest


def delete_destination(session: Session, tenant_id: str, dest_id: str) -> bool:
    dest = session.get(AlertDeliveryDestination, (tenant_id, dest_id))
    if dest is None:
        return False
    # Rule bindings to this destination cascade via the FK; alert_fires history
    # is kept (it FKs to tenant, not destination) as an audit trail.
    session.delete(dest)
    session.commit()
    return True


def get_rule_destination_ids(session: Session, tenant_id: str, rule_id: str) -> list[str]:
    return list(
        session.scalars(
            select(AlertRuleDestination.destination_id).where(
                AlertRuleDestination.tenant_id == tenant_id,
                AlertRuleDestination.alert_rule_id == rule_id,
            )
        ).all()
    )


def set_rule_destinations(
    session: Session,
    tenant_id: str,
    rule_id: str,
    destination_ids: list[str],
    *,
    override_cooldown_seconds: int | None = None,
) -> None:
    """Replace a rule's explicit destination bindings (override semantics)."""
    session.execute(
        sql_delete(AlertRuleDestination).where(
            AlertRuleDestination.tenant_id == tenant_id,
            AlertRuleDestination.alert_rule_id == rule_id,
        )
    )
    for did in dict.fromkeys(destination_ids):  # dedupe, preserve order
        session.add(
            AlertRuleDestination(
                tenant_id=tenant_id,
                alert_rule_id=rule_id,
                destination_id=did,
                override_cooldown_seconds=override_cooldown_seconds,
            )
        )
    session.commit()
