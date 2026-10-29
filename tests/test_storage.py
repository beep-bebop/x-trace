"""Tests for the storage repository: CRUD + idempotent upserts."""
from __future__ import annotations

import json
from datetime import date, datetime

from x_trace.storage import repository as repo
from x_trace.storage.models import BacktestResultRow, OhlcvCacheRow, PredictionRow, TweetRow


def test_upsert_tweet_is_idempotent(isolated_state_dir):
    tw = TweetRow(
        tweet_id="t1",
        username="alice",
        text="hello",
        created_at=datetime(2025, 6, 15, 12, 0, 0),
        like_count=10,
    )
    repo.upsert_tweet(tw)
    # Same id, different like_count → should update, not duplicate
    tw.like_count = 25
    repo.upsert_tweet(tw)

    fetched = repo.get_tweet("t1")
    assert fetched is not None
    assert fetched.like_count == 25
    assert fetched.text == "hello"


def test_get_tweets_by_user_filters_by_date(isolated_state_dir):
    repo.upsert_tweet(
        TweetRow(
            tweet_id="t1",
            username="alice",
            text="old",
            created_at=datetime(2025, 1, 1, 0, 0, 0),
        )
    )
    repo.upsert_tweet(
        TweetRow(
            tweet_id="t2",
            username="alice",
            text="new",
            created_at=datetime(2025, 6, 1, 0, 0, 0),
        )
    )
    repo.upsert_tweet(
        TweetRow(
            tweet_id="t3",
            username="bob",
            text="other",
            created_at=datetime(2025, 6, 1, 0, 0, 0),
        )
    )

    # Alice's tweets in May-June 2025 only
    out = repo.get_tweets_by_user(
        "alice",
        start=datetime(2025, 5, 1),
        end=datetime(2025, 6, 30),
    )
    assert len(out) == 1
    assert out[0].tweet_id == "t2"


def test_upsert_prediction_is_idempotent(isolated_state_dir):
    p = PredictionRow(
        prediction_id="t1#0",
        tweet_id="t1",
        ticker="AAPL",
        direction="bullish",
        prediction_type="directional",
        confidence="medium",
        rationale="...",
    )
    repo.upsert_prediction(p)
    p.confidence = "high"
    repo.upsert_prediction(p)

    fetched = repo.get_predictions_for_tweet("t1")
    assert len(fetched) == 1
    assert fetched[0].confidence == "high"


def test_get_unanalyzed_tweet_ids(isolated_state_dir):
    repo.upsert_tweet(
        TweetRow(
            tweet_id="t1",
            username="alice",
            text="bullish AAPL",
            created_at=datetime(2025, 6, 1, 0, 0, 0),
        )
    )
    repo.upsert_tweet(
        TweetRow(
            tweet_id="t2",
            username="alice",
            text="no stocks here",
            created_at=datetime(2025, 6, 2, 0, 0, 0),
        )
    )
    # t1 has a prediction, t2 does not
    repo.upsert_prediction(
        PredictionRow(
            prediction_id="t1#0",
            tweet_id="t1",
            ticker="AAPL",
            direction="bullish",
            prediction_type="directional",
            confidence="medium",
            rationale="...",
        )
    )

    unanalyzed = repo.get_unanalyzed_tweet_ids("alice")
    assert unanalyzed == ["t2"]


def test_upsert_backtest_result_is_idempotent(isolated_state_dir):
    r = BacktestResultRow(
        result_id="p1#1m",
        prediction_id="p1",
        window="1m",
        base_date=date(2025, 1, 2),
        end_date=date(2025, 2, 2),
        base_close=100.0,
        end_close=105.0,
        return_pct=5.0,
        max_drawdown_pct=-2.0,
        max_runup_pct=6.0,
        direction_correct=True,
    )
    repo.upsert_backtest_result(r)
    r.return_pct = 7.5
    repo.upsert_backtest_result(r)

    # Should not duplicate
    out = repo.get_backtest_results_for_user("alice")
    assert len(out) == 0  # no tweet/prediction linking this result


def test_ohlcv_cache_roundtrip(isolated_state_dir):
    import pandas as pd

    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [95.0, 96.0],
            "Close": [102.0, 103.0],
            "Volume": [1_000_000.0, 1_100_000.0],
        },
        index=[date(2025, 1, 2), date(2025, 1, 3)],
    )
    repo.save_ohlcv("AAPL", df)

    cached = repo.get_ohlcv_cached("AAPL", date(2025, 1, 2), date(2025, 1, 3))
    assert cached is not None
    assert len(cached) == 2
    assert (cached["Close"] == [102.0, 103.0]).all()
