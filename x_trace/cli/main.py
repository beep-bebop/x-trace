"""X-Trace CLI entrypoint.

Exposes: login / crawl / analyze / backtest / score / run.
Reports are written to ``.x-trace/reports/`` as JSON + Markdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path

import typer

from .. import __version__
from ..analyzer.tweet_analyzer import analyze_user_tweets
from ..backtest.engine import backtest_user_predictions
from ..backtest.windows import parse_windows
from ..config import ensure_state_dir, get_config
from ..crawler.session import has_session, login_interactive, login_with_cookies
from ..crawler.twitter_crawler import crawl_tweets
from ..scoring.scorer import BloggerScore, score_blogger
from ..storage import repository as repo

app = typer.Typer(
    name="x-trace",
    help="Backtest and score X (Twitter) bloggers' stock predictions.",
    no_args_is_help=True,
    add_completion=False,
)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise typer.BadParameter(f"Invalid date {s!r}; expected YYYY-MM-DD") from e


def _reports_dir() -> Path:
    ensure_state_dir()
    return Path(get_config()["reports_dir"])


def _write_reports(username: str, score: BloggerScore) -> tuple[Path, Path]:
    """Write JSON + Markdown reports for a BloggerScore. Returns (json_path, md_path)."""
    reports = _reports_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports / f"{username}_{timestamp}.json"
    md_path = reports / f"{username}_{timestamp}.md"

    # JSON report: include all predictions + backtest results for full audit
    preds = repo.get_predictions_for_user(username)
    results = repo.get_backtest_results_for_user(username)
    payload = {
        "username": username,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "score": score.model_dump(),
        "predictions": [p.model_dump(mode="json") for p in preds],
        "backtest_results": [r.model_dump(mode="json") for r in results],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Markdown report: human-readable summary
    lines = [
        f"# X-Trace Report: @{username}",
        "",
        f"- **Overall Score**: {score.overall_score:.2f} / 100 ({score.grade})",
        f"- **Sample Size**: {score.sample_size} backtest results",
        f"- **Generated**: {datetime.utcnow().isoformat()}Z",
        "",
    ]
    if score.notes:
        lines.append("## Notes")
        for n in score.notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## Per-Metric Breakdown")
    lines.append("")
    lines.append("| Metric | Raw Value | Sample | Weight | Score (0-100) |")
    lines.append("|---|---|---|---|---|")
    for m in score.per_metric:
        raw = "—" if m.raw_value is None else f"{m.raw_value:.4f}"
        lines.append(
            f"| {m.name} | {raw} | {m.sample_size} | {m.weight:.2f} | {m.normalized_score:.1f} |"
        )
    lines.append("")

    # Top hits and misses (1m window if available, else any)
    serious_preds = [p for p in preds if p.prediction_type in {"target_price", "directional", "entry_stop"} and p.ticker != "__NONE__"]
    pred_by_id = {p.prediction_id: p for p in serious_preds}
    serious_results = [r for r in results if r.prediction_id in pred_by_id]

    if serious_results:
        # Pick a representative window (prefer 1m, fall back to first seen)
        windows_seen = {r.window for r in serious_results}
        chosen_window = "1m" if "1m" in windows_seen else sorted(windows_seen)[0]
        window_results = [r for r in serious_results if r.window == chosen_window]

        # Top 5 hits (highest aligned return) and top 5 misses (lowest)
        def _aligned(r):
            p = pred_by_id[r.prediction_id]
            return -r.return_pct if p.direction == "bearish" else r.return_pct

        sorted_hits = sorted(window_results, key=_aligned, reverse=True)
        lines.append(f"## Top Calls (window: {chosen_window})")
        lines.append("")
        lines.append("| Date | Ticker | Direction | Return % | Correct? |")
        lines.append("|---|---|---|---|---|")
        for r in sorted_hits[:5]:
            p = pred_by_id[r.prediction_id]
            mark = "✓" if r.direction_correct else "✗"
            lines.append(
                f"| {r.base_date} | {p.ticker} | {p.direction} | {r.return_pct:+.2f}% | {mark} |"
            )
        lines.append("")

        lines.append(f"## Worst Calls (window: {chosen_window})")
        lines.append("")
        lines.append("| Date | Ticker | Direction | Return % | Correct? |")
        lines.append("|---|---|---|---|---|")
        for r in sorted_hits[-5:]:
            p = pred_by_id[r.prediction_id]
            mark = "✓" if r.direction_correct else "✗"
            lines.append(
                f"| {r.base_date} | {p.ticker} | {p.direction} | {r.return_pct:+.2f}% | {mark} |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.callback()
def _root(verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging")):
    """X-Trace: backtest and score X bloggers' stock predictions."""
    _setup_logging(verbose=verbose)
    ensure_state_dir()


