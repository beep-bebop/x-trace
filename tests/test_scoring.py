"""Tests for scoring metrics with known backtest result sets."""
from __future__ import annotations

from datetime import date, datetime

from x_trace.scoring import metrics as M
from x_trace.scoring.scorer import _grade_for, score_blogger
from x_trace.storage import repository as repo
from x_trace.storage.models import BacktestResultRow, PredictionRow, TweetRow


def _make_pred(
    prediction_id: str,
    tweet_id: str,
    ticker: str = "AAPL",
    direction: str = "bullish",
    prediction_type: str = "directional",
    target_price: float | None = None,
    confidence: str = "medium",
) -> PredictionRow:
    return PredictionRow(
        prediction_id=prediction_id,
        tweet_id=tweet_id,
        ticker=ticker,
        company_name=None,
        direction=direction,
        prediction_type=prediction_type,
        target_price=target_price,
        entry_price=None,
        stop_loss=None,
        time_horizon=None,
        confidence=confidence,
        rationale="test",
    )


def _make_result(
    prediction_id: str,
    window: str = "1m",
    return_pct: float = 5.0,
    direction_correct: bool = True,
    hit_target: bool | None = None,
    base_close: float = 100.0,
    end_close: float = 105.0,
) -> BacktestResultRow:
    return BacktestResultRow(
        result_id=f"{prediction_id}#{window}",
        prediction_id=prediction_id,
        window=window,
        base_date=date(2025, 1, 2),
        end_date=date(2025, 2, 2),
        base_close=base_close,
        end_close=end_close,
        return_pct=return_pct,
        max_drawdown_pct=-2.0,
        max_runup_pct=6.0,
        direction_correct=direction_correct,
        hit_target=hit_target,
        spy_return_pct=2.0,
        alpha_pct=return_pct - 2.0,
    )


def test_hit_rate_all_correct():
    preds = {
        "p1": _make_pred("p1", "t1", direction="bullish"),
        "p2": _make_pred("p2", "t1", direction="bearish"),
    }
    results = [
        _make_result("p1", return_pct=5.0, direction_correct=True),
        _make_result("p2", return_pct=-3.0, direction_correct=True),
    ]
    r = M.hit_rate(results, preds, weight=0.3)
    assert r.raw_value == 1.0
    assert r.normalized_score == 100.0
    assert r.sample_size == 2


def test_hit_rate_half_correct():
    preds = {
        "p1": _make_pred("p1", "t1", direction="bullish"),
        "p2": _make_pred("p2", "t1", direction="bullish"),
    }
    results = [
        _make_result("p1", return_pct=5.0, direction_correct=True),
        _make_result("p2", return_pct=-3.0, direction_correct=False),
    ]
    r = M.hit_rate(results, preds, weight=0.3)
    assert r.raw_value == 0.5
    assert r.normalized_score == 50.0


def test_avg_return_aligned_for_bearish():
    """A bearish call is 'correct' when return is negative; aligned return
    is -r so a -5% return becomes +5% aligned."""
    preds = {
        "p1": _make_pred("p1", "t1", direction="bearish"),
    }
    results = [_make_result("p1", return_pct=-5.0, direction_correct=True, end_close=95.0)]
    r = M.avg_return(results, preds, weight=0.2)
    assert r.raw_value == 5.0  # aligned
    assert r.normalized_score == 75.0  # 50 + 5*5


def test_target_accuracy_zero_when_target_far_away():
    preds = {
        "p1": _make_pred("p1", "t1", target_price=200.0, prediction_type="target_price"),
    }
    results = [_make_result("p1", base_close=100.0, end_close=100.0)]
    r = M.target_accuracy(results, preds, weight=0.15)
    # rel_err = |200-100|/100 = 1.0; acc = max(0, 1-1) = 0
    assert r.raw_value == 0.0
    assert r.normalized_score == 0.0


def test_target_accuracy_perfect_when_target_equals_end():
    preds = {
        "p1": _make_pred("p1", "t1", target_price=110.0, prediction_type="target_price"),
    }
    results = [_make_result("p1", base_close=100.0, end_close=110.0)]
    r = M.target_accuracy(results, preds, weight=0.15)
    assert r.raw_value == 1.0
    assert r.normalized_score == 100.0


def test_coverage_breadth_scales_logarithmically():
    preds = {
        f"p{i}": _make_pred(f"p{i}", "t1", ticker=f"T{i}") for i in range(5)
    }
    results = [_make_result(f"p{i}") for i in range(5)]
    r = M.coverage_breadth(results, preds, weight=0.1)
    assert r.raw_value == 5.0
    # log(6)/log(21) * 100
    import math
    expected = math.log(6) / math.log(21) * 100
    assert abs(r.normalized_score - expected) < 0.01


def test_confidence_calibration_positive_when_high_better():
    preds = {
        "p1": _make_pred("p1", "t1", confidence="high"),
        "p2": _make_pred("p2", "t1", confidence="high"),
        "p3": _make_pred("p3", "t1", confidence="low"),
        "p4": _make_pred("p4", "t1", confidence="low"),
    }
    # high bucket: 2/2 correct; low bucket: 1/2 correct → delta = 0.5
    results = [
        _make_result("p1", direction_correct=True),
        _make_result("p2", direction_correct=True),
        _make_result("p3", direction_correct=True),
        _make_result("p4", direction_correct=False),
    ]
    r = M.confidence_calibration(results, preds, weight=0.1)
    assert r.raw_value == 0.5
    assert r.normalized_score == 75.0  # 50 + 0.5*50


def test_grade_thresholds():
    assert _grade_for(95) == "A+"
    assert _grade_for(90) == "A+"
    assert _grade_for(89.9) == "A"
    assert _grade_for(80) == "A"
    assert _grade_for(70) == "B"
    assert _grade_for(60) == "C"
    assert _grade_for(50) == "D"
    assert _grade_for(49) == "F"
    assert _grade_for(0) == "F"


def test_score_blogger_end_to_end_with_db(isolated_state_dir):
    """Insert predictions + results into the DB and verify the composite score."""
    username = "testuser"
    # Seed a tweet, predictions, and backtest results
    tweet = TweetRow(
        tweet_id="t1",
        username=username,
        text="Bullish on AAPL, target $200",
        created_at=datetime(2025, 1, 10, 12, 0, 0),
    )
    repo.upsert_tweet(tweet)
    pred = _make_pred("p1", "t1", ticker="AAPL", direction="bullish", prediction_type="directional")
    repo.upsert_prediction(pred)
    repo.upsert_backtest_result(
        _make_result("p1", return_pct=10.0, direction_correct=True, end_close=110.0)
    )

    score = score_blogger(username)
    assert 0.0 <= score.overall_score <= 100.0
    assert score.username == username
    assert score.sample_size == 1
    # With a single correct bullish call, hit_rate=100 → score 100; avg_return
    # aligned = +10% → score 100. Other metrics have 0 sample. Overall should
    # be high since the only sample-weighted metrics are perfect.
    assert score.overall_score > 50.0
    # Per-metric breakdown should include all 7 metrics
    names = {m.name for m in score.per_metric}
    assert names == {"hit_rate", "avg_return", "target_acc", "sharpe", "horizon", "calibration", "coverage"}


def test_score_blogger_handles_empty_user(isolated_state_dir):
    score = score_blogger("nonexistent")
    assert score.overall_score == 0.0
    assert score.grade == "F"
    assert score.sample_size == 0
    assert any("No backtest results" in n for n in score.notes)
