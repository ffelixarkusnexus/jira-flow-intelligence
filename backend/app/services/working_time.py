"""Working-time arithmetic for ADR-0043.

`working_seconds_between(start, end, schedule)` is the single helper every
duration computation funnels through. When `schedule` is None or its
`enabled` flag is False, the helper returns calendar seconds — bit-for-bit
identical to pre-ADR-0043 behavior, so existing tests don't change.

Algorithm: iterate day-by-day from start to end in the schedule's local
timezone, cap each day to the schedule's [work_start_time, work_end_time]
window, skip non-working days (per the bitmask) and holidays (per the JSON
date list). O(days between start and end) — fine for slice durations
(longest realistic slice is months, not years) and the recompute consumer
processes in batches of 1000 anyway.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db.models import WorkSchedule


def working_seconds_between(
    start: datetime,
    end: datetime,
    schedule: WorkSchedule | None,
) -> int:
    """Returns the working seconds between `start` and `end`.

    If `schedule` is None or `schedule.enabled` is False, returns calendar
    seconds: `int((end - start).total_seconds())`. This is the load-bearing
    backwards-compatibility branch — existing tenants land here and see no
    behavior change.

    The helper is symmetric: `start <= end` produces a non-negative result;
    inverted bounds return 0 rather than a negative duration (slicing service
    already guards against inverted transitions; this is defense-in-depth).
    """
    if start >= end:
        return 0
    if schedule is None or not schedule.enabled:
        return int((end - start).total_seconds())

    tz = ZoneInfo(schedule.timezone or "UTC")
    work_start = _parse_time(schedule.work_start_time)
    work_end = _parse_time(schedule.work_end_time)
    holiday_set = set(schedule.holidays or [])
    mask = schedule.working_days_mask

    # Convert both bounds to schedule-local time. Iteration happens per
    # local calendar day.
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)

    total = 0
    cursor = local_start.date()
    end_date = local_end.date()
    while cursor <= end_date:
        if _is_working_day(cursor, mask, holiday_set):
            day_work_start = datetime.combine(cursor, work_start, tzinfo=tz)
            day_work_end = datetime.combine(cursor, work_end, tzinfo=tz)
            # Window [work_start, work_end] intersected with [start, end].
            window_lo = max(day_work_start, local_start)
            window_hi = min(day_work_end, local_end)
            if window_hi > window_lo:
                total += int((window_hi - window_lo).total_seconds())
        cursor += timedelta(days=1)

    return total


def _parse_time(s: str) -> time:
    """Accept HH:MM or HH:MM:SS (the migration's server_default writes
    HH:MM:SS strings; the Settings UI may save HH:MM)."""
    parts = s.split(":")
    if len(parts) == 2:
        h, m = (int(parts[0]), int(parts[1]))
        return time(h, m)
    h, m, sec = (int(parts[0]), int(parts[1]), int(parts[2]))
    return time(h, m, sec)


_WEEKDAY_BIT = {
    0: 1,  # Monday
    1: 2,  # Tuesday
    2: 4,  # Wednesday
    3: 8,  # Thursday
    4: 16,  # Friday
    5: 32,  # Saturday
    6: 64,  # Sunday
}


def _is_working_day(d: date, mask: int, holiday_set: set[str]) -> bool:
    """A day is a working day if its weekday bit is set in the mask AND it
    is not in the holiday list. Holiday match is by ISO date string in the
    schedule's local timezone — matches what the Settings UI saves."""
    if d.isoformat() in holiday_set:
        return False
    return bool(mask & _WEEKDAY_BIT[d.weekday()])


def utc(dt: datetime) -> datetime:
    """Defensive: ensure a UTC-aware datetime. Used by callers that hold
    onto naive datetimes from older code paths."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
