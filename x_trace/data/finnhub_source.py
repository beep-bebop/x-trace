"""Finnhub OHLCV source (backup, requires API key)."""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime

import pandas as pd
import requests

from ..config import get_config
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

logger = logging.getLogger(__name__)

API_BASE_URL = "https://finnhub.io/api/v1"


def _get_api_key() -> str:
    key = get_config().get("finnhub_api_key") or os.getenv("FINNHUB_API_KEY", "")
    if not key:
        raise VendorNotConfiguredError("FINNHUB_API_KEY is not set")
    return key


def get_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily candles from Finnhub's /stock/candle endpoint."""
    key = _get_api_key()
    params = {
        "symbol": symbol,
        "resolution": "D",
        "from": int(time.mktime(start.timetuple())),
        "to": int(time.mktime(end.timetuple())) + 86_400,  # inclusive
        "token": key,
    }
    try:
        resp = requests.get(f"{API_BASE_URL}/stock/candle", params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise NoMarketDataError(symbol, f"request failed: {e}") from e

    payload = resp.json()
    # Finnhub returns {"s":"no_data"} when the symbol has no rows
    status = payload.get("s", "")
    if status == "no_data":
        raise NoMarketDataError(symbol, f"finnhub returned no_data for {start}..{end}")

    if "c" not in payload or not payload["c"]:
        raise NoMarketDataError(symbol, f"empty candle series")

    # Detect rate-limit markers in error payloads
    if isinstance(payload, dict):
        msg = str(payload.get("error") or "")
        if any(m in msg.lower() for m in ("limit", "premium", "token")):
            raise VendorRateLimitError(msg)

    df = pd.DataFrame(
        {
            "Date": pd.to_datetime(payload["t"], unit="s").date,
            "Open": payload["o"],
            "High": payload["h"],
            "Low": payload["l"],
            "Close": payload["c"],
            "Volume": payload.get("v", [0] * len(payload["c"])),
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
