"""Errors shared by all market-data vendors."""
from __future__ import annotations


class VendorError(Exception):
    """Base class for vendor-related errors."""


class NoMarketDataError(VendorError):
    """Symbol returned no rows (invalid, delisted, or out of range)."""

    def __init__(self, symbol: str, detail: str = ""):
        self.symbol = symbol
        self.detail = detail
        super().__init__(f"No market data for {symbol!r}: {detail}" if detail else f"No market data for {symbol!r}")


class VendorRateLimitError(VendorError):
    """Vendor returned a rate-limit response (429 or equivalent)."""


class VendorNotConfiguredError(VendorError):
    """Vendor requires an API key that is not set."""
