from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.deps import current_tenant_context
from app.core.tenant_context import TenantContext
from app.db.models import Alert, AlertDeliveryDestination, AlertRule, Issue
from app.db.session import get_db
from app.schemas.api import (
    AlertDestinationIn,
    AlertDestinationOut,
    AlertDestinationsResponse,
    AlertOut,
    AlertRuleIn,
    AlertRuleOut,
    AlertsResponse,
    EvaluateAlertsResponse,
    RuleDestinationsIn,
    RuleDestinationsOut,
)
from app.services import alert_destinations
from app.services.alert_dispatch import (
    dispatch_alerts,
    send_failure_digest_if_due,
    send_test_to_destination,
)
from app.services.alert_service import evaluate_alerts, upsert_rule
from app.services.insight_service import InsightReport, generate_insight_report
from app.services.metrics_service import (
    WindowSnapshot,
    compute_window_snapshot,
    cycle_time_throughput,
    default_windows,
    discover_statuses,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _alert_out(a: Alert) -> AlertOut:
    return AlertOut(
        id=a.id,
        rule_id=a.rule_id,
        rule_type=a.rule_type,
        issue_id=a.issue_id,
        status=a.status,
        triggered_at=a.triggered_at,
        payload=a.payload or {},
    )


@router.get("", response_model=AlertsResponse)
def list_alerts(
    rule_id: str | None = Query(default=None),
    issue_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, le=2000),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> AlertsResponse:
    stmt = select(Alert).where(Alert.tenant_id == ctx.tenant_id)
    if rule_id:
        stmt = stmt.where(Alert.rule_id == rule_id)
    if issue_id:
        stmt = stmt.where(Alert.issue_id == issue_id)
    if since:
        stmt = stmt.where(Alert.triggered_at >= since)
    if project_key:
        # Issue-level alerts: keep only those whose issue lives in this project.
        # Non-issue alerts (status/trend) carry no project ref today; once
        # per-project re-evaluation lands they'll be persisted with one. Until
        # then we keep showing them so the page isn't misleadingly empty.
        in_project = (
            select(Issue.id)
            .where(Issue.tenant_id == ctx.tenant_id, Issue.project_key == project_key)
            .scalar_subquery()
        )
        stmt = stmt.where(Alert.issue_id.is_(None) | Alert.issue_id.in_(in_project))
    stmt = stmt.order_by(Alert.triggered_at.desc()).limit(limit)
    rows = list(db.scalars(stmt))
    return AlertsResponse(alerts=[_alert_out(a) for a in rows], total=len(rows))


def _evaluate_inputs(
    db: Session,
    ctx: TenantContext,
    *,
    now: datetime,
    days: int,
    project_key: str | None,
) -> tuple[InsightReport, WindowSnapshot]:
    """Build the insight report + current snapshot that alert evaluation needs.
    Shared by /evaluate (manual) and /evaluate-dispatch (scheduled, ADR-0037)."""
    (cur_s, cur_e), (prev_s, prev_e) = default_windows(now=now, days=days)
    statuses = discover_statuses(db, ctx.tenant_id, project_key=project_key)
    cur_snap = compute_window_snapshot(
        db, ctx.tenant_id, cur_s, cur_e, statuses=statuses, project_key=project_key
    )
    prev_snap = compute_window_snapshot(
        db, ctx.tenant_id, prev_s, prev_e, statuses=statuses, project_key=project_key
    )
    _, cur_cycles = cycle_time_throughput(db, ctx.tenant_id, cur_s, cur_e, project_key=project_key)
    _, prev_cycles = cycle_time_throughput(
        db, ctx.tenant_id, prev_s, prev_e, project_key=project_key
    )
    cur_avg = float(sum(cur_cycles) / len(cur_cycles)) if cur_cycles else None
    prev_avg = float(sum(prev_cycles) / len(prev_cycles)) if prev_cycles else None
    report = generate_insight_report(
        cur_snap, prev_snap, ctx, cycle_time_current=cur_avg, cycle_time_previous=prev_avg
    )
    return report, cur_snap


@router.post("/evaluate", response_model=EvaluateAlertsResponse)
def evaluate(
    days: int = Query(default=7, ge=1, le=365),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> EvaluateAlertsResponse:
    now = utcnow()
    report, cur_snap = _evaluate_inputs(db, ctx, now=now, days=days, project_key=project_key)
    triggered = evaluate_alerts(db, ctx, insight_report=report, now=now, current_snapshot=cur_snap)
    return EvaluateAlertsResponse(
        triggered=len(triggered),
        alerts=[_alert_out(a) for a in triggered],
    )


@router.post("/evaluate-dispatch", response_model=EvaluateAlertsResponse)
def evaluate_dispatch(
    tier: str = Query(..., pattern="^(daily|hourly)$"),
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> EvaluateAlertsResponse:
    """Cadence-tiered evaluation entry point (ADR-0037), called by the Forge
    `daily-alert-eval` / `hourly-alert-eval` scheduledTrigger resolvers. Runs
    only the rules in the requested cadence tier and persists triggered alerts
    (which activates the in-product surface). Push delivery to configured
    destinations is layered on this same path in phase 3."""
    now = utcnow()
    report, cur_snap = _evaluate_inputs(db, ctx, now=now, days=days, project_key=None)
    triggered = evaluate_alerts(
        db, ctx, insight_report=report, now=now, current_snapshot=cur_snap, tier=tier
    )
    # ADR-0037 phase 3: push newly-triggered alerts to their destinations.
    dispatch_alerts(db, ctx, triggered, now=now)
    # The 24h failure digest rides the daily sweep (once-per-day cadence
    # naturally bounds it; the function also guards once-per-24h).
    if tier == "daily":
        send_failure_digest_if_due(db, ctx, now=now)
    return EvaluateAlertsResponse(
        triggered=len(triggered),
        alerts=[_alert_out(a) for a in triggered],
    )


@router.get("/rules", response_model=list[AlertRuleOut])
def list_rules(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> list[AlertRuleOut]:
    rows = list(db.scalars(select(AlertRule).where(AlertRule.tenant_id == ctx.tenant_id)))
    return [AlertRuleOut(id=r.id, type=r.type, enabled=r.enabled, config=r.config) for r in rows]


@router.put("/rules", response_model=AlertRuleOut)
def put_rule(
    body: AlertRuleIn,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> AlertRuleOut:
    rule = upsert_rule(db, ctx.tenant_id, body.id, body.type, body.config, enabled=body.enabled)
    # ADR-0037 entry point 1: one-shot eval of the just-saved rule against
    # current state so pre-existing violations surface immediately in-product.
    # evaluate_alerts persists only — it does NOT dispatch, so there's no
    # burst-push when a new rule matches many existing tickets (push happens on
    # the next sweep/ticket-event cycle, cooldown-bounded). Best-effort: a
    # config that can't be evaluated yet (e.g. no snapshot data) just no-ops.
    if rule.enabled:
        now = utcnow()
        report, cur_snap = _evaluate_inputs(db, ctx, now=now, days=7, project_key=None)
        evaluate_alerts(
            db, ctx, insight_report=report, now=now, current_snapshot=cur_snap, rule_ids=[rule.id]
        )
    return AlertRuleOut(id=rule.id, type=rule.type, enabled=rule.enabled, config=rule.config)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> None:
    """Delete an alert rule. Existing alerts triggered by this rule stay in
    the alerts table — those are historical artifacts, not state. The
    `(tenant_id, rule_id)` PK is the only thing dropped here."""
    rule = db.get(AlertRule, (ctx.tenant_id, rule_id))
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    db.execute(
        sql_delete(AlertRule).where(AlertRule.tenant_id == ctx.tenant_id, AlertRule.id == rule_id)
    )
    db.commit()


# ----- Alert delivery destinations (ADR-0037 phase 4) -----------------------


def _dest_out(dest: AlertDeliveryDestination, recent_failures: int) -> AlertDestinationOut:
    return AlertDestinationOut(
        id=dest.id,
        type=dest.type,
        name=dest.name,
        config=alert_destinations.mask_config(dest.type, dest.config or {}),
        is_tenant_default=dest.is_tenant_default,
        status=dest.status,
        last_test_at=dest.last_test_at,
        last_test_status=dest.last_test_status,
        recent_failure_count=recent_failures,
    )


@router.get("/destinations", response_model=AlertDestinationsResponse)
def list_destinations(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> AlertDestinationsResponse:
    pairs = alert_destinations.list_destinations(db, ctx.tenant_id)
    return AlertDestinationsResponse(destinations=[_dest_out(d, fails) for d, fails in pairs])


@router.put("/destinations", response_model=AlertDestinationOut)
def put_destination(
    body: AlertDestinationIn,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> AlertDestinationOut:
    dest = alert_destinations.upsert_destination(
        db,
        ctx.tenant_id,
        dest_id=body.id,
        dtype=body.type,
        name=body.name,
        config=body.config,
        is_tenant_default=body.is_tenant_default,
        status=body.status,
    )
    fails = alert_destinations.recent_failure_count(db, ctx.tenant_id, dest.id, utcnow())
    return _dest_out(dest, fails)


@router.delete("/destinations/{destination_id}", status_code=204)
def delete_destination(
    destination_id: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> None:
    if not alert_destinations.delete_destination(db, ctx.tenant_id, destination_id):
        raise HTTPException(status_code=404, detail="destination not found")


@router.post("/destinations/{destination_id}/test", response_model=AlertDestinationOut)
def send_destination_test(
    destination_id: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> AlertDestinationOut:
    dest = db.get(AlertDeliveryDestination, (ctx.tenant_id, destination_id))
    if dest is None:
        raise HTTPException(status_code=404, detail="destination not found")
    send_test_to_destination(db, dest)
    fails = alert_destinations.recent_failure_count(db, ctx.tenant_id, dest.id, utcnow())
    return _dest_out(dest, fails)


@router.get("/rules/{rule_id}/destinations", response_model=RuleDestinationsOut)
def get_rule_destinations(
    rule_id: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> RuleDestinationsOut:
    return RuleDestinationsOut(
        destination_ids=alert_destinations.get_rule_destination_ids(db, ctx.tenant_id, rule_id)
    )


@router.put("/rules/{rule_id}/destinations", response_model=RuleDestinationsOut)
def put_rule_destinations(
    rule_id: str,
    body: RuleDestinationsIn,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(current_tenant_context),
) -> RuleDestinationsOut:
    if db.get(AlertRule, (ctx.tenant_id, rule_id)) is None:
        raise HTTPException(status_code=404, detail="rule not found")
    alert_destinations.set_rule_destinations(
        db,
        ctx.tenant_id,
        rule_id,
        body.destination_ids,
        override_cooldown_seconds=body.override_cooldown_seconds,
    )
    return RuleDestinationsOut(
        destination_ids=alert_destinations.get_rule_destination_ids(db, ctx.tenant_id, rule_id)
    )
