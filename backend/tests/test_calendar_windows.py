"""Tests for calendar_windows.

Validates MTD and QTD bucket math against the contract in ADR-0022 / the
The rule: previous window = previous *full* calendar bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.metrics_service import calendar_windows


def _dt(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_mtd_current_window_starts_at_first_of_month() -> None:
    now = _dt(2026, 5, 15, hour=14)
    (cur_s, cur_e), _ = calendar_windows("mtd", now=now)
    assert cur_s == _dt(2026, 5, 1, hour=0)
    assert cur_e == now


def test_mtd_previous_window_is_full_prior_month() -> None:
    now = _dt(2026, 5, 15)
    _, (prev_s, prev_e) = calendar_windows("mtd", now=now)
    assert prev_s == _dt(2026, 4, 1, hour=0)
    assert prev_e == _dt(2026, 5, 1, hour=0)


def test_mtd_january_previous_window_is_december_of_prior_year() -> None:
    now = _dt(2026, 1, 5)
    _, (prev_s, prev_e) = calendar_windows("mtd", now=now)
    assert prev_s == _dt(2025, 12, 1, hour=0)
    assert prev_e == _dt(2026, 1, 1, hour=0)


def test_qtd_q2_window_starts_in_april() -> None:
    """May falls in Q2 → quarter starts April 1."""
    now = _dt(2026, 5, 5)
    (cur_s, cur_e), _ = calendar_windows("qtd", now=now)
    assert cur_s == _dt(2026, 4, 1, hour=0)
    assert cur_e == now


def test_qtd_q2_previous_window_is_full_q1() -> None:
    now = _dt(2026, 5, 5)
    _, (prev_s, prev_e) = calendar_windows("qtd", now=now)
    assert prev_s == _dt(2026, 1, 1, hour=0)
    assert prev_e == _dt(2026, 4, 1, hour=0)


def test_qtd_q1_previous_window_is_q4_of_prior_year() -> None:
    now = _dt(2026, 2, 10)
    (cur_s, _), (prev_s, prev_e) = calendar_windows("qtd", now=now)
    assert cur_s == _dt(2026, 1, 1, hour=0)
    assert prev_s == _dt(2025, 10, 1, hour=0)
    assert prev_e == _dt(2026, 1, 1, hour=0)


def test_qtd_q3_q4_anchors() -> None:
    # Q3 = Jul/Aug/Sep
    now = _dt(2026, 8, 20)
    (cur_s, _), _ = calendar_windows("qtd", now=now)
    assert cur_s == _dt(2026, 7, 1, hour=0)

    # Q4 = Oct/Nov/Dec
    now = _dt(2026, 11, 5)
    (cur_s, _), _ = calendar_windows("qtd", now=now)
    assert cur_s == _dt(2026, 10, 1, hour=0)


def test_unknown_period_raises() -> None:
    with pytest.raises(ValueError):
        calendar_windows("ytd", now=_dt(2026, 5, 1))
