"""Render fired alerts into channel-specific messages (ADR-0037 phase 3).

Copy conventions:
- No sign-off on alerts (the system is the sender, not a person).
- No emoji. Email subjects < 70 chars.
- Per-channel formatting: email plain-text; Slack single-asterisk bold +
  bare URLs; Teams double-asterisk bold + [label](url) links.

The formatter is pure — the dispatch orchestration resolves `base_url`,
`issue_summary`, and `rule_name` and passes them in. Any variable that can't be
computed is omitted gracefully (per the copy doc's null-handling note).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.duration_format import human_duration

_TITLE_MAX = 80

# Friendly metric labels for `trend` alerts.
_METRIC_LABELS = {
    "cycle_time": "cycle time",
    "avg_time": "time",
    "throughput": "throughput",
    "wip": "WIP",
}


@dataclass(frozen=True)
class RenderedMessage:
    subject: str  # email only; webhook channels ignore it
    body: str


def _truncate(title: str | None) -> str | None:
    if not title:
        return None
    return title if len(title) <= _TITLE_MAX else title[: _TITLE_MAX - 1] + "…"


def _ticket_url(base_url: str | None, ticket_key: str | None) -> str | None:
    if not base_url or not ticket_key:
        return None
    return f"{base_url.rstrip('/')}/browse/{ticket_key}"


def _percent_change(change_pct: float | int | None) -> str:
    if change_pct is None:
        return ""
    pct = round(change_pct)
    sign = "+" if pct >= 0 else "−"  # noqa: RUF001 — Unicode minus required per copy doc
    return f"{sign}{abs(pct)}%"


def _metric_human(metric: str | None, status: str | None) -> str:
    label = _METRIC_LABELS.get(metric or "", metric or "metric")
    return f"{status} {label}" if status else f"Project {label}"


def _config_footer_email(rule_type: str) -> str:
    if rule_type == "wip_breach":
        return (
            "You configured this WIP limit in Jira Flow Intelligence — Settings → WIP limits. "
            "Adjust the limit, raise the rule's cooldown, or turn the rule off any time."
        )
    return (
        "You configured this alert in Jira Flow Intelligence — Settings → Alerts. "
        "Adjust the threshold or turn the rule off any time."
    )


def render_alert(
    *,
    rule_type: str,
    channel: str,
    payload: dict[str, Any],
    base_url: str | None,
    issue_summary: str | None,
    project_dashboard_url: str | None,
) -> RenderedMessage:
    """Render one fired alert for one channel. `channel` ∈ {email, slack, teams}."""
    key = payload.get("issue_key")
    title = _truncate(issue_summary)
    url = _ticket_url(base_url, key)
    status = payload.get("status")
    threshold_h = human_duration(payload.get("threshold_seconds"))
    dash = project_dashboard_url or (base_url.rstrip("/") if base_url else "")

    if rule_type == "status_duration":
        dur = human_duration(payload.get("duration_seconds"))
        return _render_per_issue(
            channel,
            subject=f"Jira Flow Intelligence — {key} stuck in {status} for {dur}",
            lead_email=(
                f"{key} — {title} — has been in {status} for {dur}. "
                f"Your threshold for this rule is {threshold_h}."
            ),
            slack=f"*{key}* stuck in *{status}* for {dur} _(threshold {threshold_h})._",
            teams=f"**{key}** stuck in **{status}** for {dur} (threshold {threshold_h}).",
            title=title,
            key=key,
            url=url,
            footer=_config_footer_email(rule_type),
        )

    if rule_type == "cycle_time":
        dur = human_duration(payload.get("elapsed_seconds"))
        return _render_per_issue(
            channel,
            subject=f"Jira Flow Intelligence — {key} exceeded {threshold_h} cycle time",
            lead_email=(
                f"{key} — {title} — has been open for {dur} since it was created. "
                f"Your cycle-time threshold for this rule is {threshold_h}."
            ),
            slack=f"*{key}* open for {dur}, exceeds cycle-time threshold {threshold_h}.",
            teams=f"**{key}** open for {dur}, exceeds cycle-time threshold {threshold_h}.",
            title=title,
            key=key,
            url=url,
            footer=_config_footer_email(rule_type),
        )

    if rule_type == "no_activity":
        dur = human_duration(payload.get("idle_seconds"))
        return _render_per_issue(
            channel,
            subject=f"Jira Flow Intelligence — {key} has had no activity for {dur}",
            lead_email=(
                f"{key} — {title} — has had no status change, comment, or field update "
                f"for {dur}. Your no-activity threshold for this rule is {threshold_h}."
            ),
            slack=f"*{key}* — no activity for {dur} _(threshold {threshold_h})._",
            teams=f"**{key}** — no activity for {dur} (threshold {threshold_h}).",
            title=title,
            key=key,
            url=url,
            footer=_config_footer_email(rule_type),
        )

    if rule_type == "trend":
        metric_h = _metric_human(payload.get("metric"), payload.get("status"))
        direction = payload.get("direction", "")
        pct = _percent_change(payload.get("change_pct"))
        if channel == "email":
            body = (
                f"{metric_h} is {direction}: a {pct} change versus the prior window.\n\n"
                f"Open the bottleneck breakdown in Jira Flow Intelligence: {dash}\n\n"
                f"{_config_footer_email(rule_type)}"
            )
            return RenderedMessage(
                subject=f"Jira Flow Intelligence — {metric_h} {direction} {pct}", body=body
            )
        if channel == "slack":
            return RenderedMessage(
                subject="",
                body=f"*{metric_h}* {direction} by *{pct}* vs prior window.\n{dash}",
            )
        return RenderedMessage(  # teams
            subject="",
            body=f"**{metric_h}** {direction} by **{pct}** vs prior window.\n[Open in Jira Flow Intelligence]({dash})",
        )

    if rule_type == "wip_breach":
        wip = payload.get("current_wip")
        limit = payload.get("limit")
        board = dash  # jira board url not resolvable backend-side in v1; dashboard is the fallback
        if channel == "email":
            body = (
                f"{status} currently has {wip} tickets in flight. "
                f"Your WIP limit for this status is {limit}.\n\n"
                f"Open Jira Flow Intelligence: {board}\n\n"
                f"{_config_footer_email(rule_type)}"
            )
            return RenderedMessage(
                subject=f"Jira Flow Intelligence — {status} WIP is {wip} (limit {limit})", body=body
            )
        if channel == "slack":
            return RenderedMessage(
                subject="",
                body=f"*WIP breach* — *{status}* has *{wip}* tickets in flight, limit *{limit}*.\n{board}",
            )
        return RenderedMessage(  # teams
            subject="",
            body=f"**WIP breach** — **{status}** has **{wip}** tickets in flight, limit **{limit}**.\n[Open Jira Flow Intelligence]({board})",
        )

    # Unknown rule type — fall back to the precomputed plain message.
    msg = str(payload.get("message", "Jira Flow Intelligence alert"))
    return RenderedMessage(subject=f"Jira Flow Intelligence — {msg[:60]}", body=msg)


def _render_per_issue(
    channel: str,
    *,
    subject: str,
    lead_email: str,
    slack: str,
    teams: str,
    title: str | None,
    key: str | None,
    url: str | None,
    footer: str,
) -> RenderedMessage:
    if channel == "email":
        parts = [lead_email]
        if url:
            parts.append(f"Open the ticket in Jira: {url}")
        parts.append(footer)
        return RenderedMessage(subject=subject, body="\n\n".join(parts))
    if channel == "slack":
        lines = [slack]
        if title:
            lines.append(title)
        if url:
            lines.append(url)
        return RenderedMessage(subject="", body="\n".join(lines))
    # teams
    lines = [teams]
    if title:
        lines.append(title)
    if url and key:
        lines.append(f"[{key} in Jira]({url})")
    return RenderedMessage(subject="", body="\n".join(lines))


def render_alert_group(
    *,
    rule_type: str,
    channel: str,
    alerts: list[Any],  # list of Alert ORM rows (typed loosely to avoid import cycle)
    base_url: str | None,
    issue_summaries: dict[str, str | None],
    project_dashboard_url: str | None,
) -> RenderedMessage:
    """Render N alerts for the same rule + destination as ONE message instead of
    N separate ones. Avoids burst-spamming a channel when a single sweep crosses
    threshold on many tickets at once (e.g. the seeded data hitting 7-day cycle
    time simultaneously on 20+ tickets). For N=1 falls back to render_alert.

    Aggregate rules (trend, wip_breach) typically fire once per sweep — for
    those, N>1 just lists the lines compactly."""
    if len(alerts) == 1:
        a = alerts[0]
        return render_alert(
            rule_type=rule_type,
            channel=channel,
            payload=a.payload or {},
            base_url=base_url,
            issue_summary=issue_summaries.get(a.issue_id) if a.issue_id else None,
            project_dashboard_url=project_dashboard_url,
        )
    if rule_type in ("status_duration", "cycle_time", "no_activity"):
        return _render_per_issue_group(rule_type, channel, alerts, base_url, issue_summaries)
    return _render_aggregate_group(rule_type, channel, alerts, base_url, project_dashboard_url)


# Per-rule-type extractors for the grouped per-issue render.
_DURATION_KEY = {
    "status_duration": "duration_seconds",
    "cycle_time": "elapsed_seconds",
    "no_activity": "idle_seconds",
}
_ACTION_WORD = {
    "status_duration": "stuck",
    "cycle_time": "open",
    "no_activity": "idle",
}


def _per_issue_group_header(rule_type: str, n: int, first_payload: dict[str, Any]) -> str:
    threshold_h = human_duration(first_payload.get("threshold_seconds"))
    if rule_type == "status_duration":
        return f"{n} tickets stuck in {first_payload.get('status')} longer than {threshold_h}"
    if rule_type == "cycle_time":
        return f"{n} tickets exceed cycle-time threshold {threshold_h}"
    return f"{n} tickets with no activity for {threshold_h}+"


def _render_per_issue_group(
    rule_type: str,
    channel: str,
    alerts: list[Any],
    base_url: str | None,
    issue_summaries: dict[str, str | None],
) -> RenderedMessage:
    n = len(alerts)
    first_payload = alerts[0].payload or {}
    header = _per_issue_group_header(rule_type, n, first_payload)
    action = _ACTION_WORD[rule_type]
    duration_key = _DURATION_KEY[rule_type]
    threshold_h = human_duration(first_payload.get("threshold_seconds"))

    # Build per-ticket items (key, title, duration, url).
    items: list[dict[str, Any]] = []
    for a in alerts:
        p = a.payload or {}
        key = p.get("issue_key", "?")
        items.append(
            {
                "key": key,
                "title": _truncate(issue_summaries.get(a.issue_id)) if a.issue_id else None,
                "duration": human_duration(p.get(duration_key)),
                "url": _ticket_url(base_url, key),
            }
        )

    if channel == "email":
        lines = [f"{n} tickets {action} longer than your threshold of {threshold_h}:", ""]
        for it in items:
            line = f"- {it['key']}"
            if it["title"]:
                line += f" — {it['title']}"
            line += f" — {action} {it['duration']}"
            if it["url"]:
                line += f"\n  {it['url']}"
            lines.append(line)
        lines.append("")
        lines.append(_config_footer_email(rule_type))
        return RenderedMessage(subject=f"Jira Flow Intelligence — {header}", body="\n".join(lines))

    if channel == "slack":
        lines = [f"*{header}*"]
        for it in items:
            link = f"<{it['url']}|{it['key']}>" if it["url"] else it["key"]
            piece = f"• {link}"
            if it["title"]:
                piece += f" — {it['title']}"
            piece += f" — {action} {it['duration']}"
            lines.append(piece)
        return RenderedMessage(subject="", body="\n".join(lines))

    # teams
    lines = [f"**{header}**"]
    for it in items:
        link = f"[{it['key']}]({it['url']})" if it["url"] else it["key"]
        piece = f"- {link}"
        if it["title"]:
            piece += f" — {it['title']}"
        piece += f" — {action} {it['duration']}"
        lines.append(piece)
    return RenderedMessage(subject="", body="\n".join(lines))


def _render_aggregate_group(
    rule_type: str,
    channel: str,
    alerts: list[Any],
    base_url: str | None,
    project_dashboard_url: str | None,
) -> RenderedMessage:
    """trend / wip_breach grouped — rare; render each line compactly. Subjects
    name the count; bodies list each fire."""
    n = len(alerts)
    dash = project_dashboard_url or (base_url.rstrip("/") if base_url else "")
    label = "trend changes" if rule_type == "trend" else "WIP breaches"
    header = f"{n} {label}"

    def line_for(payload: dict[str, Any]) -> str:
        if rule_type == "trend":
            metric_h = _metric_human(payload.get("metric"), payload.get("status"))
            return (
                f"{metric_h} {payload.get('direction', '')} by "
                f"{_percent_change(payload.get('change_pct'))} vs prior window"
            )
        # wip_breach
        return (
            f"{payload.get('status')}: {payload.get('current_wip')} in flight, "
            f"limit {payload.get('limit')}"
        )

    lines_text = [line_for(a.payload or {}) for a in alerts]

    if channel == "email":
        body = (
            f"{header}:\n\n"
            + "\n".join(f"- {ln}" for ln in lines_text)
            + f"\n\nOpen Jira Flow Intelligence: {dash}"
        )
        return RenderedMessage(subject=f"Jira Flow Intelligence — {header}", body=body)
    if channel == "slack":
        return RenderedMessage(
            subject="",
            body=f"*{header}.*\n" + "\n".join(f"• {ln}" for ln in lines_text) + f"\n{dash}",
        )
    return RenderedMessage(
        subject="",
        body=f"**{header}.**\n"
        + "\n".join(f"- {ln}" for ln in lines_text)
        + f"\n[Open Jira Flow Intelligence]({dash})",
    )


def render_test_message(channel: str, destination_name: str) -> RenderedMessage:
    """The 'Send test' message (ADR-0037 §test variants). Unmistakably a test."""
    if channel == "email":
        return RenderedMessage(
            subject="Jira Flow Intelligence — test message",
            body=(
                "This is a test from Jira Flow Intelligence. You triggered it from "
                "Settings → Alert Destinations → Send test.\n\n"
                f"Destination: {destination_name}\n\n"
                "If you received this, the email destination is configured correctly."
            ),
        )
    if channel == "slack":
        return RenderedMessage(
            subject="",
            body=(
                "*Test message from Jira Flow Intelligence.*\n"
                f"Triggered from Settings → Alert Destinations → Send test for destination *{destination_name}*.\n"
                "If you can see this, the destination is configured correctly."
            ),
        )
    return RenderedMessage(  # teams
        subject="",
        body=(
            "**Test message from Jira Flow Intelligence.**\n"
            f"Triggered from Settings → Alert Destinations → Send test for destination **{destination_name}**.\n"
            "If you can see this, the destination is configured correctly."
        ),
    )
