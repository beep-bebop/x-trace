"""Central configuration with environment-variable overrides.

Mirrors the pattern in TradingAgents' `default_config.py`: a single
``DEFAULT_CONFIG`` dict plus an ``_apply_env_overrides`` helper that
coerces ``X_TRACE_*`` env vars to the type of the existing default.
"""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_DIR = Path(os.getenv("X_TRACE_STATE_DIR", _PROJECT_ROOT / ".x-trace"))


def _apply_env_overrides(config: dict) -> dict:
    """Apply X_TRACE_* env vars to the config dict in-place."""
    # Flat scalar overrides: X_TRACE_<KEY>
    flat_keys = {
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "deepseek_base_url": "DEEPSEEK_BASE_URL",
        "deepseek_model": "DEEPSEEK_MODEL",
        "alpha_vantage_api_key": "ALPHA_VANTAGE_API_KEY",
        "finnhub_api_key": "FINNHUB_API_KEY",
    }
    for cfg_key, env_var in flat_keys.items():
        raw = os.environ.get(env_var)
        if raw:
            config[cfg_key] = raw

    # Backtest windows (list)
    raw_windows = os.environ.get("X_TRACE_BACKTEST_WINDOWS")
    if raw_windows:
        config["backtest_windows"] = [w.strip() for w in raw_windows.split(",") if w.strip()]

    # Scoring weights (dict[str, float])
    raw_weights = os.environ.get("X_TRACE_SCORE_WEIGHTS")
    if raw_weights:
        weights: dict[str, float] = {}
        for pair in raw_weights.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            try:
                weights[k.strip()] = float(v.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid weight value for {k!r}: {exc}") from exc
        if weights:
            config["scoring_weights"] = weights

    # Crawler settings (nested)
    interval = os.environ.get("X_TRACE_CRAWLER_REQUEST_INTERVAL_SEC")
    if interval:
        try:
            config["crawler"]["request_interval_sec"] = float(interval)
        except ValueError as exc:
            raise ValueError(f"Invalid X_TRACE_CRAWLER_REQUEST_INTERVAL_SEC: {exc}") from exc
    retries = os.environ.get("X_TRACE_CRAWLER_MAX_RETRIES")
    if retries:
        try:
            config["crawler"]["max_retries"] = int(retries)
        except ValueError as exc:
            raise ValueError(f"Invalid X_TRACE_CRAWLER_MAX_RETRIES: {exc}") from exc

    # Crawler proxy (Playwright needs explicit proxy to reach X from China)
    proxy = os.environ.get("X_TRACE_PROXY")
    if proxy:
        config["crawler"]["proxy"] = proxy.strip()

    # Data vendors (comma-separated vendor chain)
    vendors = os.environ.get("X_TRACE_DATA_VENDORS_OHLCV")
    if vendors:
        config["data_vendors"]["ohlcv"] = vendors.strip()

    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": str(_PROJECT_ROOT),
    "state_dir": str(_STATE_DIR),
    # LLM (DeepSeek via OpenAI-compatible endpoint)
    "deepseek_api_key": os.getenv("DEEPSEEK_API_KEY", ""),
    "deepseek_base_url": "https://api.deepseek.com/v1",
    "deepseek_model": "deepseek-chat",
    # Market data backup keys (yfinance needs none)
    "alpha_vantage_api_key": os.getenv("ALPHA_VANTAGE_API_KEY", ""),
    "finnhub_api_key": os.getenv("FINNHUB_API_KEY", ""),
    # Vendor chain for OHLCV (primary, then fallbacks)
    "data_vendors": {
        "ohlcv": "yfinance,alpha_vantage,finnhub",
    },
    # Backtest windows
    "backtest_windows": ["1w", "1m", "3m", "to_date"],
    # Crawler
    "crawler": {
        "storage_state_path": str(_STATE_DIR / "state.json"),
        "request_interval_sec": 2.5,
        "max_retries": 3,
        "proxy": os.getenv("X_TRACE_PROXY", ""),  # e.g. http://127.0.0.1:7892
    },
    # Storage
    "storage": {
        "db_path": str(_STATE_DIR / "x-trace.db"),
    },
    # Reports
    "reports_dir": str(_STATE_DIR / "reports"),
    # Scoring weights (must sum to 1.0)
    "scoring_weights": {
        "hit_rate": 0.30,
        "avg_return": 0.20,
        "target_acc": 0.15,
        "sharpe": 0.10,
        "horizon": 0.05,
        "calibration": 0.10,
        "coverage": 0.10,
    },
})


_cached_config: dict | None = None


def get_config() -> dict:
    """Return a deep copy of the merged config (cached)."""
    global _cached_config
    if _cached_config is None:
        _cached_config = deepcopy(DEFAULT_CONFIG)
        # Re-evaluate state_dir from env so tests can isolate via X_TRACE_STATE_DIR.
        state_dir = Path(os.getenv("X_TRACE_STATE_DIR", _PROJECT_ROOT / ".x-trace"))
        _cached_config["state_dir"] = str(state_dir)
        _cached_config["storage"]["db_path"] = str(state_dir / "x-trace.db")
        _cached_config["crawler"]["storage_state_path"] = str(state_dir / "state.json")
        _cached_config["reports_dir"] = str(state_dir / "reports")
    return _cached_config


def ensure_state_dir() -> Path:
    """Create the state directory (for DB, session, reports) if missing."""
    path = Path(DEFAULT_CONFIG["state_dir"])
    path.mkdir(parents=True, exist_ok=True)
    Path(DEFAULT_CONFIG["reports_dir"]).mkdir(parents=True, exist_ok=True)
    return path