@app.command()
def login():
    """Open a browser to log into X and save the session."""
    asyncio.run(login_interactive(headless=False))


@app.command("login-cookies")
def login_cookies(
    auth_token: str = typer.Option(..., "--auth-token", help="X auth_token cookie value"),
    ct0: str = typer.Option(..., "--ct0", help="X ct0 (CSRF) cookie value"),
):
    """Save an X session from browser cookies (no browser window needed).

    Get the two cookie values from your logged-in browser:
    DevTools (F12) -> Application -> Cookies -> https://x.com
    -> copy `auth_token` and `ct0` values.
    """
    path = login_with_cookies(auth_token, ct0)
    typer.secho(f"Session saved to {path}", fg=typer.colors.GREEN)
    typer.secho("Run `x-trace crawl` or `x-trace run` next.", fg=typer.colors.CYAN)


@app.command()
def crawl(
    username: str = typer.Option(..., "--username", "-u", help="X handle without @"),
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="End date YYYY-MM-DD"),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser headless"),
):
    """Crawl tweets for a user between two dates."""
    if not has_session():
        typer.secho("No saved session. Run `x-trace login` first.", fg=typer.colors.RED)
        raise typer.Exit(1)
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if end_d < start_d:
        raise typer.BadParameter("--end must be on or after --start")
    new_count = asyncio.run(crawl_tweets(username, start_d, end_d, headless=headless))
    typer.secho(f"Crawled {new_count} new tweets for @{username}", fg=typer.colors.GREEN)


@app.command()
def analyze(
    username: str = typer.Option(..., "--username", "-u", help="X handle without @"),
):
    """Run DeepSeek over unanalyzed tweets for a user."""
    new_preds = analyze_user_tweets(username)
    typer.secho(f"Persisted {new_preds} new predictions for @{username}", fg=typer.colors.GREEN)


@app.command()
def backtest(
    username: str = typer.Option(..., "--username", "-u", help="X handle without @"),
    windows: str = typer.Option("1m,3m", "--windows", "-w", help="Comma-separated: 1w,1m,3m,to_date"),
    max_workers: int = typer.Option(4, "--max-workers", help="Parallel OHLCV fetches"),
):
    """Backtest all predictions for a user across the given windows."""
    wins = parse_windows(windows)
    new_count = backtest_user_predictions(username, wins, max_workers=max_workers)
    typer.secho(f"Persisted {new_count} new backtest results for @{username}", fg=typer.colors.GREEN)


@app.command()
def score(
    username: str = typer.Option(..., "--username", "-u", help="X handle without @"),
):
    """Compute the multi-dimensional score and write JSON + Markdown reports."""
    result = score_blogger(username)
    json_path, md_path = _write_reports(username, result)
    typer.secho(
        f"@{username}: {result.overall_score:.2f}/100 (grade {result.grade})  "
        f"sample={result.sample_size}",
        fg=typer.colors.GREEN,
    )
    typer.secho(f"  JSON: {json_path}", fg=typer.colors.CYAN)
    typer.secho(f"  Markdown: {md_path}", fg=typer.colors.CYAN)


