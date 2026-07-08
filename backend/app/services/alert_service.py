from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.tenant_context import TenantContext
from app.db.models import Alert, AlertRule, Issue
from app.services.duration_format import human_duration
from app.services.insight_service import InsightReport, TrendChange
from app.services.metrics_service import WindowSnapshot
from app.services.wip_limits_service import get_wip_limit
from app.services.working_time import working_seconds_between


@dataclass
class AlertEvent:
    rule_id: str
    rule_type: str
    issue_id: str | None
    status: str | None
    key: str  # idempotency key — same key means same alert
    triggered_at: datetime
    payload: dict[str, Any]


# ADR-0041: state-based ticket-level alert rules (`status_duration`,
# `cycle_time`, `no_activity`) include the UTC date in their idempotency key
# so a perpetually-breaching condition re-fires once per UTC day instead of
# once per breach event. Supersedes ADR-0008.
def _utc_date_iso(now: datetime) -> str:
    return now.astimezone(UTC).date().isoformat()


# ----- Rule evaluators ---------------------------------------------------------


def _evaluate_status_duration(rule: AlertRule, issue: Issue, now: datetime) -> list[AlertEvent]:
    """Trigger when a slice's duration exceeds threshold for a watched status."""
    threshold = float(rule.config.get("threshold_seconds", 0))
    target_status = rule.config.get("status")
    if threshold <= 0:
        return []

    events: list[AlertEvent] = []
    for s in issue.time_slices:
        if target_status and s.status != target_status:
            continue
        if s.duration_seconds <= threshold:
            continue
        events.append(
            AlertEvent(
                rule_id=rule.id,
                rule_type=rule.type,
                issue_id=issue.id,
                status=s.status,
                # ADR-0041 daily bucket — supersedes ADR-0008's slice-start bucket
                # so a ticket that stays in the same status past threshold re-fires
                # once per UTC day instead of once per slice-entry.
                key=f"{issue.id}|status_duration|{s.status}|{_utc_date_iso(now)}",
                triggered_at=now,
                payload={
                    "issue_key": issue.key,
                    "status": s.status,
                    "duration_seconds": s.duration_seconds,
                    "threshold_seconds": threshold,
                    "is_open": s.is_open,
                    "message": (
                        f"{issue.key} has been in {s.status} for "
                        f"{human_duration(s.duration_seconds)} "
                        f"(threshold {human_duration(threshold)})."
                    ),
                },
            )
        )
    return events


def _evaluate_cycle_time(
    rule: AlertRule, issue: Issue, now: datetime, ctx: "TenantContext | None" = None
) -> list[AlertEvent]:
    threshold = float(rule.config.get("threshold_seconds", 0))
    if threshold <= 0:
        return []

    # ADR-0043: when a tenant has an active work schedule, the elapsed
    # cycle-time is measured in working seconds. `threshold_seconds`
    # then naturally means "working seconds" (a 7d threshold under a
    # Mon-Fri 9-5 schedule fires after ~7 working days, ~11 calendar days).
    # ctx=None falls through to calendar math — preserves all existing
    # call sites + tests until they're updated to thread ctx through.
    schedule = ctx.work_schedule if ctx is not None else None
    if issue.done_at is not None:
        elapsed = float(working_seconds_between(issue.created_at, issue.done_at, schedule))
    else:
        elapsed = float(working_seconds_between(issue.created_at, now, schedule))

    if elapsed <= threshold:
        return []

    return [
        AlertEvent(
            rule_id=rule.id,
            rule_type=rule.type,
            issue_id=issue.id,
            status=issue.current_status,
            # ADR-0041 daily bucket — supersedes ADR-0008's fire-once-per-issue
            # so a perpetually-overdue ticket re-fires once per UTC day. The
            # ADR-0008 reasoning ("cycle time is monotone, alerting again adds
            # nothing") was mathematically true but operationally wrong: a
            # missed Monday notification left no recovery path.
            key=f"{issue.id}|cycle|{_utc_date_iso(now)}",
            triggered_at=now,
            payload={
                "issue_key": issue.key,
                "elapsed_seconds": int(elapsed),
                "threshold_seconds": int(threshold),
                "message": (
                    f"{issue.key} exceeded cycle time threshold of "
                    f"{human_duration(threshold)} (elapsed {human_duration(elapsed)})."
                ),
            },
        )
    ]


