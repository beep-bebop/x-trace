# X-Trace

Backtest and score X (Twitter) bloggers' stock predictions.

## Quick Start

```bash
# 1. Install
pip install -e .
playwright install chromium

# 2. Configure
cp .env.example .env
# Edit .env and set DEEPSEEK_API_KEY

# 3. Login to X (one-time, opens a visible browser)
x-trace login

# 4. End-to-end run
x-trace run --username RachelDashCS --start 2025-01-01 --end 2025-03-31 --windows 1m,3m
```

## Commands

- `x-trace login` — open a browser, log into X, save the session.
- `x-trace crawl --username <u> --start <YYYY-MM-DD> --end <YYYY-MM-DD>` — fetch tweets.
- `x-trace analyze [--username <u>]` — run DeepSeek over unanalyzed tweets.
- `x-trace backtest [--windows 1m,3m]` — backtest all predictions.
- `x-trace score --username <u>` — compute the multi-dimensional score.
- `x-trace run ...` — pipeline of crawl → analyze → backtest → score.

Reports are written to `.x-trace/reports/` as JSON + Markdown.

## Architecture

| Module | Purpose |
|---|---|
| `x_trace/crawler` | Playwright-based tweet scraping with login-session persistence. |
| `x_trace/analyzer` | DeepSeek LLM extracts `StockPrediction` records from raw tweets. |
| `x_trace/data` | Multi-vendor OHLCV fetcher (yfinance → Alpha Vantage → Finnhub) with chain fallback. |
| `x_trace/backtest` | Per-prediction return / drawdown / target-hit calculation over configurable windows. |
| `x_trace/scoring` | Seven-metric weighted score (hit rate, return, target accuracy, Sharpe, horizon, calibration, coverage). |
| `x_trace/storage` | SQLite persistence (WAL) for tweets, predictions, backtest results, OHLCV cache. |
| `x_trace/cli` | Typer entrypoint exposing the commands above. |

## Extending

- **New blogger**: just pass a different `--username`. Sessions and DB rows are keyed by `tweet_id`.
- **New data source**: add a `*_source.py` under `x_trace/data/`, register it in `interface.VENDOR_METHODS`, and append the vendor name to `X_TRACE_DATA_VENDORS_OHLCV` (or the `data_vendors.ohlcv` config).
- **New LLM provider**: the analyzer uses `langchain_openai.ChatOpenAI` with a configurable `base_url`; any OpenAI-compatible endpoint works.
