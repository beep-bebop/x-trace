"""Tests for analyzer schemas and tweet → prediction row conversion."""
from __future__ import annotations

from x_trace.analyzer.schemas import (
    Direction,
    PredictionType,
    StockPrediction,
    TweetAnalysis,
)
from x_trace.analyzer.tweet_analyzer import _to_prediction_rows
from x_trace.storage.models import TweetRow


def _make_tweet(tweet_id: str = "123", text: str = "Bullish on AAPL, target $200") -> TweetRow:
    from datetime import datetime
    return TweetRow(
        tweet_id=tweet_id,
        username="test_user",
        text=text,
        created_at=datetime(2025, 6, 15, 12, 0, 0),
    )


def test_stock_prediction_validates_required_fields():
    pred = StockPrediction(
        ticker="aapl",
        direction=Direction.BULLISH,
        prediction_type=PredictionType.TARGET_PRICE,
        target_price=200.0,
        confidence="high",
        rationale="target $200",
    )
    assert pred.ticker == "aapl"  # not uppercased at schema level
    assert pred.target_price == 200.0
    assert pred.entry_price is None


def test_stock_prediction_nullish_float_coerced_to_none():
    pred = StockPrediction(
        ticker="AAPL",
        direction=Direction.BULLISH,
        prediction_type=PredictionType.TARGET_PRICE,
        target_price="N/A",
        entry_price="-",
        stop_loss="tbd",
        confidence="medium",
        rationale="...",
    )
    assert pred.target_price is None
    assert pred.entry_price is None
    assert pred.stop_loss is None


def test_tweet_analysis_with_no_predictions():
    a = TweetAnalysis(predictions=[], has_no_stock_mention=True, summary="No stocks.")
    assert a.has_no_stock_mention is True
    assert a.predictions == []


def test_tweet_analysis_with_multiple_predictions():
    a = TweetAnalysis(
        predictions=[
            StockPrediction(
                ticker="AAPL",
                direction=Direction.BULLISH,
                prediction_type=PredictionType.DIRECTIONAL,
                confidence="medium",
                rationale="...",
            ),
            StockPrediction(
                ticker="TSLA",
                direction=Direction.BEARISH,
                prediction_type=PredictionType.TARGET_PRICE,
                target_price=250.0,
                confidence="high",
                rationale="...",
            ),
        ],
        has_no_stock_mention=False,
        summary="Two picks.",
    )
    assert len(a.predictions) == 2
    assert a.predictions[1].target_price == 250.0


def test_to_prediction_rows_assigns_sequential_ids():
    tweet = _make_tweet()
    analysis = TweetAnalysis(
        predictions=[
            StockPrediction(
                ticker="aapl",
                direction=Direction.BULLISH,
                prediction_type=PredictionType.DIRECTIONAL,
                confidence="medium",
                rationale="...",
            ),
            StockPrediction(
                ticker="tsla",
                direction=Direction.BEARISH,
                prediction_type=PredictionType.DIRECTIONAL,
                confidence="low",
                rationale="...",
            ),
        ],
        has_no_stock_mention=False,
        summary="Two picks.",
    )
    rows = _to_prediction_rows(tweet, analysis)
    assert len(rows) == 2
    assert rows[0].prediction_id == "123#0"
    assert rows[1].prediction_id == "123#1"
    assert rows[0].ticker == "AAPL"
    assert rows[1].ticker == "TSLA"
    assert rows[0].direction == "bullish"
    assert rows[1].direction == "bearish"
