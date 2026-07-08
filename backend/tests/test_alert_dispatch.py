from datetime import datetime, timedelta

from app.core.tenant_context import TenantContext
from app.db.models import (
    Alert,
    AlertDeliveryDestination,
    AlertFire,
    AlertRule,
    AlertRuleDestination,
    Issue,
)
from app.services import alert_dispatch
from app.services.alert_messages import render_alert
from app.services.webhook_dispatcher import DispatchResult


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _dest(session, ctx, dest_id, dest_type, config, *, default=True):
    d = AlertDeliveryDestination(
        tenant_id=ctx.tenant_id,
        id=dest_id,
        type=dest_type,
        name=dest_id,
        config=config,
        status="active",
        is_tenant_default=default,
        created_at=_dt("2026-01-01T00:00:00Z"),
    )
    session.add(d)
    session.flush()
    return d


def _alert(session, ctx, *, rule_id="r1", rule_type="status_duration"):
    a = Alert(
        tenant_id=ctx.tenant_id,
        rule_id=rule_id,
        rule_type=rule_type,
        issue_id="10001",
        status="Review",
        key=f"{rule_id}|k",
        triggered_at=_dt("2026-01-04T00:00:00Z"),
        payload={
            "issue_key": "ABC-1",
            "status": "Review",
            "duration_seconds": 259200,
            "threshold_seconds": 14400,
        },
    )
    session.add(a)
    session.flush()
    return a


# ----- rendering -----------------------------------------------------------


def test_render_status_duration_email_has_no_signoff():
    msg = render_alert(
        rule_type="status_duration",
        channel="email",
        payload={
            "issue_key": "ABC-1",
            "status": "Review",
            "duration_seconds": 259200,
            "threshold_seconds": 14400,
        },
        base_url="https://acme.atlassian.net",
        issue_summary="Implement OAuth login flow",
        project_dashboard_url=None,
    )
    assert len(msg.subject) < 70
    assert "Review" in msg.subject
    assert "https://acme.atlassian.net/browse/ABC-1" in msg.body
    assert "Settings → Alerts" in msg.body
    # No sign-off on alerts (the system is the sender, not a person).
    assert "Jira Flow Intelligence team" not in msg.body


def test_render_trend_uses_unicode_minus_for_negative():
    msg = render_alert(
        rule_type="trend",
        channel="slack",
        payload={
            "metric": "throughput",
            "status": None,
            "change_pct": -18,
            "direction": "worsening",
        },
        base_url="https://acme.atlassian.net",
        issue_summary=None,
        project_dashboard_url=None,
    )
    assert "−18%" in msg.body  # noqa: RUF001 — asserting the Unicode minus is used


# ----- dispatch ------------------------------------------------------------


def test_dispatch_email_records_delivered(session, ctx: TenantContext, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        alert_dispatch.resend_service,
        "send_alert_email",
        lambda **kw: sent.append(kw) or True,
    )
    _dest(session, ctx, "d-email", "email", {"address": "ops@acme.com"})
    alert = _alert(session, ctx)

    delivered = alert_dispatch.dispatch_alerts(
        session, ctx, [alert], now=_dt("2026-01-04T00:00:00Z")
    )

    assert delivered == 1
    assert sent
    assert sent[0]["to"] == "ops@acme.com"
    fires = list(session.scalars(select_fires(ctx)))
    assert len(fires) == 1
    assert fires[0].status == "delivered"
    assert fires[0].destination_id == "d-email"


def test_dispatch_cooldown_skips_second_fire(session, ctx: TenantContext, monkeypatch):
    monkeypatch.setattr(alert_dispatch.resend_service, "send_alert_email", lambda **kw: True)
    _dest(session, ctx, "d-email", "email", {"address": "ops@acme.com"})
    alert = _alert(session, ctx)
    now = _dt("2026-01-04T00:00:00Z")

    first = alert_dispatch.dispatch_alerts(session, ctx, [alert], now=now)
    second = alert_dispatch.dispatch_alerts(session, ctx, [alert], now=now + timedelta(minutes=10))

    assert first == 1
    assert second == 0  # within the 1h default cooldown
    fires = list(session.scalars(select_fires(ctx)))
    statuses = sorted(f.status for f in fires)
    assert statuses == ["delivered", "skipped_cooldown"]


