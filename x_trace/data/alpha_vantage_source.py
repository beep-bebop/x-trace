"""Alpha Vantage OHLCV source (backup, requires API key)."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from io import StringIO

import pandas as pd
import requests

from ..config import get_config
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

logger = logging.getLogger(__name__)

API_BASE_URL = "https://www.alphavantage.co/query"


def _get_api_key() -> str:
    key = get_config().get("alpha_vantage_api_key") or os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if not key:
        raise VendorNotConfiguredError("ALPHA_VANTAGE_API_KEY is not set")
    return key


def get_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily OHLCV from Alpha Vantage TIME_SERIES_DAILY."""
    key = _get_api_key()
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "full",
        "datatype": "csv",
        "apikey": key,
    }
    try:
        resp = requests.get(API_BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise NoMarketDataError(symbol, f"request failed: {e}") from e

    text = resp.text
    # Alpha Vantage returns JSON with an error key when rate-limited
    if text.lstrip().startswith("{"):
        payload = resp.json()
        info = payload.get("Information") or payload.get("Note") or ""
        if "rate" in info.lower() or "limit" in info.lower():
            raise VendorRateLimitError(f"Alpha Vantage: {info}")
        if "Error Message" in payload:
            raise NoMarketDataError(symbol, payload["Error Message"])
        raise NoMarketDataError(symbol, f"unexpected payload: {payload}")

    df = pd.read_csv(StringIO(text))
    if df.empty:
        raise NoMarketDataError(symbol, "empty CSV")

    # Columns: timestamp, open, high, low, close, volume
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.date
    df = df.rename(
        columns={
            "timestamp": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    df = df[(df["Date"] >= start) & (df["Date"] <= end)]
    df = df.sort_values("Date").set_index("Date")

    if df.empty:
        raise NoMarketDataError(symbol, f"no rows between {start} and {end}")

    for col in ["Open", "High", "Low", "Close"]:
        df[col] = df[col].astype(float).round(4)
    df["Volume"] = df["Volume"].astype(float)

    return df[["Open", "High", "Low", "Close", "Volume"]]
