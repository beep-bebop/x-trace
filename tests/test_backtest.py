"""Tests for backtest window parsing and resolution."""
from __future__ import annotations

from datetime import date

import pytest

from x_trace.backtest.windows import Window, parse_windows, resolve_window


def test_parse_windows_from_string():
    wins = parse_windows("1m,3m")
    assert wins == [Window.ONE_MONTH, Window.THREE_MONTHS]


def test_parse_windows_from_list():
    wins = parse_windows(["1w", "to_date"])
    assert wins == [Window.ONE_WEEK, Window.TO_DATE]


def test_parse_windows_strips_whitespace():
    wins = parse_windows(" 1m , 3m ,")
    assert wins == [Window.ONE_MONTH, Window.THREE_MONTHS]


def test_parse_windows_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown window"):
        parse_windows("1m,5y")


def test_resolve_window_one_week():
    post = date(2025, 1, 10)
    start, end = resolve_window(post, Window.ONE_WEEK)
    assert start == post
    assert end == date(2025, 1, 17)


def test_resolve_window_one_month():
    post = date(2025, 1, 10)
    start, end = resolve_window(post, Window.ONE_MONTH)
    assert start == post
    assert end == date(2025, 2, 9)


def test_resolve_window_three_months():
    post = date(2025, 1, 10)
    start, end = resolve_window(post, Window.THREE_MONTHS)
    assert start == post
    assert end == date(2025, 4, 10)


def test_resolve_window_to_date_uses_today_default():
    post = date(2025, 1, 10)
    start, end = resolve_window(post, Window.TO_DATE)
    assert start == post
    assert end == date.today()


def test_resolve_window_to_date_accepts_today():
    post = date(2025, 1, 10)
    today = date(2025, 6, 30)
    start, end = resolve_window(post, Window.TO_DATE, today=today)
    assert start == post
    assert end == today