def test_dispatch_webhook_failure_recorded(session, ctx: TenantContext, monkeypatch):
    monkeypatch.setattr(
        alert_dispatch,
        "post_webhook",
        lambda url, text, **kw: DispatchResult(ok=False, detail="HTTP 404: no_team"),
    )
    _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    alert = _alert(session, ctx)

    delivered = alert_dispatch.dispatch_alerts(
        session, ctx, [alert], now=_dt("2026-01-04T00:00:00Z")
    )

    assert delivered == 0
    fires = list(session.scalars(select_fires(ctx)))
    assert len(fires) == 1
    assert fires[0].status == "failed"
    assert "404" in (fires[0].detail or "")


def test_explicit_binding_overrides_tenant_default(session, ctx: TenantContext, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        alert_dispatch.resend_service,
        "send_alert_email",
        lambda **kw: calls.append(kw["to"]) or True,
    )
    # A tenant-default email destination + a non-default one bound to the rule.
    _dest(session, ctx, "d-default", "email", {"address": "default@acme.com"}, default=True)
    _dest(session, ctx, "d-bound", "email", {"address": "bound@acme.com"}, default=False)
    session.add(
        AlertRule(tenant_id=ctx.tenant_id, id="r1", type="status_duration", config={}, enabled=True)
    )
    session.flush()
    session.add(
        AlertRuleDestination(tenant_id=ctx.tenant_id, alert_rule_id="r1", destination_id="d-bound")
    )
    session.flush()
    alert = _alert(session, ctx, rule_id="r1")

    alert_dispatch.dispatch_alerts(session, ctx, [alert], now=_dt("2026-01-04T00:00:00Z"))

    # Explicit binding wins — only the bound destination is used, not the default.
    assert calls == ["bound@acme.com"]


def test_auto_pause_after_consecutive_failures(session, ctx: TenantContext, monkeypatch):
    monkeypatch.setattr(
        alert_dispatch,
        "post_webhook",
        lambda url, text, **kw: DispatchResult(ok=False, detail="HTTP 404"),
    )
    dest = _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    alert = _alert(session, ctx)
    base = _dt("2026-01-04T00:00:00Z")

    for i in range(5):
        alert_dispatch.dispatch_alerts(session, ctx, [alert], now=base + timedelta(minutes=i))

    session.refresh(dest)
    assert dest.status == "disabled"  # paused after 5 consecutive failures


def _failed_fire(session, ctx, dest_id, fired_at):
    session.add(
        AlertFire(
            tenant_id=ctx.tenant_id,
            alert_rule_id="r1",
            destination_id=dest_id,
            fired_at=fired_at,
            status="failed",
            detail="HTTP 404: no_team",
        )
    )


def test_failure_digest_sends_when_due(session, ctx: TenantContext, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        alert_dispatch.resend_service,
        "send_failure_digest_email",
        lambda **kw: captured.update(kw) or True,
    )
    ctx.tenant.admin_contact_email = "admin@acme.com"
    _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    now = _dt("2026-01-04T00:00:00Z")
    for i in range(3):
        _failed_fire(session, ctx, "d-slack", now - timedelta(hours=i))
    session.flush()

    sent = alert_dispatch.send_failure_digest_if_due(session, ctx, now=now)

    assert sent is True
    assert captured["failure_count"] == 3
    assert ctx.tenant.last_failure_digest_at == now


def test_failure_digest_skips_when_recently_sent(session, ctx: TenantContext, monkeypatch):
    monkeypatch.setattr(
        alert_dispatch.resend_service, "send_failure_digest_email", lambda **kw: True
    )
    ctx.tenant.admin_contact_email = "admin@acme.com"
    now = _dt("2026-01-04T00:00:00Z")
    ctx.tenant.last_failure_digest_at = now - timedelta(hours=2)  # within 24h
    _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    _failed_fire(session, ctx, "d-slack", now)
    session.flush()

    assert alert_dispatch.send_failure_digest_if_due(session, ctx, now=now) is False


def test_failure_digest_skips_when_no_failures(session, ctx: TenantContext, monkeypatch):
    monkeypatch.setattr(
        alert_dispatch.resend_service, "send_failure_digest_email", lambda **kw: True
    )
    ctx.tenant.admin_contact_email = "admin@acme.com"
    session.flush()
    assert (
        alert_dispatch.send_failure_digest_if_due(session, ctx, now=_dt("2026-01-04T00:00:00Z"))
        is False
    )


# ----- destination CRUD + test-send ----------------------------------------


