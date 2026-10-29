"""End-to-end tweet analyzer: DB → LLM → DB.

Iterates over unanalyzed tweets for a user, runs DeepSeek on each, and
persists the extracted ``PredictionRow`` records. Re-runs are idempotent
because ``prediction_id`` is keyed by ``(tweet_id, idx)``.
"""
from __future__ import annotations

import logging
from typing import Iterable

from ..storage import repository as repo
from ..storage.models import PredictionRow, TweetRow
from .llm_client import DeepSeekClient
from .schemas import TweetAnalysis

logger = logging.getLogger(__name__)


def _to_prediction_rows(tweet: TweetRow, analysis: TweetAnalysis) -> list[PredictionRow]:
    rows: list[PredictionRow] = []
    for idx, pred in enumerate(analysis.predictions):
        rows.append(
            PredictionRow(
                prediction_id=f"{tweet.tweet_id}#{idx}",
                tweet_id=tweet.tweet_id,
                ticker=pred.ticker.upper(),
                company_name=pred.company_name,
                direction=pred.direction.value,
                prediction_type=pred.prediction_type.value,
                target_price=pred.target_price,
                entry_price=pred.entry_price,
                stop_loss=pred.stop_loss,
                time_horizon=pred.time_horizon,
                confidence=pred.confidence,
                rationale=pred.rationale,
            )
        )
    return rows


def analyze_user_tweets(
    username: str,
    client: DeepSeekClient | None = None,
    tweet_ids: Iterable[str] | None = None,
) -> int:
    """Analyze unanalyzed tweets for `username`. Returns the number of
    new predictions persisted.

    If `tweet_ids` is provided, only those tweets are (re)analyzed; otherwise
    all tweets for the user without any PredictionRow are processed.
    """
    client = client or DeepSeekClient()

    if tweet_ids is None:
        targets = repo.get_unanalyzed_tweet_ids(username)
    else:
        targets = list(tweet_ids)

    if not targets:
        logger.info("No tweets to analyze for %s", username)
        return 0

    new_preds = 0
    for tweet_id in targets:
        tweet = repo.get_tweet(tweet_id)
        if tweet is None:
            logger.warning("Tweet %s not found in DB; skipping", tweet_id)
            continue

        try:
            analysis: TweetAnalysis = client.analyze_tweet(tweet.text)
        except Exception as e:
            logger.error("LLM failed on tweet %s: %s", tweet_id, e)
            continue

        # LLM may return None when it doesn't emit a tool call (e.g. refusal
        # or empty response). Treat as no-stock-mention so we don't retry.
        if analysis is None:
            logger.warning("LLM returned None for tweet %s; persisting sentinel", tweet_id)
            sentinel = PredictionRow(
                prediction_id=f"{tweet_id}#0",
                tweet_id=tweet_id,
                ticker="__NONE__",
                company_name=None,
                direction="neutral",
                prediction_type="neutral_mention",
                target_price=None,
                entry_price=None,
                stop_loss=None,
                time_horizon=None,
                confidence="low",
                rationale="LLM returned no structured output.",
            )
            repo.upsert_prediction(sentinel)
            continue

        # If the tweet truly has no stock mentions, persist a sentinel
        # PredictionRow so we don't keep retrying it on every run.
        if analysis.has_no_stock_mention or not analysis.predictions:
            sentinel = PredictionRow(
                prediction_id=f"{tweet_id}#0",
                tweet_id=tweet_id,
                ticker="__NONE__",
                company_name=None,
                direction="neutral",
                prediction_type="neutral_mention",
                target_price=None,
                entry_price=None,
                stop_loss=None,
                time_horizon=None,
                confidence="low",
                rationale=analysis.summary or "No stock mention.",
            )
            repo.upsert_prediction(sentinel)
            continue

        for row in _to_prediction_rows(tweet, analysis):
            repo.upsert_prediction(row)
            new_preds += 1

    logger.info("Analyzed %d tweets for %s; %d new predictions", len(targets), username, new_preds)
    return new_preds
