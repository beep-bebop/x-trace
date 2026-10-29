"""Repository pattern over SQLite via SQLModel.

A single connection in WAL mode with a threading.Lock makes concurrent
reads/writes safe for the backtest thread pool.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from ..config import get_config
from .models import BacktestResultRow, OhlcvCacheRow, PredictionRow, TweetRow


_lock = threading.Lock()
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        db_path = get_config()["storage"]["db_path"]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the backtest thread pool can share it
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def _set_wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA synchronous=NORMAL")

        SQLModel.metadata.create_all(_engine)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    engine = _get_engine()
    with Session(engine, expire_on_commit=False) as session:
        with _lock:
            yield session


# ---------------------------------------------------------------------------
# Tweets
# ---------------------------------------------------------------------------


def upsert_tweet(row: TweetRow) -> None:
    with session_scope() as s:
        existing = s.get(TweetRow, row.tweet_id)
        if existing:
            existing.text = row.text
            existing.reply_count = row.reply_count
            existing.retweet_count = row.retweet_count
            existing.like_count = row.like_count
            existing.urls = row.urls
            s.add(existing)
        else:
            s.add(row)
        s.commit()


def get_tweet(tweet_id: str) -> Optional[TweetRow]:
    with session_scope() as s:
        return s.get(TweetRow, tweet_id)


def get_tweets_by_user(
    username: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[TweetRow]:
    with session_scope() as s:
        stmt = select(TweetRow).where(TweetRow.username == username)
        if start:
            stmt = stmt.where(TweetRow.created_at >= start)
        if end:
            stmt = stmt.where(TweetRow.created_at <= end)
        stmt = stmt.order_by(TweetRow.created_at.asc())
        return list(s.exec(stmt).all())


def get_existing_tweet_ids(username: str) -> set[str]:
    with session_scope() as s:
        stmt = select(TweetRow.tweet_id).where(TweetRow.username == username)
        return {row for row in s.exec(stmt).all()}


def get_all_usernames() -> list[str]:
    """Return all distinct usernames that have at least one tweet in the DB."""
    with session_scope() as s:
        stmt = select(TweetRow.username).distinct()
        return list(s.exec(stmt).all())


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------


def upsert_prediction(row: PredictionRow) -> None:
    with session_scope() as s:
        existing = s.get(PredictionRow, row.prediction_id)
        if existing:
            for k in row.model_dump():
                setattr(existing, k, getattr(row, k))
            s.add(existing)
        else:
            s.add(row)
        s.commit()


def get_predictions_for_user(username: str) -> list[PredictionRow]:
    with session_scope() as s:
        stmt = (
            select(PredictionRow)
            .join(TweetRow, TweetRow.tweet_id == PredictionRow.tweet_id)
            .where(TweetRow.username == username)
            .order_by(PredictionRow.analyzed_at.asc())
        )
        return list(s.exec(stmt).all())


def get_predictions_for_tweet(tweet_id: str) -> list[PredictionRow]:
    with session_scope() as s:
        stmt = select(PredictionRow).where(PredictionRow.tweet_id == tweet_id)
        return list(s.exec(stmt).all())


def get_unanalyzed_tweet_ids(username: str) -> list[str]:
    with session_scope() as s:
        all_tweets = select(TweetRow.tweet_id).where(TweetRow.username == username)
        analyzed = select(PredictionRow.tweet_id)
        unanalyzed = all_tweets.except_(analyzed)
        return list(s.execute(unanalyzed).scalars().all())


# ---------------------------------------------------------------------------
# Backtest results
# ---------------------------------------------------------------------------


def upsert_backtest_result(row: BacktestResultRow) -> None:
    with session_scope() as s:
        existing = s.get(BacktestResultRow, row.result_id)
        if existing:
            for k in row.model_dump():
                setattr(existing, k, getattr(row, k))
            s.add(existing)
        else:
            s.add(row)
        s.commit()


def get_backtest_results_for_user(username: str) -> list[BacktestResultRow]:
    with session_scope() as s:
        stmt = (
            select(BacktestResultRow)
            .join(PredictionRow, PredictionRow.prediction_id == BacktestResultRow.prediction_id)
            .join(TweetRow, TweetRow.tweet_id == PredictionRow.tweet_id)
            .where(TweetRow.username == username)
        )
        return list(s.exec(stmt).all())


# ---------------------------------------------------------------------------
# OHLCV cache
# ---------------------------------------------------------------------------


def get_ohlcv_cached(symbol: str, start: date, end: date) -> Optional[pd.DataFrame]:
    """Return cached OHLCV rows for [start, end] if present, else None."""
    with session_scope() as s:
        stmt = (
            select(OhlcvCacheRow)
            .where(OhlcvCacheRow.symbol == symbol)
            .where(OhlcvCacheRow.trade_date >= start)
            .where(OhlcvCacheRow.trade_date <= end)
            .order_by(OhlcvCacheRow.trade_date.asc())
        )
        rows = list(s.exec(stmt).all())
        if not rows:
            return None
        df = pd.DataFrame(
            [
                {
                    "Date": r.trade_date,
                    "Open": r.open,
                    "High": r.high,
                    "Low": r.low,
                    "Close": r.close,
                    "Volume": r.volume,
                }
                for r in rows
            ]
        )
        df.set_index("Date", inplace=True)
        return df


def save_ohlcv(symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    with session_scope() as s:
        for idx, row in df.iterrows():
            d = idx.date() if isinstance(idx, datetime) else idx
            cache_row = OhlcvCacheRow(
                symbol=symbol,
                trade_date=d,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0)),
            )
            existing = s.get(OhlcvCacheRow, (symbol, d))
            if existing:
                existing.open = cache_row.open
                existing.high = cache_row.high
                existing.low = cache_row.low
                existing.close = cache_row.close
                existing.volume = cache_row.volume
                s.add(existing)
            else:
                s.add(cache_row)
        s.commit()