def _evaluate_no_activity(
    rule: AlertRule, issue: Issue, now: datetime, ctx: "TenantContext | None" = None
) -> list[AlertEvent]:
    threshold = float(rule.config.get("threshold_seconds", 0))
    if threshold <= 0 or issue.done_at is not None:
        return []

    last_event = issue.updated_at
    last_transition = max((t.transitioned_at for t in issue.transitions), default=None)
    if last_transition and last_transition > last_event:
        last_event = last_transition

    # ADR-0043: working-time when a schedule is active; calendar otherwise.
    schedule = ctx.work_schedule if ctx is not None else None
    elapsed = float(working_seconds_between(last_event, now, schedule))
    if elapsed <= threshold:
        return []

    return [
        AlertEvent(
            rule_id=rule.id,
            rule_type=rule.type,
            issue_id=issue.id,
            status=issue.current_status,
            # ADR-0041 daily bucket — supersedes ADR-0008's last_event-timestamp
            # bucket. The previous key only re-fired when activity advanced the
            # timestamp, which by definition can't happen on an idle ticket —
            # the very condition the rule exists to flag.
            key=f"{issue.id}|no_activity|{_utc_date_iso(now)}",
            triggered_at=now,
            payload={
                "issue_key": issue.key,
                "last_activity_at": last_event.isoformat(),
                "idle_seconds": int(elapsed),
                "threshold_seconds": int(threshold),
                "message": (
                    f"{issue.key} has had no activity for "
                    f"{human_duration(elapsed)} "
                    f"(threshold {human_duration(threshold)})."
                ),
            },
        )
    ]


def _evaluate_wip_breach(
    rule: AlertRule,
    snapshot: WindowSnapshot,
    session: Session,
    tenant_id: str,
    now: datetime,
) -> list[AlertEvent]:
    """Fire when wip_avg exceeds the configured WIP limit for the (project,
    status) the rule watches. Idempotent per hour-window — same breach
    won't re-fire repeatedly during one evaluation cadence, but a breach
    that persists across hours re-fires once per hour.

    `breach_minutes = 0` on the limit row disables alerting for that limit
    (per ADR-0022) — the limit still drives the visual breach indicator but
    no alert is generated.
    """
    target_status = rule.config.get("status")
    project_key = rule.config.get("project_key")
    if not target_status or not isinstance(target_status, str):
        return []

    status_result = snapshot.statuses.get(target_status)
    if status_result is None:
        return []
    current_wip = status_result.wip_avg

    resolved = get_wip_limit(session, tenant_id, project_key, target_status)
    if resolved.max_in_progress is None or resolved.breach_minutes <= 0:
        return []
    if current_wip <= resolved.max_in_progress:
        return []

    # Window-aligned idempotency: bucket by start of the snapshot's window so
    # the same breach within the same evaluation window never persists twice.
    window_bucket = int(snapshot.window_start.timestamp())
    return [
        AlertEvent(
            rule_id=rule.id,
            rule_type=rule.type,
            issue_id=None,
            status=target_status,
            key=f"wip_breach|{target_status}|{project_key or '*'}|{window_bucket}",
            triggered_at=now,
            payload={
                "status": target_status,
                "project_key": project_key,
                "current_wip": round(current_wip, 2),
                "limit": resolved.max_in_progress,
                "breach_pct": round((current_wip / resolved.max_in_progress - 1) * 100),
                "message": (
                    f"WIP in {target_status} = {current_wip:.1f} / "
                    f"{resolved.max_in_progress} (over limit)."
                ),
            },
        )
    ]


