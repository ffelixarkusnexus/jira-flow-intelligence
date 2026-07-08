"""ADR-0043 working-time math tests.

Load-bearing case: Friday 5PM → Monday 9AM under a Mon-Fri 9-5 schedule
returns ZERO working seconds. If that ever regresses, the entire bottleneck-
card "honors business hours" claim is back to being a lie."""

from __future__ import annotations

from datetime import datetime

from app.db.models import WorkSchedule
from app.services.working_time import working_seconds_between


def _schedule(
    *,
    timezone: str = "UTC",
    working_days_mask: int = 31,  # Mon-Fri
    work_start: str = "09:00:00",
    work_end: str = "17:00:00",
    holidays: list[str] | None = None,
    enabled: bool = True,
) -> WorkSchedule:
    s = WorkSchedule()
    s.tenant_id = "t"
    s.name = "default"
    s.timezone = timezone
    s.working_days_mask = working_days_mask
    s.work_start_time = work_start
    s.work_end_time = work_end
    s.holidays = holidays or []
    s.enabled = enabled
    return s


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_calendar_fallback_when_no_schedule():
    """schedule=None returns the integer calendar-seconds difference."""
    start = _dt("2026-06-05T17:00:00Z")  # Fri 17:00 UTC
    end = _dt("2026-06-08T09:00:00Z")  # Mon 09:00 UTC
    # 2 days + 16 hours = 230400 seconds
    assert working_seconds_between(start, end, None) == 230_400


def test_calendar_fallback_when_schedule_disabled():
    """An existing schedule row with enabled=False also falls through to
    calendar math — operator disabling is bit-for-bit reversible."""
    schedule = _schedule(enabled=False)
    start = _dt("2026-06-05T17:00:00Z")
    end = _dt("2026-06-08T09:00:00Z")
    assert working_seconds_between(start, end, schedule) == 230_400


def test_same_day_inside_work_window():
    """Mon 10:00 to Mon 11:00 = 3600 working seconds."""
    schedule = _schedule()
    start = _dt("2026-06-08T10:00:00Z")  # Mon
    end = _dt("2026-06-08T11:00:00Z")
    assert working_seconds_between(start, end, schedule) == 3600


def test_friday_5pm_to_monday_9am_returns_zero():
    """LOAD-BEARING. The single most important regression guard for ADR-0043."""
    schedule = _schedule()
    start = _dt("2026-06-05T17:00:00Z")  # Fri 17:00 (end of working window)
    end = _dt("2026-06-08T09:00:00Z")  # Mon 09:00 (start of working window)
    assert working_seconds_between(start, end, schedule) == 0


def test_weekend_skipped():
    """Wed 10:00 to Mon 11:00. Wed: 7h, Thu+Fri: 16h, Mon: 2h = 25 working hours."""
    schedule = _schedule()
    start = _dt("2026-06-03T10:00:00Z")  # Wed
    end = _dt("2026-06-08T11:00:00Z")  # Mon
    expected = 25 * 3600
    assert working_seconds_between(start, end, schedule) == expected


def test_holiday_skipped():
    """A holiday in the middle of the range is excluded from working time."""
    schedule = _schedule(holidays=["2026-06-04"])  # Thursday is a holiday
    # Wed 10:00 to Fri 11:00: Wed 7h + Thu (skipped) + Fri 2h = 9h
    start = _dt("2026-06-03T10:00:00Z")
    end = _dt("2026-06-05T11:00:00Z")
    assert working_seconds_between(start, end, schedule) == 9 * 3600


def test_timezone_correctness():
    """Schedule in Europe/Madrid. A UTC range that spans Madrid's evening
    cutoff should drop the after-hours portion correctly."""
    schedule = _schedule(timezone="Europe/Madrid")
    # 2026-06-08 (Mon) 14:00 UTC = 16:00 Madrid (1h of work left)
    # 2026-06-08 (Mon) 16:00 UTC = 18:00 Madrid (1h past close)
    start = _dt("2026-06-08T14:00:00Z")
    end = _dt("2026-06-08T16:00:00Z")
    # Only 14:00→15:00 UTC counts (16:00→17:00 Madrid). 1h.
    assert working_seconds_between(start, end, schedule) == 3600


def test_exact_boundary_start_of_work():
    """Mon 09:00 to Mon 09:00 = 0; Mon 09:00 to Mon 10:00 = 3600."""
    schedule = _schedule()
    t0 = _dt("2026-06-08T09:00:00Z")
    t1 = _dt("2026-06-08T10:00:00Z")
    assert working_seconds_between(t0, t0, schedule) == 0
    assert working_seconds_between(t0, t1, schedule) == 3600


def test_inverted_bounds_returns_zero():
    """Defense-in-depth: callers that pass end < start get 0, not a
    negative number that downstream callers would have to special-case."""
    schedule = _schedule()
    start = _dt("2026-06-08T11:00:00Z")
    end = _dt("2026-06-08T10:00:00Z")
    assert working_seconds_between(start, end, schedule) == 0
