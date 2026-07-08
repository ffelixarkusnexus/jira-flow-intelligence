"""Shared helpers for rendering durations as human-readable strings.

Raw seconds appear in our domain everywhere (Jira changelog elapsed time,
cycle-time thresholds, no-activity windows, WIP breach minutes). Anywhere
a duration crosses into user-facing text — in-product alerts list, outbound
Slack/Teams/email messages, dashboard tooltips — it must be rendered in
days/hours/minutes, never as "604800s". Both producers and consumers route
through this module so the format stays consistent.
"""

from __future__ import annotations


def human_duration(seconds: float | int | None) -> str:
    """Render a duration in seconds as `"N days, M hours"` (or minutes
    when sub-hour). Granularity collapses to the larger units when present
    — "3 days, 5 hours" omits minutes because the minute precision isn't
    decision-relevant at multi-day scale.

    `None` becomes an empty string (callers can detect "no value" and
    omit the slot entirely rather than rendering a stray placeholder).
    """
    if seconds is None:
        return ""
    s = int(seconds)
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if not days and not hours:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts)
