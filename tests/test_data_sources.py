"""Tests for the data interface: yfinance source + multi-vendor fallback."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from x_trace.data import interface as data_iface
from x_trace.data import yfinance_source
from x_trace.data.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError


def _sample_df(start: date, end: date) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="B")  # business days
    return pd.DataFrame(
        {
            "Open": 100.0,
            "High": 105.0,
            "Low": 95.0,
            "Close": 102.0,
            "Volume": 1_000_000.0,
        },
        index=dates,
    )


def test_yfinance_get_ohlcv_returns_normalized_columns(monkeypatch):
    """yfinance source should return Open/High/Low/Close/Volume columns and
    strip timezone info from the index."""
    sample = _sample_df(date(2025, 1, 2), date(2025, 1, 31))
    # Simulate tz-aware index (yfinance returns UTC)
    sample.index = sample.index.tz_localize("UTC")

    class FakeTicker:
        def history(self, **kwargs):
            return sample

    monkeypatch.setattr(yfinance_source.yf, "Ticker", lambda symbol: FakeTicker())

    df = yfinance_source.get_ohlcv("AAPL", date(2025, 1, 2), date(2025, 1, 31))
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.tz is None
    assert (df["Close"] == 102.0).all()


def test_yfinance_raises_no_market_data_on_empty(monkeypatch):
    empty = pd.DataFrame()

    class FakeTicker:
        def history(self, **kwargs):
            return empty

    monkeypatch.setattr(yfinance_source.yf, "Ticker", lambda symbol: FakeTicker())
    with pytest.raises(NoMarketDataError):
        yfinance_source.get_ohlcv("BAD", date(2025, 1, 2), date(2025, 1, 31))


def test_interface_falls_back_to_alpha_vantage_when_yfinance_rate_limited(
    monkeypatch, isolated_state_dir
):
    """When yfinance raises VendorRateLimitError, the interface should move
    on to the next vendor in the configured chain."""
    sample = _sample_df(date(2025, 1, 2), date(2025, 1, 31))

    def yf_fail(*a, **kw):
        raise VendorRateLimitError("yfinance 429")

    # Patch the whole VENDOR_METHODS dict so we don't need API keys
    monkeypatch.setattr(
        data_iface,
        "VENDOR_METHODS",
        {
            "get_ohlcv": {
                "yfinance": yf_fail,
                "alpha_vantage": lambda symbol, start, end: sample,
            }
        },
    )

    df = data_iface.get_ohlcv("AAPL", date(2025, 1, 2), date(2025, 1, 31))
    assert not df.empty
    assert (df["Close"] == 102.0).all()


def test_interface_uses_cache_on_second_call(monkeypatch, isolated_state_dir):
    """A successful fetch is cached; the second call should not re-invoke
    the vendor implementation."""
    sample = _sample_df(date(2025, 1, 2), date(2025, 1, 31))
    call_count = {"n": 0}

    def impl(symbol, start, end):
        call_count["n"] += 1
        return sample.copy()

    monkeypatch.setattr(
        data_iface,
        "VENDOR_METHODS",
        {"get_ohlcv": {"yfinance": impl}},
    )

    df1 = data_iface.get_ohlcv("AAPL", date(2025, 1, 2), date(2025, 1, 31))
    assert call_count["n"] == 1
    df2 = data_iface.get_ohlcv("AAPL", date(2025, 1, 2), date(2025, 1, 31))
    # The cache should serve the second call without invoking the vendor again
    assert call_count["n"] == 1
    assert df2.shape == df1.shape


def test_interface_raises_no_market_data_when_all_vendors_empty(
    monkeypatch, isolated_state_dir
):
    def empty(symbol, start, end):
        raise NoMarketDataError(symbol, "empty")

    monkeypatch.setattr(
        data_iface,
        "VENDOR_METHODS",
        {"get_ohlcv": {"yfinance": empty, "alpha_vantage": empty, "finnhub": empty}},
    )

    with pytest.raises(NoMarketDataError):
        data_iface.get_ohlcv("NOPE", date(2025, 1, 2), date(2025, 1, 31))
