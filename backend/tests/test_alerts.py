from datetime import datetime

from sqlalchemy import select

from app.core.tenant_context import TenantContext
from app.db.models import Alert, AlertRule, Issue, TimeSlice
from app.services.alert_service import (
    evaluate_alerts,
    evaluate_issue_alerts,
    rule_tier,
    upsert_rule,
)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _seed_issue(session, ctx: TenantContext, key: str = "ABC-123"):
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="10001",
        key=key,
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-04T00:00:00Z"),
        current_status="Review",
    )
    session.add(issue)
    session.flush()

    session.add(
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="10001",
            status="Review",
            start_at=_dt("2026-01-01T00:00:00Z"),
            end_at=_dt("2026-01-04T00:00:00Z"),
            duration_seconds=3 * 86400,
            is_open=True,
        )
    )
    session.commit()
    return issue


def test_status_duration_rule_triggers_when_threshold_exceeded(session, ctx):
    _seed_issue(session, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-48h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 48 * 3600},
    )

    triggered = evaluate_alerts(session, ctx, now=_dt("2026-01-04T00:00:00Z"))
    assert len(triggered) == 1
    assert triggered[0].rule_id == "review-48h"
    assert triggered[0].status == "Review"
    assert triggered[0].tenant_id == ctx.tenant_id


def test_rule_tier_classifies_by_threshold():
    """ADR-0037: cadence tier is derived from each rule's effective threshold."""

    def r(rtype: str, cfg: dict) -> AlertRule:
        return AlertRule(tenant_id="t", id="x", type=rtype, config=cfg, enabled=True)

    assert rule_tier(r("status_duration", {"threshold_seconds": 4 * 3600})) == "hourly"
    assert rule_tier(r("status_duration", {"threshold_seconds": 48 * 3600})) == "daily"
    assert rule_tier(r("cycle_time", {"threshold_seconds": 14 * 86400})) == "daily"
    assert rule_tier(r("no_activity", {"threshold_seconds": 7 * 86400})) == "daily"
    assert rule_tier(r("wip_breach", {"breach_minutes": 30})) == "hourly"
    assert rule_tier(r("wip_breach", {"breach_minutes": 2000})) == "daily"  # 2000m = 120000s
    assert rule_tier(r("trend", {"threshold_pct": 30})) == "daily"  # window-based → always daily


def test_evaluate_alerts_tier_filter(session, ctx):
    """ADR-0037: the hourly sweep evaluates only sub-24h-threshold rules; the
    daily sweep only the rest. Same issue violates both rules; each tier fires
    exactly its own."""
    _seed_issue(session, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-4h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 4 * 3600},
    )
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-48h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 48 * 3600},
    )
    now = _dt("2026-01-04T00:00:00Z")  # 3 days in status — both thresholds exceeded

    hourly = evaluate_alerts(session, ctx, now=now, tier="hourly")
    assert {a.rule_id for a in hourly} == {"review-4h"}

    daily = evaluate_alerts(session, ctx, now=now, tier="daily")
    assert {a.rule_id for a in daily} == {"review-48h"}


def test_evaluate_issue_alerts_targets_given_issues(session, ctx):
    """ADR-0037 ticket-event path: per-issue eval fires the status_duration rule
    for the targeted issue; empty / unknown id sets fire nothing."""
    _seed_issue(session, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-4h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 4 * 3600},
    )
    now = _dt("2026-01-04T00:00:00Z")

    fired = evaluate_issue_alerts(session, ctx, ["10001"], now=now)
    assert {a.rule_id for a in fired} == {"review-4h"}

    assert evaluate_issue_alerts(session, ctx, [], now=now) == []
    assert evaluate_issue_alerts(session, ctx, ["99999"], now=now) == []


def test_evaluate_issue_alerts_excludes_no_activity(session, ctx):
    """no_activity is absence-of-event, so the ticket-event path must NOT
    evaluate it (only status_duration / cycle_time). With only a no_activity
    rule configured, the per-issue path fires nothing even when the issue is
    well past the idle threshold."""
    _seed_issue(session, ctx)  # updated_at 2026-01-04
    upsert_rule(session, ctx.tenant_id, "idle-1d", "no_activity", {"threshold_seconds": 86400})
    now = _dt("2026-01-10T00:00:00Z")  # 6 days idle — would fire on the sweep, not here
    assert evaluate_issue_alerts(session, ctx, ["10001"], now=now) == []


