"""Pydantic schemas for LLM-extracted stock predictions.

Mirrors the pattern in TradingAgents' `tradingagents/agents/schemas.py`:
- Enums for closed-set fields (direction, prediction_type, confidence)
- Field descriptions double as the model's output instructions
- `field_validator` coerces nullish strings to None for optional floats
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}


def _coerce_optional_float(value):
    if isinstance(value, str) and value.strip().lower() in _NULLISH_FLOAT:
        return None
    return value


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class PredictionType(str, Enum):
    TARGET_PRICE = "target_price"        # explicit price target
    DIRECTIONAL = "directional"          # bullish/bearish call, no target
    ENTRY_STOP = "entry_stop"            # entry + stop-loss levels
    HOLD = "hold"                        # hold/wait
    WATCH = "watch"                      # on the watchlist, no clear call
    NEUTRAL_MENTION = "neutral_mention"  # mentions but expresses no view


class StockPrediction(BaseModel):
    """A single stock prediction extracted from a tweet.

    A tweet may mention multiple stocks; each becomes its own prediction.
    """

    ticker: str = Field(
        description="Normalized stock ticker symbol in uppercase, e.g. AAPL / TSLA / NVDA"
    )
    company_name: str | None = Field(
        default=None,
        description="Optional full company name if mentioned, e.g. Apple Inc.",
    )
    direction: Direction = Field(
        description=(
            "The blogger's directional view on the ticker. "
            "bullish = expects price to rise; "
            "bearish = expects price to fall; "
            "neutral = no clear directional view (use with HOLD/WATCH/NEUTRAL_MENTION)."
        ),
    )
    prediction_type: PredictionType = Field(
        description=(
            "The form of the prediction. Use: "
            "target_price when an explicit price target is given; "
            "directional for a plain bullish/bearish call; "
            "entry_stop when entry and stop-loss levels are given; "
            "hold when recommending to hold/wait; "
            "watch when adding to a watchlist without a directional call; "
            "neutral_mention when the stock is mentioned without any view."
        ),
    )
    target_price: float | None = Field(
        default=None,
        description="Explicit target price in the instrument's quote currency, if stated.",
    )
    entry_price: float | None = Field(
        default=None,
        description="Suggested entry price, if stated.",
    )
    stop_loss: float | None = Field(
        default=None,
        description="Suggested stop-loss price, if stated.",
    )
    time_horizon: str | None = Field(
        default=None,
        description=(
            "Stated time horizon for the prediction, e.g. '1 month', "
            "'end of year', 'Q3'. None if not stated."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Inferred confidence based on tone, specificity, and hedge words. "
            "high = explicit target/entry with strong language; "
            "medium = clear directional view without hard levels; "
            "low = vague, hedged, or speculative phrasing."
        ),
    )
    rationale: str = Field(
        description="One or two sentences quoting the part of the tweet that supports this prediction.",
    )

    @field_validator("target_price", "entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


class TweetAnalysis(BaseModel):
    """The LLM's structured analysis of one tweet."""

    predictions: list[StockPrediction] = Field(
        default_factory=list,
        description="List of stock predictions extracted from this tweet. Empty if the tweet mentions no stocks.",
    )
    has_no_stock_mention: bool = Field(
        default=False,
        description="True when the tweet mentions no stocks at all.",
    )
    summary: str = Field(
        description="One-sentence summary of what the tweet says about stocks, if anything.",
    )
