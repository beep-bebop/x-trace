"""Backtest engine: compute return / drawdown / target-hit per prediction.

The engine is deliberately vendor-agnostic — it calls
``x_trace.data.interface.get_ohlcv`` which handles multi-vendor fallback
and caching. SPY is fetched in parallel as the benchmark for alpha.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Iterable

import pandas as pd

from ..data import interface as data_iface
from ..data.errors import NoMarketDataError
from ..storage import repository as repo
from ..storage.models import BacktestResultRow, PredictionRow, TweetRow
from .windows import Window, resolve_window

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "SPY"


def _first_trading_day_on_or_after(df: pd.DataFrame, target: date) -> tuple[date, pd.Series] | None:
    """Return (date, row) for the first trading day >= target, or None."""
    if df is None or df.empty:
        return None
    # Index may be date or datetime
    idx_dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    for i, d in enumerate(idx_dates):
        if d >= target:
            return d, df.iloc[i]
    return None


def _last_trading_day_on_or_before(df: pd.DataFrame, target: date) -> tuple[date, pd.Series] | None:
    if df is None or df.empty:
        return None
    idx_dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    last: tuple[date, pd.Series] | None = None
    for i, d in enumerate(idx_dates):
        if d <= target:
            last = (d, df.iloc[i])
        else:
            break
    return last


def _max_drawdown_runup(df: pd.DataFrame, base_close: float) -> tuple[float, float]:
    """Return (max_drawdown_pct, max_runup_pct) over the window.

    Both are computed against ``base_close`` (the entry price).
    """
    if df is None or df.empty or base_close <= 0:
        return 0.0, 0.0
    closes = df["Close"]
    # Drawdown: most negative (close - base) / base
    drawdowns = (closes - base_close) / base_close * 100.0
    max_dd = float(drawdowns.min())
    max_up = float(drawdowns.max())
    return max_dd, max_up


def backtest_prediction(
    pred: PredictionRow,
    post_date: date,
    windows: list[Window],
    today: date | None = None,
) -> list[BacktestResultRow]:
    """Compute one BacktestResultRow per window for a single prediction."""
    results: list[BacktestResultRow] = []
    if pred.ticker == "__NONE__":
        return results

    # We need OHLCV from post_date through the latest window end + a small buffer
    ends = [resolve_window(post_date, w, today)[1] for w in windows]
    max_end = max(ends)
    # Fetch the whole range once
    try:
        df = data_iface.get_ohlcv(pred.ticker, post_date, max_end)
    except NoMarketDataError as e:
        logger.warning("No data for %s; skipping prediction %s: %s", pred.ticker, pred.prediction_id, e)
        return results
    except Exception as e:
        logger.warning("Failed to fetch %s for %s: %s", pred.ticker, post_date, e)
        return results

    base = _first_trading_day_on_or_after(df, post_date)
    if base is None:
        logger.warning("No trading day on/after %s for %s", post_date, pred.ticker)
        return results
    base_date, base_row = base
    base_close = float(base_row["Close"])

    # Benchmark (SPY) over the same range — best-effort
    try:
        spy_df = data_iface.get_ohlcv(BENCHMARK_SYMBOL, post_date, max_end)
    except Exception as e:
        logger.debug("Could not fetch SPY benchmark: %s", e)
        spy_df = None

    for window in windows:
        _, end_target = resolve_window(post_date, window, today)
        end = _last_trading_day_on_or_before(df, end_target)
        if end is None:
            logger.warning("No trading day on/before %s for %s; skipping %s", end_target, pred.ticker, window.value)
            continue
        end_date, end_row = end
        end_close = float(end_row["Close"])
        if pd.isna(end_close) or pd.isna(base_close):
            logger.warning(
                "NaN close for %s on %s or %s; skipping %s window",
                pred.ticker, base_date, end_date, window.value,
            )
            continue

        window_df = df.loc[(df.index >= pd.Timestamp(base_date)) & (df.index <= pd.Timestamp(end_date))]
        max_dd, max_up = _max_drawdown_runup(window_df, base_close)

        return_pct = (end_close - base_close) / base_close * 100.0
        direction_correct = (
            (return_pct > 0 and pred.direction == "bullish")
            or (return_pct < 0 and pred.direction == "bearish")
            or (pred.direction == "neutral")
        )

        hit_target: bool | None = None
        if pred.target_price is not None and base_close > 0:
            if pred.direction == "bullish":
                hit_target = bool((window_df["High"] >= pred.target_price).any())
            elif pred.direction == "bearish":
                hit_target = bool((window_df["Low"] <= pred.target_price).any())

        spy_return_pct: float | None = None
        alpha_pct: float | None = None
        if spy_df is not None and not spy_df.empty:
            spy_base = _first_trading_day_on_or_after(spy_df, post_date)
            spy_end = _last_trading_day_on_or_before(spy_df, end_target)
            if spy_base and spy_end:
                spy_return_pct = (spy_end[1]["Close"] - spy_base[1]["Close"]) / spy_base[1]["Close"] * 100.0
                alpha_pct = return_pct - spy_return_pct

        results.append(
            BacktestResultRow(
                result_id=f"{pred.prediction_id}#{window.value}",
                prediction_id=pred.prediction_id,
                window=window.value,
                base_date=base_date,
                end_date=end_date,
                base_close=base_close,
                end_close=end_close,
                return_pct=round(return_pct, 4),
                max_drawdown_pct=round(max_dd, 4),
                max_runup_pct=round(max_up, 4),
                direction_correct=direction_correct,
                hit_target=hit_target,
                spy_return_pct=round(spy_return_pct, 4) if spy_return_pct is not None else None,
                alpha_pct=round(alpha_pct, 4) if alpha_pct is not None else None,
            )
        )

    return results


def backtest_user_predictions(
    username: str,
    windows: list[Window],
    today: date | None = None,
    max_workers: int = 4,
) -> int:
    """Backtest all predictions for `username` across `windows`.

    Returns the number of new BacktestResultRow records persisted.
    Predictions are fetched with their parent tweet (for the post_date)
    and processed in a thread pool to parallelize OHLCV fetches across
    different tickers.
    """
    predictions = repo.get_predictions_for_user(username)
    if not predictions:
        logger.info("No predictions to backtest for %s", username)
        return 0

    # Build a {tweet_id -> created_at.date()} map
    tweet_dates: dict[str, date] = {}
    for pred in predictions:
        if pred.tweet_id not in tweet_dates:
            tw = repo.get_tweet(pred.tweet_id)
            if tw is not None:
                tweet_dates[pred.tweet_id] = tw.created_at.date()

    new_count = 0

    def _work(pred: PredictionRow) -> list[BacktestResultRow]:
        post_date = tweet_dates.get(pred.tweet_id)
        if post_date is None:
            logger.warning("No tweet date for prediction %s; skipping", pred.prediction_id)
            return []
        return backtest_prediction(pred, post_date, windows, today)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, pred): pred for pred in predictions if pred.ticker != "__NONE__"}
        for fut in as_completed(futures):
            try:
                rows = fut.result()
            except Exception as e:
                logger.warning("Backtest failed for %s: %s", futures[fut].prediction_id, e)
                continue
            for row in rows:
                repo.upsert_backtest_result(row)
                new_count += 1

    logger.info("Backtested %d predictions for %s; %d new results", len(predictions), username, new_count)
    return new_count
