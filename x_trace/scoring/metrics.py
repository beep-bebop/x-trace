"""Individual scoring metrics. Each takes a list of BacktestResultRow (+ the
parent PredictionRows where needed) and returns a MetricResult.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from ..storage.models import BacktestResultRow, PredictionRow


# Prediction types that should count towards serious metrics
SERIOUS_TYPES = {"target_price", "directional", "entry_stop"}


@dataclass
class MetricResult:
    name: str
    raw_value: float | None  # None when no data
    sample_size: int
    weight: float
    normalized_score: float  # 0-100


def _serious_predictions(preds: Iterable[PredictionRow]) -> list[PredictionRow]:
    return [p for p in preds if p.prediction_type in SERIOUS_TYPES and p.ticker != "__NONE__"]


def _aligned_return(result: BacktestResultRow, pred: PredictionRow) -> float:
    """Return the direction-aligned return: positive if the call was right."""
    r = result.return_pct
    if pred.direction == "bearish":
        return -r
    return r


def hit_rate(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
) -> MetricResult:
    """Fraction of directional / target_price calls where direction_correct."""
    subset = [
        r for r in results
        if r.prediction_id in preds_by_id
        and preds_by_id[r.prediction_id].prediction_type in SERIOUS_TYPES
    ]
    if not subset:
        return MetricResult("hit_rate", None, 0, weight, 0.0)
    hits = sum(1 for r in subset if r.direction_correct)
    rate = hits / len(subset)
    # 50% hit rate is neutral (coin flip); scale so 50%→50, 100%→100, 0%→0.
    score = max(0.0, min(100.0, rate * 100.0))
    return MetricResult("hit_rate", rate, len(subset), weight, score)


def avg_return(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
) -> MetricResult:
    """Mean direction-aligned return % over serious predictions."""
    subset = [
        r for r in results
        if r.prediction_id in preds_by_id
        and preds_by_id[r.prediction_id].prediction_type in SERIOUS_TYPES
    ]
    if not subset:
        return MetricResult("avg_return", None, 0, weight, 0.0)
    aligned = [_aligned_return(r, preds_by_id[r.prediction_id]) for r in subset]
    mean = sum(aligned) / len(aligned)
    # Map -10%→0, 0%→50, +10%→100 via a linear clamped scale.
    score = max(0.0, min(100.0, 50.0 + mean * 5.0))
    return MetricResult("avg_return", mean, len(subset), weight, score)


def target_accuracy(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
) -> MetricResult:
    """For predictions with a target_price: 1 - |target - end_close| / base_close."""
    subset = [
        r for r in results
        if r.prediction_id in preds_by_id
        and preds_by_id[r.prediction_id].target_price is not None
        and r.base_close > 0
    ]
    if not subset:
        return MetricResult("target_acc", None, 0, weight, 0.0)
    accs: list[float] = []
    for r in subset:
        target = preds_by_id[r.prediction_id].target_price
        if target is None:
            continue
        rel_err = abs(target - r.end_close) / r.base_close
        acc = max(0.0, 1.0 - rel_err)
        accs.append(acc)
    if not accs:
        return MetricResult("target_acc", None, 0, weight, 0.0)
    mean = sum(accs) / len(accs)
    return MetricResult("target_acc", mean, len(accs), weight, mean * 100.0)


def prediction_sharpe(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
) -> MetricResult:
    """Mean aligned return / std of aligned return (risk-adjusted)."""
    subset = [
        r for r in results
        if r.prediction_id in preds_by_id
        and preds_by_id[r.prediction_id].prediction_type in SERIOUS_TYPES
    ]
    if len(subset) < 2:
        return MetricResult("sharpe", None, len(subset), weight, 0.0)
    aligned = [_aligned_return(r, preds_by_id[r.prediction_id]) for r in subset]
    mean = sum(aligned) / len(aligned)
    var = sum((x - mean) ** 2 for x in aligned) / (len(aligned) - 1)
    std = math.sqrt(var)
    if std == 0:
        # All returns identical — no risk; reward positive mean, punish negative
        sharpe = float("inf") if mean > 0 else (float("-inf") if mean < 0 else 0.0)
    else:
        sharpe = mean / std
    # Map sharpe to 0-100: 0→50, +2→100, -2→0
    if sharpe == float("inf"):
        score = 100.0
    elif sharpe == float("-inf"):
        score = 0.0
    else:
        score = max(0.0, min(100.0, 50.0 + sharpe * 25.0))
    return MetricResult("sharpe", sharpe if std != 0 else None, len(subset), weight, score)


def horizon_accuracy(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
) -> MetricResult:
    """For target_price predictions: did the price touch the target within the
    stated window? Returns the fraction that did."""
    subset = [
        r for r in results
        if r.prediction_id in preds_by_id
        and preds_by_id[r.prediction_id].target_price is not None
        and r.hit_target is not None
    ]
    if not subset:
        return MetricResult("horizon", None, 0, weight, 0.0)
    hits = sum(1 for r in subset if r.hit_target)
    rate = hits / len(subset)
    return MetricResult("horizon", rate, len(subset), weight, rate * 100.0)


def confidence_calibration(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
) -> MetricResult:
    """High-confidence hit rate minus low-confidence hit rate.

    A positive value means the blogger's stated confidence correlates with
    being right — a sign of good self-calibration.
    """
    serious = [
        r for r in results
        if r.prediction_id in preds_by_id
        and preds_by_id[r.prediction_id].prediction_type in SERIOUS_TYPES
    ]
    if not serious:
        return MetricResult("calibration", None, 0, weight, 50.0)
    high = [r for r in serious if preds_by_id[r.prediction_id].confidence == "high"]
    low = [r for r in serious if preds_by_id[r.prediction_id].confidence == "low"]
    if not high or not low:
        # Can't compute a meaningful delta without both buckets
        return MetricResult("calibration", None, len(serious), weight, 50.0)
    high_rate = sum(1 for r in high if r.direction_correct) / len(high)
    low_rate = sum(1 for r in low if r.direction_correct) / len(low)
    delta = high_rate - low_rate  # in [-1, 1]
    # Map delta -1→0, 0→50, +1→100
    score = 50.0 + delta * 50.0
    return MetricResult("calibration", delta, len(serious), weight, max(0.0, min(100.0, score)))


def coverage_breadth(
    results: list[BacktestResultRow],
    preds_by_id: dict[str, PredictionRow],
    weight: float,
    max_tickers_for_full_score: int = 20,
) -> MetricResult:
    """log(1 + unique_tickers) normalized to a 0-100 scale."""
    tickers = {
        preds_by_id[r.prediction_id].ticker
        for r in results
        if r.prediction_id in preds_by_id and preds_by_id[r.prediction_id].ticker != "__NONE__"
    }
    n = len(tickers)
    if n == 0:
        return MetricResult("coverage", 0, 0, weight, 0.0)
    # log(1 + n) / log(1 + max) * 100
    score = math.log(1 + n) / math.log(1 + max_tickers_for_full_score) * 100.0
    score = max(0.0, min(100.0, score))
    return MetricResult("coverage", float(n), n, weight, score)
