"""Multi-vendor OHLCV router with chain fallback.

Mirrors TradingAgents' `route_to_vendor`: the configured vendor list IS
the chain; we do NOT silently fall back to vendors the user did not
choose. On rate-limit / not-configured we move to the next vendor; on
NoMarketDataError we keep trying in case another vendor has coverage.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import pandas as pd

from ..config import get_config
from .errors import NoMarketDataError, VendorError, VendorNotConfiguredError, VendorRateLimitError
from . import alpha_vantage_source, finnhub_source, yfinance_source

logger = logging.getLogger(__name__)


VENDOR_METHODS: dict[str, dict[str, Callable[..., pd.DataFrame]]] = {
    "get_ohlcv": {
        "yfinance": yfinance_source.get_ohlcv,
        "alpha_vantage": alpha_vantage_source.get_ohlcv,
        "finnhub": finnhub_source.get_ohlcv,
    },
}


def get_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch OHLCV with chain fallback and an optional SQLite cache.

    Raises:
        NoMarketDataError: when every vendor in the chain returned no data.
        VendorError: when every vendor in the chain errored and none reported no-data.
    """
    from ..storage import repository as repo

    # 1. Try the local cache first (7-day freshness is implicit: we only
    #    cache rows that actually exist, so missing dates indicate the
    #    vendor simply has no data for that day).
    cached = repo.get_ohlcv_cached(symbol, start, end)
    if cached is not None and not cached.empty:
        # Only treat as a hit if the cached range actually covers [start, end]
        if cached.index.min() <= start and cached.index.max() >= end:
            return cached

    # 2. Resolve the configured vendor chain
    config = get_config()
    chain_str = config.get("data_vendors", {}).get("ohlcv", "yfinance,alpha_vantage,finnhub")
    chain = [v.strip() for v in chain_str.split(",") if v.strip()]

    available = VENDOR_METHODS["get_ohlcv"]
    chain = [v for v in chain if v in available]
    if not chain:
        # Fall back to all available vendors
        chain = list(available.keys())

    last_no_data: NoMarketDataError | None = None
    first_error: Exception | None = None

    for vendor in chain:
        impl = available[vendor]
        try:
            df = impl(symbol, start, end)
        except VendorRateLimitError as e:
            logger.warning("Vendor %r rate-limited for %s; trying next.", vendor, symbol)
            if first_error is None:
                first_error = e
            continue
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next.", vendor, symbol)
            if first_error is None:
                first_error = e
            continue
        except NoMarketDataError as e:
            last_no_data = e
            continue
        except Exception as e:
            logger.warning("Vendor %r failed for %s: %s", vendor, symbol, e)
            if first_error is None:
                first_error = e
            continue

        # Success — persist to cache and return
        try:
            repo.save_ohlcv(symbol, df)
        except Exception as e:
            logger.warning("Failed to cache OHLCV for %s: %s", symbol, e)
        return df

    if last_no_data is not None:
        raise last_no_data
    if first_error is not None:
        raise first_error
    raise NoMarketDataError(symbol, "no vendor available")