def test_alerts_are_idempotent(session, ctx):
    _seed_issue(session, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-48h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 48 * 3600},
    )

    now = _dt("2026-01-04T00:00:00Z")
    first = evaluate_alerts(session, ctx, now=now)
    second = evaluate_alerts(session, ctx, now=now)
    assert len(first) == 1
    assert len(second) == 0  # no duplicates


def test_cycle_time_rule(session, ctx):
    _seed_issue(session, ctx)
    upsert_rule(session, ctx.tenant_id, "cycle-2d", "cycle_time", {"threshold_seconds": 2 * 86400})
    triggered = evaluate_alerts(session, ctx, now=_dt("2026-01-04T00:00:00Z"))
    assert any(a.rule_id == "cycle-2d" for a in triggered)


def test_status_duration_does_not_fire_below_threshold(session, ctx):
    _seed_issue(session, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-1w",
        "status_duration",
        {"status": "Review", "threshold_seconds": 7 * 86400},
    )
    triggered = evaluate_alerts(session, ctx, now=_dt("2026-01-04T00:00:00Z"))
    assert all(a.rule_id != "review-1w" for a in triggered)


# ----- wip_breach -----------------------------------------------------------


def _build_snapshot(status: str, wip_avg: float):
    """Mock WindowSnapshot for wip_breach evaluation."""
    from app.services.metrics_service import StatusWindowResult, WindowSnapshot

    win_s = _dt("2026-01-01T00:00:00Z")
    win_e = _dt("2026-01-02T00:00:00Z")
    snap = WindowSnapshot(window_start=win_s, window_end=win_e)
    snap.statuses[status] = StatusWindowResult(
        status=status,
        window_start=win_s,
        window_end=win_e,
        avg_seconds=0,
        p50_seconds=0,
        p90_seconds=0,
        wip_avg=wip_avg,
        throughput=0,
        sample_size=0,
    )
    return snap


def test_wip_breach_fires_when_wip_over_limit(session, ctx):
    from app.services.wip_limits_service import upsert_wip_limit

    upsert_wip_limit(
        session, ctx.tenant_id, None, "Code Review", max_in_progress=3, breach_minutes=60
    )
    upsert_rule(session, ctx.tenant_id, "cr-breach", "wip_breach", {"status": "Code Review"})
    snap = _build_snapshot("Code Review", wip_avg=5.2)
    triggered = evaluate_alerts(
        session, ctx, now=_dt("2026-01-02T00:00:00Z"), current_snapshot=snap
    )
    assert len(triggered) == 1
    assert triggered[0].rule_id == "cr-breach"
    assert triggered[0].status == "Code Review"
    assert triggered[0].payload["limit"] == 3
    assert triggered[0].payload["current_wip"] == 5.2


def test_wip_breach_skipped_when_under_limit(session, ctx):
    from app.services.wip_limits_service import upsert_wip_limit

    upsert_wip_limit(
        session, ctx.tenant_id, None, "Code Review", max_in_progress=5, breach_minutes=60
    )
    upsert_rule(session, ctx.tenant_id, "cr-breach", "wip_breach", {"status": "Code Review"})
    snap = _build_snapshot("Code Review", wip_avg=2.0)
    triggered = evaluate_alerts(
        session, ctx, now=_dt("2026-01-02T00:00:00Z"), current_snapshot=snap
    )
    assert all(a.rule_id != "cr-breach" for a in triggered)


def test_wip_breach_disabled_when_breach_minutes_zero(session, ctx):
    from app.services.wip_limits_service import upsert_wip_limit

    upsert_wip_limit(
        session, ctx.tenant_id, None, "Code Review", max_in_progress=3, breach_minutes=0
    )
    upsert_rule(session, ctx.tenant_id, "cr-breach", "wip_breach", {"status": "Code Review"})
    snap = _build_snapshot("Code Review", wip_avg=5.0)
    triggered = evaluate_alerts(
        session, ctx, now=_dt("2026-01-02T00:00:00Z"), current_snapshot=snap
    )
    # breach_minutes=0 = visual indicator only; no alert.
    assert all(a.rule_id != "cr-breach" for a in triggered)