def _evaluate_trend(
    rule: AlertRule, trends: Iterable[TrendChange], now: datetime
) -> list[AlertEvent]:
    metric_filter = rule.config.get("metric")
    direction_filter = rule.config.get("direction", "worsening")
    threshold_pct = float(rule.config.get("threshold_pct", 30.0))

    events: list[AlertEvent] = []
    for trend in trends:
        if metric_filter and trend.metric != metric_filter:
            continue
        if trend.direction != direction_filter:
            continue
        if abs(trend.change_pct) < threshold_pct:
            continue
        events.append(
            AlertEvent(
                rule_id=rule.id,
                rule_type=rule.type,
                issue_id=None,
                status=trend.status,
                key=(f"trend|{trend.metric}|{trend.status or '*'}|{int(now.timestamp() // 3600)}"),
                triggered_at=now,
                payload={
                    "metric": trend.metric,
                    "status": trend.status,
                    "change_pct": trend.change_pct,
                    "direction": trend.direction,
                    "ratio": trend.ratio,
                    "message": (
                        f"{trend.status or 'system'} {trend.metric} "
                        f"{trend.direction} {round(trend.change_pct):+d}% vs prior window."
                    ),
                },
            )
        )
    return events


# ----- Orchestration -----------------------------------------------------------


def _persist(session: Session, tenant_id: str, event: AlertEvent) -> Alert | None:
    # Explicit existence check before insert. The unique constraint on
    # (tenant_id, rule_id, issue_id, status, key) is the safety net, but
    # SQLite + Postgres both treat NULLs as *distinct* in unique constraints,
    # so the IntegrityError fallback alone misses idempotency for alerts
    # with NULL issue_id (trend, wip_breach). The explicit query below
    # handles NULL-equivalence correctly.
    cond_issue = (
        Alert.issue_id.is_(None) if event.issue_id is None else Alert.issue_id == event.issue_id
    )
    cond_status = Alert.status.is_(None) if event.status is None else Alert.status == event.status
    existing = session.scalar(
        select(Alert).where(
            Alert.tenant_id == tenant_id,
            Alert.rule_id == event.rule_id,
            Alert.key == event.key,
            cond_issue,
            cond_status,
        )
    )
    if existing is not None:
        return None

    alert = Alert(
        tenant_id=tenant_id,
        rule_id=event.rule_id,
        rule_type=event.rule_type,
        issue_id=event.issue_id,
        status=event.status,
        key=event.key,
        triggered_at=event.triggered_at,
        payload=event.payload,
    )
    session.add(alert)
    try:
        session.flush()
        return alert
    except IntegrityError:
        # Same (tenant_id, rule_id, issue_id, status, key) under a non-NULL
        # issue_id — the unique constraint kicks in.
        session.rollback()
        return None


# ADR-0037: evaluation-cadence tiering. A rule's effective threshold decides
# how often it needs re-evaluation. Rules thresholding at < 24h are checked
# on the hourly sweep; everything else (the day-to-week defaults) on the daily
# sweep. `trend` is window-based and always daily-tier. This keeps the common
# case (default-threshold rules) at one evaluation per day instead of hundreds.
_TIER_CUTOFF_SECONDS = 86_400  # 24h


def _rule_effective_threshold_seconds(rule: AlertRule) -> float | None:
    """Effective time threshold used for cadence tiering. None = no time
    threshold (the `trend` window-comparison rule)."""
    cfg = rule.config or {}
    if rule.type in ("status_duration", "cycle_time", "no_activity"):
        v = cfg.get("threshold_seconds")
        return float(v) if v is not None else None
    if rule.type == "wip_breach":
        bm = cfg.get("breach_minutes")
        return float(bm) * 60 if bm is not None else 0.0
    return None


def rule_tier(rule: AlertRule) -> str:
    """'hourly' for rules thresholding under 24h, else 'daily'. `trend` is
    always daily (window-based). See ADR-0037 section A."""
    if rule.type == "trend":
        return "daily"
    thr = _rule_effective_threshold_seconds(rule)
    if thr is None:
        return "daily"
    return "hourly" if thr < _TIER_CUTOFF_SECONDS else "daily"


