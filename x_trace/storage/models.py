"""SQLModel table definitions for tweets, predictions, backtest results, and OHLCV cache."""
from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TweetRow(SQLModel, table=True):
    """A raw tweet fetched from X."""

    __tablename__ = "tweets"

    tweet_id: str = Field(primary_key=True)
    username: str = Field(index=True)
    text: str
    created_at: datetime  # UTC
    reply_count: int = 0
    retweet_count: int = 0
    like_count: int = 0
    urls: str = ""  # JSON-serialized list[str]
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class PredictionRow(SQLModel, table=True):
    """A structured stock prediction extracted from a tweet by the LLM."""

    __tablename__ = "predictions"

    prediction_id: str = Field(primary_key=True)  # f"{tweet_id}#{idx}"
    tweet_id: str = Field(index=True)
    ticker: str = Field(index=True)
    company_name: Optional[str] = None
    direction: str  # bullish / bearish / neutral
    prediction_type: str  # target_price / directional / entry_stop / hold / watch / neutral_mention
    target_price: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    time_horizon: Optional[str] = None
    confidence: str  # low / medium / high
    rationale: str = ""
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)


class BacktestResultRow(SQLModel, table=True):
    """Outcome of backtesting one prediction over one window."""

    __tablename__ = "backtest_results"

    result_id: str = Field(primary_key=True)  # f"{prediction_id}#{window}"
    prediction_id: str = Field(index=True)
    window: str  # 1w / 1m / 3m / to_date
    base_date: date
    end_date: date
    base_close: float
    end_close: float
    return_pct: float
    max_drawdown_pct: float
    max_runup_pct: float
    direction_correct: bool
    hit_target: Optional[bool] = None
    spy_return_pct: Optional[float] = None
    alpha_pct: Optional[float] = None
    backtested_at: datetime = Field(default_factory=datetime.utcnow)


class OhlcvCacheRow(SQLModel, table=True):
    """Daily OHLCV cache to avoid re-fetching from vendors."""

    __tablename__ = "ohlcv_cache"

    symbol: str = Field(primary_key=True)
    trade_date: date = Field(primary_key=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