def test_upsert_list_destination_masks_webhook(session, ctx: TenantContext):
    from app.services import alert_destinations

    alert_destinations.upsert_destination(
        session,
        ctx.tenant_id,
        dest_id="d1",
        dtype="slack",
        name="#eng-alerts",
        config={"webhook_url": "https://hooks.slack.com/services/T/B/secrettoken"},
        is_tenant_default=True,
        status="active",
    )
    pairs = alert_destinations.list_destinations(session, ctx.tenant_id)
    assert len(pairs) == 1
    dest, fails = pairs[0]
    assert fails == 0
    masked = alert_destinations.mask_config(dest.type, dest.config)
    assert masked["webhook_url_set"] is True
    assert "secrettoken" not in str(masked)  # full URL not echoed


def test_set_and_get_rule_destinations(session, ctx: TenantContext):
    from app.services import alert_destinations

    session.add(
        AlertRule(tenant_id=ctx.tenant_id, id="r1", type="status_duration", config={}, enabled=True)
    )
    for did in ("d1", "d2"):
        _dest(session, ctx, did, "email", {"address": f"{did}@acme.com"}, default=False)
    session.flush()

    alert_destinations.set_rule_destinations(session, ctx.tenant_id, "r1", ["d1", "d2"])
    assert set(alert_destinations.get_rule_destination_ids(session, ctx.tenant_id, "r1")) == {
        "d1",
        "d2",
    }
    # Replace semantics: setting a new list drops the old bindings.
    alert_destinations.set_rule_destinations(session, ctx.tenant_id, "r1", ["d2"])
    assert alert_destinations.get_rule_destination_ids(session, ctx.tenant_id, "r1") == ["d2"]


def test_send_test_to_destination_stamps_status(session, ctx: TenantContext, monkeypatch):
    monkeypatch.setattr(alert_dispatch.resend_service, "send_alert_email", lambda **kw: True)
    dest = _dest(session, ctx, "d1", "email", {"address": "ops@acme.com"}, default=False)
    ok = alert_dispatch.send_test_to_destination(session, dest)
    assert ok is True
    assert dest.last_test_status == "ok"
    assert dest.last_test_at is not None


def test_delete_destination(session, ctx: TenantContext):
    from app.services import alert_destinations

    _dest(session, ctx, "d1", "email", {"address": "ops@acme.com"}, default=False)
    assert alert_destinations.delete_destination(session, ctx.tenant_id, "d1") is True
    assert alert_destinations.delete_destination(session, ctx.tenant_id, "d1") is False
    assert alert_destinations.list_destinations(session, ctx.tenant_id) == []


# ----- grouping (ADR-0037 v1 multi-alert batching) -------------------------


def _alert_for_issue(session, ctx, issue_id, key_suffix, summary):
    """Insert an Issue + an Alert for the cycle_time rule. Used to build N
    distinct alerts that will all bucket under the same (rule, dest)."""
    issue = Issue(
        tenant_id=ctx.tenant_id,
        id=issue_id,
        key=f"DEMO-{issue_id}",
        created_at=_dt("2026-01-01T00:00:00Z"),
        updated_at=_dt("2026-01-04T00:00:00Z"),
        current_status="In Progress",
        summary=summary,
    )
    session.add(issue)
    session.flush()
    a = Alert(
        tenant_id=ctx.tenant_id,
        rule_id="cycle-7d",
        rule_type="cycle_time",
        issue_id=issue_id,
        status="In Progress",
        key=f"{issue_id}|cycle",
        triggered_at=_dt("2026-01-04T00:00:00Z"),
        payload={
            "issue_key": f"DEMO-{issue_id}",
            "elapsed_seconds": 700_000,  # ~8d
            "threshold_seconds": 604_800,  # 7d
        },
    )
    session.add(a)
    session.flush()
    return a


def test_dispatch_groups_same_rule_dest_into_one_message(session, ctx: TenantContext, monkeypatch):
    """5 cycle_time alerts on different tickets for the same destination should
    dispatch as ONE message (not 5). All 5 record as delivered fires."""
    sent_bodies: list[str] = []
    monkeypatch.setattr(
        alert_dispatch,
        "post_webhook",
        lambda url, text, **kw: sent_bodies.append(text) or DispatchResult(ok=True),
    )
    _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    alerts = [
        _alert_for_issue(session, ctx, f"100{i:02d}", str(i), f"ticket {i}") for i in range(5)
    ]
    now = _dt("2026-01-04T00:00:00Z")

    delivered = alert_dispatch.dispatch_alerts(session, ctx, alerts, now=now)

    assert delivered == 5  # 5 alerts reached the destination
    assert len(sent_bodies) == 1  # ...via ONE grouped message
    body = sent_bodies[0]
    assert "5 tickets" in body
    # Each ticket key appears in the grouped body.
    for i in range(5):
        assert f"DEMO-100{i:02d}" in body
    fires = list(session.scalars(select_fires(ctx)))
    assert len(fires) == 5
    assert all(f.status == "delivered" for f in fires)