def test_wip_breach_idempotent_within_window(session, ctx):
    from app.services.wip_limits_service import upsert_wip_limit

    upsert_wip_limit(
        session, ctx.tenant_id, None, "Code Review", max_in_progress=3, breach_minutes=60
    )
    upsert_rule(session, ctx.tenant_id, "cr-breach", "wip_breach", {"status": "Code Review"})
    snap = _build_snapshot("Code Review", wip_avg=5.0)
    first = evaluate_alerts(session, ctx, now=_dt("2026-01-02T00:00:00Z"), current_snapshot=snap)
    second = evaluate_alerts(session, ctx, now=_dt("2026-01-02T00:00:01Z"), current_snapshot=snap)
    assert len(first) == 1
    # Same snapshot window → same idempotency key → no duplicate.
    assert all(a.rule_id != "cr-breach" for a in second)


# ----- ADR-0041: state-based alert re-fire daily bucket --------------------
#
# These tests prove the unified principle: state-based ticket-level rules
# (`cycle_time`, `status_duration`, `no_activity`) fire at most once per UTC
# day per breaching condition. A perpetually-stuck ticket re-fires daily
# until it stops breaching, instead of firing once on first breach and then
# going silent (the ADR-0008 behavior that caused the operational gap).


def _alert_keys(session, ctx) -> list[str]:
    return list(
        session.scalars(
            select(Alert.key).where(Alert.tenant_id == ctx.tenant_id).order_by(Alert.id)
        )
    )


def test_cycle_time_refires_next_utc_day(session, ctx):
    """Same ticket past threshold on two consecutive UTC days produces two
    distinct alert rows, one per UTC date bucket. This is the load-bearing
    test that proves the supersession of ADR-0008."""
    _seed_issue(session, ctx)
    upsert_rule(session, ctx.tenant_id, "cycle-2d", "cycle_time", {"threshold_seconds": 2 * 86400})

    # T0: 2026-06-10 23:55 UTC — first day past threshold
    t0 = _dt("2026-06-10T23:55:00Z")
    first = evaluate_alerts(session, ctx, now=t0)
    # T1: 2026-06-11 00:05 UTC — 10 minutes later but next UTC day
    t1 = _dt("2026-06-11T00:05:00Z")
    second = evaluate_alerts(session, ctx, now=t1)

    assert len(first) == 1
    assert len(second) == 1
    keys = _alert_keys(session, ctx)
    assert keys == ["10001|cycle|2026-06-10", "10001|cycle|2026-06-11"]


def test_cycle_time_does_not_refire_same_utc_day(session, ctx):
    """Two evaluations within the same UTC day produce exactly one alert row
    (the daily-bucket idempotency holds within the day)."""
    _seed_issue(session, ctx)
    upsert_rule(session, ctx.tenant_id, "cycle-2d", "cycle_time", {"threshold_seconds": 2 * 86400})

    first = evaluate_alerts(session, ctx, now=_dt("2026-06-10T08:00:00Z"))
    second = evaluate_alerts(session, ctx, now=_dt("2026-06-10T20:00:00Z"))

    assert len(first) == 1
    assert len(second) == 0
    assert _alert_keys(session, ctx) == ["10001|cycle|2026-06-10"]


def test_status_duration_refires_next_utc_day(session, ctx):
    """Same UTC-day-boundary re-fire pattern for status_duration."""
    _seed_issue(session, ctx)
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-48h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 48 * 3600},
    )

    first = evaluate_alerts(session, ctx, now=_dt("2026-06-10T23:55:00Z"))
    second = evaluate_alerts(session, ctx, now=_dt("2026-06-11T00:05:00Z"))

    assert len(first) == 1
    assert len(second) == 1
    keys = _alert_keys(session, ctx)
    assert keys == [
        "10001|status_duration|Review|2026-06-10",
        "10001|status_duration|Review|2026-06-11",
    ]