def evaluate_alerts(
    session: Session,
    ctx: TenantContext,
    insight_report: InsightReport | None = None,
    *,
    now: datetime | None = None,
    current_snapshot: WindowSnapshot | None = None,
    tier: str | None = None,
    rule_ids: list[str] | None = None,
) -> list[Alert]:
    """Run enabled rules for the tenant. `current_snapshot` is required for
    `wip_breach` rules; passing None just skips that rule type. Routers that
    compute a snapshot anyway (e.g. /alerts/evaluate) thread it through.

    `tier` (ADR-0037): when set ('daily'|'hourly'), evaluate only rules in that
    cadence tier (see `rule_tier`). None = all rules (manual eval, rule-CRUD).
    `rule_ids`: when set, restrict to those rule ids (used by the rule-CRUD
    one-shot eval to evaluate just the saved rule)."""
    if now is None:
        now = utcnow()

    rules = list(
        session.scalars(
            select(AlertRule).where(
                AlertRule.tenant_id == ctx.tenant_id, AlertRule.enabled.is_(True)
            )
        )
    )
    if tier is not None:
        rules = [r for r in rules if rule_tier(r) == tier]
    if rule_ids is not None:
        wanted = set(rule_ids)
        rules = [r for r in rules if r.id in wanted]
    if not rules:
        return []

    issues = list(session.scalars(select(Issue).where(Issue.tenant_id == ctx.tenant_id)))
    persisted: list[Alert] = []

    for rule in rules:
        events: list[AlertEvent] = []
        if rule.type == "status_duration":
            for issue in issues:
                events.extend(_evaluate_status_duration(rule, issue, now))
        elif rule.type == "cycle_time":
            for issue in issues:
                events.extend(_evaluate_cycle_time(rule, issue, now, ctx))
        elif rule.type == "no_activity":
            for issue in issues:
                events.extend(_evaluate_no_activity(rule, issue, now, ctx))
        elif rule.type == "trend" and insight_report is not None:
            events.extend(_evaluate_trend(rule, insight_report.trends, now))
        elif rule.type == "wip_breach" and current_snapshot is not None:
            events.extend(_evaluate_wip_breach(rule, current_snapshot, session, ctx.tenant_id, now))

        for event in events:
            stored = _persist(session, ctx.tenant_id, event)
            if stored is not None:
                persisted.append(stored)

    session.commit()
    return persisted


def evaluate_issue_alerts(
    session: Session,
    ctx: TenantContext,
    issue_ids: list[str],
    *,
    now: datetime | None = None,
) -> list[Alert]:
    """Targeted per-issue evaluation for the ticket-event path (ADR-0037 entry
    point 2). Evaluates ONLY the given issues against the per-issue rule types
    (`status_duration`, `cycle_time`) — no snapshot, no full-tenant scan, so the
    cost is proportional to the events ingested. `no_activity` (absence-of-event:
    an ingested issue just had activity, so it's not idle), `trend`, and
    `wip_breach` (aggregate / snapshot) are NOT evaluated here — they stay on the
    periodic sweep. Persist-only, like `evaluate_alerts`; dispatch is layered on
    the caller in phase 3."""
    if not issue_ids:
        return []
    if now is None:
        now = utcnow()

    rules = list(
        session.scalars(
            select(AlertRule).where(
                AlertRule.tenant_id == ctx.tenant_id,
                AlertRule.enabled.is_(True),
                AlertRule.type.in_(("status_duration", "cycle_time")),
            )
        )
    )
    if not rules:
        return []

    issues = list(
        session.scalars(
            select(Issue).where(Issue.tenant_id == ctx.tenant_id, Issue.id.in_(issue_ids))
        )
    )
    persisted: list[Alert] = []
    for rule in rules:
        for issue in issues:
            if rule.type == "status_duration":
                events = _evaluate_status_duration(rule, issue, now)
            else:  # cycle_time
                events = _evaluate_cycle_time(rule, issue, now, ctx)
            for event in events:
                stored = _persist(session, ctx.tenant_id, event)
                if stored is not None:
                    persisted.append(stored)

    session.commit()
    return persisted


def upsert_rule(
    session: Session,
    tenant_id: str,
    rule_id: str,
    rule_type: str,
    config: dict[str, Any],
    enabled: bool = True,
) -> AlertRule:
    rule = session.get(AlertRule, (tenant_id, rule_id))
    if rule is None:
        rule = AlertRule(
            tenant_id=tenant_id, id=rule_id, type=rule_type, config=config, enabled=enabled
        )
        session.add(rule)
    else:
        rule.type = rule_type
        rule.config = config
        rule.enabled = enabled
    session.commit()
    return rule
