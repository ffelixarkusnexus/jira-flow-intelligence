"""Coverage for `app.services.duration_format.human_duration` — the shared
seconds→human-readable formatter used by both the in-product alerts list
and the outbound Slack/Teams/email messages. Locks the format so the
in-product display can't drift back to raw `604800s`-style output that
the user explicitly objected to seeing in alert bodies.
"""

from __future__ import annotations

import pytest

from app.services.duration_format import human_duration


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        # None and empty: no-value sentinel, callers omit the slot.
        (None, ""),
        # Sub-minute → minutes (rounded down to 0 for <60s).
        (0, "0 minutes"),
        (1, "0 minutes"),
        (59, "0 minutes"),
        # Plural / singular agreement.
        (60, "1 minute"),
        (120, "2 minutes"),
        (1800, "30 minutes"),
        # Hours kick in at 3600s; minutes collapse (granularity decision).
        (3600, "1 hour"),
        (7200, "2 hours"),
        (3660, "1 hour"),  # 1h 1m → just "1 hour"
        # Days kick in at 86400s; hours show when nonzero.
        (86400, "1 day"),
        (172800, "2 days"),
        (90000, "1 day, 1 hour"),
        # The actual numbers from the user's overview-tab bug report,
        # which previously rendered as raw seconds:
        (604800, "7 days"),  # 7-day cycle threshold
        (625910, "7 days, 5 hours"),  # ticket elapsed past 7d threshold
        (259200, "3 days"),  # 72h no-activity threshold
        (345203, "3 days, 23 hours"),  # ticket idle past 72h
        (2385949, "27 days, 14 hours"),  # SCRUM-1 idle (worst case in the bug)
        # Float inputs (from time-delta calculations) coerce via int().
        (3600.5, "1 hour"),
        (60.9, "1 minute"),
    ],
)
def test_human_duration(seconds: float | int | None, expected: str) -> None:
    assert human_duration(seconds) == expected


def test_human_duration_minutes_only_when_under_an_hour() -> None:
    """Sanity: the minutes slot is suppressed when days OR hours are present,
    so the format collapses to the two most-relevant units."""
    # 1 day 1 hour 1 minute = 86400 + 3600 + 60 = 90060
    assert human_duration(90060) == "1 day, 1 hour"
    # 1 hour 30 minutes = 5400
    assert human_duration(5400) == "1 hour"
