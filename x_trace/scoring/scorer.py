"""Compose per-metric results into a single 0-100 blogger score."""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, Field

from ..config import get_config
from ..storage import repository as repo
from ..storage.models import BacktestResultRow, PredictionRow
from . import metrics as M

logger = logging.getLogger(__name__)


class MetricScore(BaseModel):
    name: str
    raw_value: float | None
    sample_size: int
    weight: float
    normalized_score: float


class BloggerScore(BaseModel):
    username: str
    overall_score: float = Field(ge=0.0, le=100.0)
    grade: str
    per_metric: list[MetricScore]
    sample_size: int
    notes: list[str] = Field(default_factory=list)


def _grade_for(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def score_blogger(username: str) -> BloggerScore:
    """Compute the multi-dimensional score for ``username``.

    Pulls all predictions + backtest results for the user from the DB,
    runs every metric, and returns a weighted composite.
    """
    preds = repo.get_predictions_for_user(username)
    results = repo.get_backtest_results_for_user(username)

    preds_by_id: dict[str, PredictionRow] = {p.prediction_id: p for p in preds}
    weights = get_config()["scoring_weights"]

    metric_results: list[M.MetricResult] = [
        M.hit_rate(results, preds_by_id, weights.get("hit_rate", 0.0)),
        M.avg_return(results, preds_by_id, weights.get("avg_return", 0.0)),
        M.target_accuracy(results, preds_by_id, weights.get("target_acc", 0.0)),
        M.prediction_sharpe(results, preds_by_id, weights.get("sharpe", 0.0)),
        M.horizon_accuracy(results, preds_by_id, weights.get("horizon", 0.0)),
        M.confidence_calibration(results, preds_by_id, weights.get("calibration", 0.0)),
        M.coverage_breadth(results, preds_by_id, weights.get("coverage", 0.0)),
    ]

    notes: list[str] = []
    serious_preds = [p for p in preds if p.prediction_type in M.SERIOUS_TYPES and p.ticker != "__NONE__"]
    if not serious_preds:
        notes.append("No serious predictions (target_price / directional / entry_stop) — score is illustrative only.")
    if not results:
        notes.append("No backtest results yet — run `x-trace backtest` first.")

    # Weighted composite. Skip metrics with sample_size==0 by renormalizing
    # the remaining weights so the score is still meaningful on small samples.
    usable = [m for m in metric_results if m.sample_size > 0 or m.name == "coverage"]
    total_weight_used = sum(m.weight for m in usable)
    if total_weight_used <= 0:
        overall = 0.0
    else:
        overall = sum(m.normalized_score * m.weight for m in usable) / total_weight_used

    overall = max(0.0, min(100.0, overall))

    return BloggerScore(
        username=username,
        overall_score=round(overall, 2),
        grade=_grade_for(overall),
        per_metric=[MetricScore(**asdict(m)) for m in metric_results],
        sample_size=len(results),
        notes=notes,
    )