@app.command()
def run(
    username: str = typer.Option(..., "--username", "-u", help="X handle without @"),
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="End date YYYY-MM-DD"),
    windows: str = typer.Option("1m,3m", "--windows", "-w", help="Comma-separated: 1w,1m,3m,to_date"),
    headless: bool = typer.Option(True, "--headless/--headed", help="Run browser headless"),
    max_workers: int = typer.Option(4, "--max-workers", help="Parallel OHLCV fetches"),
):
    """End-to-end: crawl → analyze → backtest → score → write reports."""
    start_d = _parse_date(start)
    end_d = _parse_date(end)

    if not has_session():
        typer.secho("No saved session. Run `x-trace login` first.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"[1/4] Crawling @{username} {start} → {end} ...", fg=typer.colors.CYAN)
    n_tweets = asyncio.run(crawl_tweets(username, start_d, end_d, headless=headless))
    typer.secho(f"      +{n_tweets} new tweets", fg=typer.colors.GREEN)

    typer.secho("[2/4] Analyzing tweets with DeepSeek ...", fg=typer.colors.CYAN)
    n_preds = analyze_user_tweets(username)
    typer.secho(f"      +{n_preds} new predictions", fg=typer.colors.GREEN)

    typer.secho(f"[3/4] Backtesting over windows={windows} ...", fg=typer.colors.CYAN)
    wins = parse_windows(windows)
    n_results = backtest_user_predictions(username, wins, max_workers=max_workers)
    typer.secho(f"      +{n_results} new results", fg=typer.colors.GREEN)

    typer.secho("[4/4] Scoring ...", fg=typer.colors.CYAN)
    result = score_blogger(username)
    json_path, md_path = _write_reports(username, result)
    typer.secho(
        f"      @{username}: {result.overall_score:.2f}/100 (grade {result.grade})  "
        f"sample={result.sample_size}",
        fg=typer.colors.GREEN,
    )
    typer.secho(f"  JSON: {json_path}", fg=typer.colors.CYAN)
    typer.secho(f"  Markdown: {md_path}", fg=typer.colors.CYAN)


@app.command()
def compare():
    """Generate a leaderboard Markdown report comparing all evaluated bloggers."""
    usernames = repo.get_all_usernames()
    if not usernames:
        typer.secho("No bloggers in the DB. Run `x-trace run` first.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"Scoring {len(usernames)} bloggers ...", fg=typer.colors.CYAN)
    rows: list[tuple[str, BloggerScore]] = []
    for u in usernames:
        try:
            s = score_blogger(u)
            rows.append((u, s))
        except Exception as e:
            typer.secho(f"  @{u}: FAILED ({e})", fg=typer.colors.RED)

    rows.sort(key=lambda r: r[1].overall_score, reverse=True)

    reports = _reports_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = reports / f"leaderboard_{timestamp}.md"
    json_path = reports / f"leaderboard_{timestamp}.json"

    # JSON
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "bloggers": [
            {
                "username": u,
                **s.model_dump(),
            }
            for u, s in rows
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Markdown
    lines = [
        "# X-Trace Leaderboard",
        "",
        f"- **Generated**: {datetime.utcnow().isoformat()}Z",
        f"- **Bloggers**: {len(rows)}",
        "",
        "| Rank | Blogger | Score | Grade | Sample | Hit Rate | Avg Return | Sharpe | Coverage |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (u, s) in enumerate(rows, 1):
        hit = next((m for m in s.per_metric if m.name == "hit_rate"), None)
        ret = next((m for m in s.per_metric if m.name == "avg_return"), None)
        sha = next((m for m in s.per_metric if m.name == "sharpe"), None)
        cov = next((m for m in s.per_metric if m.name == "coverage"), None)
        hit_val = "—" if not hit or hit.raw_value is None else f"{hit.raw_value:.1%}"
        ret_val = "—" if not ret or ret.raw_value is None else f"{ret.raw_value:+.2f}%"
        sha_val = "—" if not sha or sha.raw_value is None else f"{sha.raw_value:.2f}"
        cov_val = "—" if not cov or cov.raw_value is None else f"{int(cov.raw_value)}"
        lines.append(
            f"| {i} | @{u} | {s.overall_score:.2f} | {s.grade} | {s.sample_size} | "
            f"{hit_val} | {ret_val} | {sha_val} | {cov_val} |"
        )
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    typer.secho("Leaderboard:", fg=typer.colors.GREEN)
    for i, (u, s) in enumerate(rows, 1):
        typer.secho(
            f"  {i}. @{u}: {s.overall_score:.2f}/100 ({s.grade})  sample={s.sample_size}",
            fg=typer.colors.CYAN,
        )
    typer.secho(f"  Markdown: {md_path}", fg=typer.colors.CYAN)
    typer.secho(f"  JSON: {json_path}", fg=typer.colors.CYAN)


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
):
    """Launch the web UI (FastAPI + SPA frontend)."""
    import uvicorn
    from ..web.app import app as web_app

    typer.secho(f"Starting X-Trace Web UI at http://{host}:{port}", fg=typer.colors.CYAN)
    typer.secho("Press Ctrl+C to stop.", fg=typer.colors.YELLOW)
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def version():
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
