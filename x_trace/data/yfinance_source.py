"""yfinance OHLCV source (primary, no API key required)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from ..config import get_config
from .errors import NoMarketDataError, VendorRateLimitError

logger = logging.getLogger(__name__)

# Redirect yfinance's internal SQLite cache (TZ / cookie / ISIN) to the
# project-local state dir. The default location is in AppData\Local which
# the Trae sandbox blocks, causing "unable to open database file" errors.
_CACHE_DIR = Path(get_config()["state_dir"]) / "yfinance-cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
yf.set_tz_cache_location(str(_CACHE_DIR))


def get_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily OHLCV from yfinance.

    yfinance treats ``end`` as EXCLUSIVE, so we add one day to make the
    requested range inclusive (mirrors TradingAgents' ``get_YFin_data_online``).
    Returns a DataFrame indexed by date with columns
    ``Open, High, Low, Close, Volume``.
    """
    start_str = start.strftime("%Y-%m-%d")
    end_inclusive = (end + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_str, end=end_inclusive, auto_adjust=False)
    except Exception as e:
        msg = str(e).lower()
        if "rate" in msg or "429" in msg:
            raise VendorRateLimitError(f"yfinance rate-limited: {e}")
        raise

    if df is None or df.empty:
        raise NoMarketDataError(symbol, f"no rows between {start} and {end}")

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Normalize column names and keep only what we need
    df = df.rename(columns=str.capitalize)
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].copy()

    # Drop rows with NaN Close (yfinance returns NaN for today/pre-market)
    if "Close" in df.columns:
        before = len(df)
        df = df.dropna(subset=["Close"])
        if before != len(df):
            logger.debug("Dropped %d NaN-close rows for %s", before - len(df), symbol)

    if df.empty:
        raise NoMarketDataError(symbol, f"all rows had NaN close between {start} and {end}")

    # Round prices to 2 decimals
    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            df[col] = df[col].round(4)

    return df
