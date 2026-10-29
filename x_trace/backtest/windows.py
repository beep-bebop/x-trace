"""Backtest window definitions and resolution."""
from __future__ import annotations

from datetime import date, timedelta
from enum import Enum


class Window(str, Enum):
    ONE_WEEK = "1w"
    ONE_MONTH = "1m"
    THREE_MONTHS = "3m"
    TO_DATE = "to_date"


def parse_windows(spec: list[str] | str) -> list[Window]:
    """Parse a list of window shorthand strings into Window enums.

    Accepts either a list (``["1m", "3m"]``) or a comma-separated string
    (``"1m,3m"``). Unknown values raise ``ValueError``.
    """
    if isinstance(spec, str):
        tokens = [s.strip() for s in spec.split(",") if s.strip()]
    else:
        tokens = [s.strip() for s in spec if s.strip()]
    out: list[Window] = []
    for tok in tokens:
        try:
            out.append(Window(tok))
        except ValueError as e:
            raise ValueError(f"Unknown window {tok!r}. Valid: {[w.value for w in Window]}") from e
    return out


def resolve_window(post_date: date, window: Window, today: date | None = None) -> tuple[date, date]:
    """Return the (start, end) date range for a backtest window.

    ``start`` is always ``post_date`` (the trading day on or after the post).
    ``end`` depends on the window:
    - 1w → post_date + 7 days
    - 1m → post_date + 30 days
    - 3m → post_date + 90 days
    - to_date → today (defaults to date.today())
    """
    if today is None:
        today = date.today()
    if window == Window.ONE_WEEK:
        return post_date, post_date + timedelta(days=7)
    if window == Window.ONE_MONTH:
        return post_date, post_date + timedelta(days=30)
    if window == Window.THREE_MONTHS:
        return post_date, post_date + timedelta(days=90)
    if window == Window.TO_DATE:
        return post_date, today
    raise ValueError(f"Unknown window: {window}")
