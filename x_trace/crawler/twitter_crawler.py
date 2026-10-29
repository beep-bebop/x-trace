"""Tweet crawler: scroll a blogger's profile and parse each tweet.

The crawler is intentionally resilient: it persists every tweet as soon as
it is parsed (via ``repository.upsert_tweet``) so a crash mid-scroll does
not lose work. Resume is handled by ``get_existing_tweet_ids`` — already
known tweets are skipped, and after 5 consecutive known IDs the scroll
stops because we have likely reached the previously-crawled boundary.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import date, datetime, timezone
from typing import Iterable

from playwright.async_api import BrowserContext, Page

from ..storage import repository as repo
from ..storage.models import TweetRow
from .session import get_logged_in_context, is_session_valid

logger = logging.getLogger(__name__)

_TWEET_ARTICLE_SELECTOR = 'article[data-testid="tweet"]'
_TWEET_LINK_RE = re.compile(r"/(\w+)/status/(\d+)")


class TweetParseError(Exception):
    pass


async def _parse_tweet_article(article, username: str) -> TweetRow | None:
    """Extract a TweetRow from an article element, or None if not a tweet
    by the target user (e.g. a retweet of someone else)."""
    # Tweet link: the first <a> with href matching /<user>/status/<id>
    tweet_id = None
    links = await article.query_selector_all('a[href*="/status/"]')
    for link in links:
        href = await link.get_attribute("href")
        if not href:
            continue
        m = _TWEET_LINK_RE.search(href)
        if m and m.group(1).lower() == username.lower():
            tweet_id = m.group(2)
            break

    if tweet_id is None:
        # Could be a pinned tweet or a reply; skip if we cannot find an id
        return None

    # Tweet text
    text_el = await article.query_selector('div[data-testid="tweetText"]')
    text = (await text_el.inner_text()) if text_el else ""

    # Timestamp: the <time> element's datetime attribute is ISO-8601 UTC
    time_el = await article.query_selector("time")
    created_at_str = await time_el.get_attribute("datetime") if time_el else None
    if not created_at_str:
        return None
    # Parse ISO-8601 (e.g. 2025-06-15T13:45:00.000Z) to a naive UTC datetime
    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)

    # Engagement counts (best-effort, default 0)
    def _to_int(s: str | None) -> int:
        if not s:
            return 0
        s = s.strip().replace(",", "")
        multipliers = {"K": 1_000, "M": 1_000_000}
        if s and s[-1] in multipliers:
            try:
                return int(float(s[:-1]) * multipliers[s[-1]])
            except ValueError:
                return 0
        try:
            return int(s)
        except ValueError:
            return 0

    reply_count = 0
    retweet_count = 0
    like_count = 0
    for group in ["reply", "retweet", "like", "bookmark"]:
        btn = await article.query_selector(f'button[data-testid="{group}"]')
        if btn is None:
            continue
        aria = await btn.get_attribute("aria-label")
        if not aria:
            continue
        # aria-label looks like "123 Replies" / "4.5K Likes"
        parts = aria.split()
        try:
            val = _to_int(parts[0])
        except (IndexError, ValueError):
            val = 0
        if group == "reply":
            reply_count = val
        elif group == "retweet":
            retweet_count = val
        elif group == "like":
            like_count = val

    # Outgoing URLs in the tweet body
    urls: list[str] = []
    for link in links:
        href = await link.get_attribute("href")
        if href and href.startswith("http") and "/status/" not in href:
            urls.append(href)

    return TweetRow(
        tweet_id=tweet_id,
        username=username,
        text=text,
        created_at=created_at,
        reply_count=reply_count,
        retweet_count=retweet_count,
        like_count=like_count,
        urls=json.dumps(urls),
    )


async def _scroll_to_load(
    page: Page,
    username: str,
    start: date,
    end: date,
    existing_ids: set[str],
    max_iter: int = 200,
) -> Iterable[TweetRow]:
    """Scroll the user's profile page, yielding new tweets as they appear.

    Stops when:
    - we've scrolled past `start` (earliest tweet older than start), or
    - we hit `max_iter` iterations, or
    - 5 consecutive tweets are already in `existing_ids` (resume boundary).
    """
    seen_in_session: set[str] = set()
    consecutive_known = 0
    interval = 2.5  # default; overridden by config below

    from ..config import get_config

    cfg = get_config()["crawler"]
    interval = cfg["request_interval_sec"]
    max_retries = cfg["max_retries"]

    for _ in range(max_iter):
        # Wait for tweet articles to render
        try:
            await page.wait_for_selector(_TWEET_ARTICLE_SELECTOR, timeout=10_000)
        except Exception:
            logger.warning("No tweet articles found on page; stopping scroll.")
            break

        articles = await page.query_selector_all(_TWEET_ARTICLE_SELECTOR)
        oldest_seen: datetime | None = None

        for article in articles:
            try:
                row = await _parse_tweet_article(article, username)
            except Exception as e:
                logger.debug("Failed to parse article: %s", e)
                continue
            if row is None:
                continue

            if row.tweet_id in seen_in_session:
                continue
            seen_in_session.add(row.tweet_id)

            if oldest_seen is None or row.created_at < oldest_seen:
                oldest_seen = row.created_at

            # Filter by date range
            if row.created_at.date() < start:
                # Older than start — keep going a bit more in case of out-of-order
                consecutive_known = 5  # force stop
                continue
            if row.created_at.date() > end:
                continue

            # Persist immediately so a crash doesn't lose work
            try:
                repo.upsert_tweet(row)
            except Exception as e:
                logger.warning("Failed to persist tweet %s: %s", row.tweet_id, e)

            if row.tweet_id in existing_ids:
                consecutive_known += 1
            else:
                consecutive_known = 0
            yield row

        if consecutive_known >= 5:
            logger.info("Hit resume boundary (5 consecutive known tweets); stopping.")
            break

        if oldest_seen is not None and oldest_seen.date() < start:
            logger.info("Reached tweets older than start (%s); stopping.", start)
            break

        # Scroll down
        for _ in range(max_retries):
            try:
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                # Random jitter for anti-detection
                await asyncio.sleep(interval + random.uniform(0, 1.5))
                break
            except Exception as e:
                logger.warning("Scroll error: %s; retrying", e)
                await asyncio.sleep(2.0)
        else:
            logger.warning("Exhausted scroll retries; stopping.")
            break


async def crawl_tweets(
    username: str,
    start: date,
    end: date,
    headless: bool = True,
) -> int:
    """Crawl tweets for `username` between [start, end] inclusive.

    Returns the number of newly-persisted tweets. Existing tweets in the
    DB are skipped via the resume boundary logic.
    """
    from playwright.async_api import async_playwright

    existing_ids = repo.get_existing_tweet_ids(username)
    new_count = 0

    async with async_playwright() as p:
        context = await get_logged_in_context(p, headless=headless)
        try:
            if not await is_session_valid(context):
                from .session import SessionExpiredError
                raise SessionExpiredError(
                    "Saved session is no longer valid. Run `x-trace login`."
                )

            page = await context.new_page()
            url = f"https://x.com/{username}"
            logger.info("Opening %s", url)
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

            async for row in _scroll_to_load(page, username, start, end, existing_ids):
                if row.tweet_id not in existing_ids:
                    new_count += 1
                    existing_ids.add(row.tweet_id)
        finally:
            await context.close()

    logger.info("Crawled %d new tweets for %s", new_count, username)
    return new_count