def test_no_activity_refires_next_utc_day(session, ctx):
    """Same UTC-day-boundary re-fire pattern for no_activity.

    This is the case ADR-0008's last_event-timestamp key failed: an idle
    ticket *cannot* advance its last_event timestamp by definition, so the
    old key never re-fired. The daily bucket makes idle tickets re-surface."""
    _seed_issue(session, ctx)  # updated_at 2026-01-04
    upsert_rule(session, ctx.tenant_id, "idle-1d", "no_activity", {"threshold_seconds": 86400})

    first = evaluate_alerts(session, ctx, now=_dt("2026-06-10T23:55:00Z"))
    second = evaluate_alerts(session, ctx, now=_dt("2026-06-11T00:05:00Z"))

    assert len(first) == 1
    assert len(second) == 1
    keys = _alert_keys(session, ctx)
    assert keys == [
        "10001|no_activity|2026-06-10",
        "10001|no_activity|2026-06-11",
    ]


def test_ticket_that_stops_breaching_mid_day_does_not_fire(session, ctx):
    """If a ticket's status_duration drops below threshold mid-day (because
    the slice closed when the ticket moved status), no alert fires on the
    next evaluation that day — the daily bucket only matters when the
    breaching condition is actually present."""
    # Seed a closed slice: ticket was in Review for 1h, then left.
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id="10002",
        key="ABC-002",
        created_at=_dt("2026-06-10T08:00:00Z"),
        updated_at=_dt("2026-06-10T09:00:00Z"),
        current_status="Done",
    )
    session.add(issue)
    session.flush()
    session.add(
        TimeSlice(
            tenant_id=ctx.tenant_id,
            issue_id="10002",
            status="Review",
            start_at=_dt("2026-06-10T08:00:00Z"),
            end_at=_dt("2026-06-10T09:00:00Z"),
            duration_seconds=3600,  # 1h — below 48h threshold
            is_open=False,
        )
    )
    session.commit()
    upsert_rule(
        session,
        ctx.tenant_id,
        "review-48h",
        "status_duration",
        {"status": "Review", "threshold_seconds": 48 * 3600},
    )

    fired = evaluate_alerts(session, ctx, now=_dt("2026-06-10T20:00:00Z"))
    assert fired == []
    assert _alert_keys(session, ctx) == []


def test_legacy_adr0008_alert_row_does_not_collide_with_new_daily_bucket(session, ctx):
    """Historical alert rows written under ADR-0008's `{issue.id}|cycle` key
    must not block ADR-0041's `{issue.id}|cycle|{date}` key, and vice versa.
    On the first evaluation after deploy, a previously-stuck ticket fires a
    fresh daily-bucketed alert — re-surfacing the breach to operators."""
    issue = _seed_issue(session, ctx)
    # Simulate a pre-migration alert row with the old key shape.
    session.add(
        Alert(
            tenant_id=ctx.tenant_id,
            rule_id="cycle-2d",
            rule_type="cycle_time",
            issue_id=issue.id,
            status="Review",
            key=f"{issue.id}|cycle",  # ADR-0008 shape — no date bucket
            triggered_at=_dt("2026-04-01T00:00:00Z"),
            payload={"legacy": True},
        )
    )
    session.commit()
    upsert_rule(session, ctx.tenant_id, "cycle-2d", "cycle_time", {"threshold_seconds": 2 * 86400})

    fired = evaluate_alerts(session, ctx, now=_dt("2026-06-10T12:00:00Z"))
    assert len(fired) == 1
    keys = _alert_keys(session, ctx)
    # Legacy row preserved; new daily-bucketed row added alongside it.
    assert keys == [f"{issue.id}|cycle", f"{issue.id}|cycle|2026-06-10"]


def test_perpetually_stuck_ticket_yields_one_row_per_utc_day(session, ctx):
    """Integration: simulate seven consecutive daily evaluations on a stuck
    cycle_time-breaching ticket. Expect exactly one alert row per UTC day —
    the unified daily-bucket principle applied end-to-end."""
    _seed_issue(session, ctx)
    upsert_rule(session, ctx.tenant_id, "cycle-2d", "cycle_time", {"threshold_seconds": 2 * 86400})

    days = [f"2026-06-{day:02d}" for day in range(10, 17)]  # 7 consecutive days
    for day in days:
        evaluate_alerts(session, ctx, now=_dt(f"{day}T08:00:00Z"))

    keys = _alert_keys(session, ctx)
    assert keys == [f"10001|cycle|{day}" for day in days]
    assert len(keys) == 7
