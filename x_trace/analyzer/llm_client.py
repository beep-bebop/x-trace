"""DeepSeek LLM client (OpenAI-compatible).

DeepSeek's API is OpenAI-compatible, so we use ``langchain_openai.ChatOpenAI``
with ``base_url=https://api.deepseek.com/v1``. Following the pattern in
TradingAgents' ``NormalizedChatOpenAI.with_structured_output``, we bind
the schema as a tool but suppress ``tool_choice`` for models that reject it.
"""
from __future__ import annotations

import logging
from typing import Type, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..config import get_config
from .schemas import TweetAnalysis

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


SYSTEM_PROMPT = """You are a financial-tweet analyst. Given a single tweet, extract every stock
prediction it contains.

For each stock mentioned:
- Identify the ticker (uppercase, e.g. AAPL). If the company name is given but not the ticker,
  infer the most likely US-listed ticker.
- Decide the blogger's directional view: bullish / bearish / neutral.
- Decide the prediction type:
  * target_price — an explicit price target is given
  * directional — a plain bullish/bearish call with no hard levels
  * entry_stop — entry and stop-loss levels are given
  * hold — recommending to hold or wait
  * watch — adding to a watchlist, no clear call
  * neutral_mention — mentions the stock without expressing a view
- Extract target_price, entry_price, stop_loss as floats when stated, else null.
- Extract time_horizon as a short string when stated (e.g. "1 month", "end of year").
- Infer confidence: high (explicit targets + strong tone), medium (clear direction),
  low (vague/hedged).
- Quote the supporting phrase from the tweet in `rationale`.

If the tweet mentions no stocks, set `has_no_stock_mention=true`, leave
`predictions` empty, and put a one-sentence summary in `summary`.

Return ONLY the structured object. Do not add commentary outside the schema."""


class DeepSeekClient:
    """Thin wrapper around ChatOpenAI configured for the DeepSeek endpoint."""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        cfg = get_config()
        self.model = model or cfg["deepseek_model"]
        self.base_url = base_url or cfg["deepseek_base_url"]
        self.api_key = api_key or cfg["deepseek_api_key"]
        if not self.api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Add it to .env or export it."
            )
        self._client = ChatOpenAI(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            temperature=0.0,
        )

    def _structured(self, schema: Type[T]):
        """Bind a Pydantic schema for structured output.

        DeepSeek's function-calling guide says some models reject the
        ``tool_choice`` parameter, so we suppress it (mirror of TradingAgents'
        ``NormalizedChatOpenAI.with_structured_output``).
        """
        try:
            return self._client.with_structured_output(
                schema, method="function_calling", tool_choice=None
            )
        except TypeError:
            # Older langchain versions don't accept tool_choice kwarg
            return self._client.with_structured_output(schema, method="function_calling")

    def analyze_tweet(self, text: str) -> TweetAnalysis:
        """Run the LLM on a single tweet and return the parsed analysis."""
        if not text or not text.strip():
            return TweetAnalysis(predictions=[], has_no_stock_mention=True, summary="Empty tweet.")
        chain = self._structured(TweetAnalysis)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=text),
        ]
        result = chain.invoke(messages)
        return result