def test_dispatch_groups_separate_buckets_for_different_rules(
    session, ctx: TenantContext, monkeypatch
):
    """Different rule_ids on the same destination get separate buckets =
    separate messages."""
    sent_bodies: list[str] = []
    monkeypatch.setattr(
        alert_dispatch,
        "post_webhook",
        lambda url, text, **kw: sent_bodies.append(text) or DispatchResult(ok=True),
    )
    _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    a1 = _alert(session, ctx, rule_id="r-a")
    a2 = _alert(session, ctx, rule_id="r-b")
    a2.issue_id = "10002"  # avoid Alert PK collision via uniqueness constraint
    a2.key = "r-b|k"
    session.flush()

    alert_dispatch.dispatch_alerts(session, ctx, [a1, a2], now=_dt("2026-01-04T00:00:00Z"))

    assert len(sent_bodies) == 2  # one message per rule


def test_dispatch_cooldown_skips_whole_bucket(session, ctx: TenantContext, monkeypatch):
    """When a bucket's (rule, dest) is in cooldown, ALL alerts in the bucket
    record skipped_cooldown — nothing dispatches."""
    sent: list[str] = []
    monkeypatch.setattr(
        alert_dispatch,
        "post_webhook",
        lambda url, text, **kw: sent.append(text) or DispatchResult(ok=True),
    )
    _dest(session, ctx, "d-slack", "slack", {"webhook_url": "https://hooks.slack.com/x"})
    now = _dt("2026-01-04T00:00:00Z")
    # Seed a recent delivered fire so cooldown engages.
    session.add(
        AlertFire(
            tenant_id=ctx.tenant_id,
            alert_rule_id="cycle-7d",
            destination_id="d-slack",
            fired_at=now - timedelta(minutes=5),
            status="delivered",
            detail=None,
        )
    )
    session.flush()
    alerts = [
        _alert_for_issue(session, ctx, f"200{i:02d}", str(i), f"ticket {i}") for i in range(3)
    ]

    alert_dispatch.dispatch_alerts(session, ctx, alerts, now=now)

    assert sent == []  # nothing dispatched
    fires_now = list(
        session.scalars(
            select_fires(ctx).where(AlertFire.fired_at == now)  # type: ignore[attr-defined]
        )
    )
    assert len(fires_now) == 3
    assert all(f.status == "skipped_cooldown" for f in fires_now)


# ----- webhook payload shape (per-channel) ---------------------------------


class _FakeResp:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


def test_post_webhook_slack_sends_plain_text(monkeypatch):
    """Slack incoming webhook accepts the simple {text: ...} payload."""
    from app.services import webhook_dispatcher

    captured: dict = {}
    monkeypatch.setattr(
        webhook_dispatcher.httpx,
        "post",
        lambda url, json, timeout: captured.update({"url": url, "json": json}) or _FakeResp(),
    )
    res = webhook_dispatcher.post_webhook(
        "https://hooks.slack.com/x", "hello", channel_type="slack"
    )
    assert res.ok
    assert captured["json"] == {"text": "hello"}


def test_post_webhook_teams_sends_adaptive_card(monkeypatch):
    """Teams Workflows webhooks require Adaptive Card or MessageCard format —
    a plain {text: ...} payload times out / is rejected (verified 2026-06-01
    against the Workflows in-app help text). This guards against re-introducing
    the bare-text payload for Teams."""
    from app.services import webhook_dispatcher

    captured: dict = {}
    monkeypatch.setattr(
        webhook_dispatcher.httpx,
        "post",
        lambda url, json, timeout: captured.update({"url": url, "json": json}) or _FakeResp(),
    )
    res = webhook_dispatcher.post_webhook(
        "https://default.webhook.office.com/x", "hello", channel_type="teams"
    )
    assert res.ok
    p = captured["json"]
    assert p["type"] == "message"
    assert p["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = p["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"][0]["text"] == "hello"


def select_fires(ctx: TenantContext):
    from sqlalchemy import select

    return select(AlertFire).where(AlertFire.tenant_id == ctx.tenant_id)
