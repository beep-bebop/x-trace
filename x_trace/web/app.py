"""FastAPI backend exposing X-Trace functionality as REST endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..analyzer.tweet_analyzer import analyze_user_tweets
from ..backtest.engine import backtest_user_predictions
from ..backtest.windows import parse_windows
from ..config import ensure_state_dir, get_config
from ..crawler.session import has_session
from ..crawler.twitter_crawler import crawl_tweets
from ..scoring.scorer import score_blogger
from ..storage import repository as repo

logger = logging.getLogger(__name__)

app = FastAPI(title="X-Trace API", version="1.0.0")

_STATIC_DIR = Path(__file__).parent / "static"

# --- Background job tracking ---
_jobs: dict[str, dict[str, Any]] = {}
_job_lock = threading.Lock()


def _run_job(job_id: str, fn, *args, **kwargs):
    """Run a function in a background thread and track its status."""
    with _job_lock:
        _jobs[job_id] = {"status": "running", "result": None, "error": None, "started_at": datetime.utcnow().isoformat()}
    try:
        result = fn(*args, **kwargs)
        with _job_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result
            _jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        with _job_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
        logger.exception("Job %s failed", job_id)


# --- Request models ---
class CrawlRequest(BaseModel):
    username: str
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD
    headless: bool = True


class AnalyzeRequest(BaseModel):
    username: str


class BacktestRequest(BaseModel):
    username: str
    windows: str = "1m,3m"
    max_workers: int = 1


class RunRequest(BaseModel):
    username: str
    start: str
    end: str
    windows: str = "1m,3m"
    headless: bool = True
    max_workers: int = 1


# --- Helpers ---
def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {s}, expected YYYY-MM-DD")


def _tweet_to_dict(t) -> dict:
    return {
        "tweet_id": t.tweet_id,
        "username": t.username,
        "text": t.text,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "reply_count": t.reply_count,
        "retweet_count": t.retweet_count,
        "like_count": t.like_count,
        "urls": t.urls,
    }


def _pred_to_dict(p) -> dict:
    return {
        "prediction_id": p.prediction_id,
        "tweet_id": p.tweet_id,
        "ticker": p.ticker,
        "company_name": p.company_name,
        "direction": p.direction,
        "prediction_type": p.prediction_type,
        "target_price": p.target_price,
        "entry_price": p.entry_price,
        "stop_loss": p.stop_loss,
        "time_horizon": p.time_horizon,
        "confidence": p.confidence,
        "rationale": p.rationale,
        "analyzed_at": p.analyzed_at.isoformat() if p.analyzed_at else None,
    }


def _result_to_dict(r) -> dict:
    return {
        "result_id": r.result_id,
        "prediction_id": r.prediction_id,
        "window": r.window,
        "base_date": r.base_date.isoformat() if r.base_date else None,
        "end_date": r.end_date.isoformat() if r.end_date else None,
        "base_close": r.base_close,
        "end_close": r.end_close,
        "return_pct": r.return_pct,
        "max_drawdown_pct": r.max_drawdown_pct,
        "max_runup_pct": r.max_runup_pct,
        "direction_correct": r.direction_correct,
        "hit_target": r.hit_target,
        "spy_return_pct": r.spy_return_pct,
        "alpha_pct": r.alpha_pct,
    }


# ===========================================================================
# API Routes
# ===========================================================================

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/bloggers")
async def list_bloggers():
    """List all bloggers with tweet counts and quick stats."""
    usernames = repo.get_all_usernames()
    bloggers = []
    for u in usernames:
        tweets = repo.get_tweets_by_user(u)
        preds = repo.get_predictions_for_user(u)
        results = repo.get_backtest_results_for_user(u)
        serious_preds = [p for p in preds if p.ticker != "__NONE__"]
        bloggers.append({
            "username": u,
            "tweet_count": len(tweets),
            "prediction_count": len(serious_preds),
            "result_count": len(results),
        })
    bloggers.sort(key=lambda b: b["tweet_count"], reverse=True)
    return bloggers


@app.get("/api/leaderboard")
async def leaderboard():
    """Compute scores for all bloggers and return sorted leaderboard."""
    usernames = repo.get_all_usernames()
    rows = []
    for u in usernames:
        try:
            s = score_blogger(u)
            rows.append({
                "username": u,
                "overall_score": s.overall_score,
                "grade": s.grade,
                "sample_size": s.sample_size,
                "per_metric": [m.model_dump() for m in s.per_metric],
                "notes": s.notes,
            })
        except Exception as e:
            logger.warning("Failed to score %s: %s", u, e)
    rows.sort(key=lambda r: r["overall_score"], reverse=True)
    return rows


@app.get("/api/bloggers/{username}/score")
async def get_score(username: str):
    """Get detailed score for a blogger."""
    try:
        s = score_blogger(username)
        return s.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bloggers/{username}/tweets")
async def get_tweets(username: str, limit: int = 200):
    """Get tweets for a blogger."""
    tweets = repo.get_tweets_by_user(username)
    return [_tweet_to_dict(t) for t in tweets[:limit]]


@app.get("/api/bloggers/{username}/predictions")
async def get_predictions(username: str):
    """Get predictions for a blogger (excludes __NONE__ sentinels)."""
    preds = repo.get_predictions_for_user(username)
    serious = [p for p in preds if p.ticker != "__NONE__"]
    return [_pred_to_dict(p) for p in serious]


@app.get("/api/bloggers/{username}/results")
async def get_results(username: str):
    """Get backtest results for a blogger."""
    results = repo.get_backtest_results_for_user(username)
    return [_result_to_dict(r) for r in results]


@app.get("/api/bloggers/{username}/detail")
async def get_detail(username: str):
    """Get combined detail: score + tweets + predictions + results."""
    try:
        s = score_blogger(username)
        score_data = s.model_dump()
    except Exception:
        score_data = None

    tweets = repo.get_tweets_by_user(username)
    preds = repo.get_predictions_for_user(username)
    serious_preds = [p for p in preds if p.ticker != "__NONE__"]
    results = repo.get_backtest_results_for_user(username)

    return {
        "username": username,
        "score": score_data,
        "tweet_count": len(tweets),
        "prediction_count": len(serious_preds),
        "result_count": len(results),
        "tweets": [_tweet_to_dict(t) for t in tweets[:100]],
        "predictions": [_pred_to_dict(p) for p in serious_preds[:200]],
        "results": [_result_to_dict(r) for r in results[:500]],
    }


@app.post("/api/bloggers/{username}/crawl")
async def api_crawl(username: str, req: CrawlRequest):
    """Trigger crawl in background."""
    if not has_session():
        raise HTTPException(status_code=400, detail="No saved session. Run 'x-trace login' first.")
    job_id = f"crawl_{username}_{datetime.utcnow().strftime('%H%M%S')}"
    start_d = _parse_date(req.start)
    end_d = _parse_date(req.end)
    t = threading.Thread(
        target=_run_job,
        args=(job_id, asyncio.run, crawl_tweets(username, start_d, end_d, headless=req.headless)),
    )
    t.start()
    return {"job_id": job_id, "status": "started"}


@app.post("/api/bloggers/{username}/analyze")
async def api_analyze(username: str, req: AnalyzeRequest):
    """Trigger analysis in background."""
    job_id = f"analyze_{username}_{datetime.utcnow().strftime('%H%M%S')}"
    t = threading.Thread(
        target=_run_job,
        args=(job_id, analyze_user_tweets, username),
    )
    t.start()
    return {"job_id": job_id, "status": "started"}


@app.post("/api/bloggers/{username}/backtest")
async def api_backtest(username: str, req: BacktestRequest):
    """Trigger backtest in background."""
    job_id = f"backtest_{username}_{datetime.utcnow().strftime('%H%M%S')}"
    wins = parse_windows(req.windows)
    t = threading.Thread(
        target=_run_job,
        args=(job_id, backtest_user_predictions, username, wins),
        kwargs={"max_workers": req.max_workers},
    )
    t.start()
    return {"job_id": job_id, "status": "started"}


@app.post("/api/bloggers/{username}/run")
async def api_run(username: str, req: RunRequest):
    """Trigger full pipeline in background."""
    if not has_session():
        raise HTTPException(status_code=400, detail="No saved session. Run 'x-trace login' first.")

    def _pipeline():
        start_d = _parse_date(req.start)
        end_d = _parse_date(req.end)
        wins = parse_windows(req.windows)
        n_tweets = asyncio.run(crawl_tweets(username, start_d, end_d, headless=req.headless))
        n_preds = analyze_user_tweets(username)
        n_results = backtest_user_predictions(username, wins, max_workers=req.max_workers)
        score = score_blogger(username)
        return {
            "tweets": n_tweets,
            "predictions": n_preds,
            "results": n_results,
            "score": score.model_dump(),
        }

    job_id = f"run_{username}_{datetime.utcnow().strftime('%H%M%S')}"
    t = threading.Thread(target=_run_job, args=(job_id, _pipeline))
    t.start()
    return {"job_id": job_id, "status": "started"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    """Check job status."""
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/reports")
async def list_reports():
    """List report files with parsed metadata (score, grade, blogger)."""
    reports_dir = Path(get_config()["reports_dir"])
    if not reports_dir.exists():
        return {"blogger": [], "leaderboard": []}

    files = sorted(reports_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    blogger_reports = []
    leaderboard_reports = []

    for f in files[:80]:
        entry = {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "type": "md" if f.suffix == ".md" else "json",
        }
        # Parse JSON to extract metadata
        if f.suffix == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if "bloggers" in data:
                    # Leaderboard report
                    entry["blogger_count"] = len(data["bloggers"])
                    entry["top_blogger"] = data["bloggers"][0]["username"] if data["bloggers"] else None
                    entry["top_score"] = data["bloggers"][0]["overall_score"] if data["bloggers"] else None
                    leaderboard_reports.append(entry)
                elif "score" in data:
                    # Blogger report
                    entry["username"] = data.get("username", "")
                    entry["score"] = data["score"]["overall_score"]
                    entry["grade"] = data["score"]["grade"]
                    entry["sample_size"] = data["score"]["sample_size"]
                    entry["prediction_count"] = len(data.get("predictions", []))
                    entry["result_count"] = len(data.get("backtest_results", []))
                    blogger_reports.append(entry)
            except (json.JSONDecodeError, KeyError, IndexError):
                entry["type"] = "json"
                blogger_reports.append(entry)
        elif f.suffix == ".md":
            # For markdown, extract username from filename
            parts = f.stem.split("_")
            if "leaderboard" in f.name:
                leaderboard_reports.append(entry)
            elif parts:
                entry["username"] = parts[0]
                blogger_reports.append(entry)

    return {"blogger": blogger_reports, "leaderboard": leaderboard_reports}


@app.get("/api/reports/{filename}")
async def read_report(filename: str):
    """Read the content of a report file."""
    reports_dir = Path(get_config()["reports_dir"])
    filepath = reports_dir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    # Prevent path traversal
    if not str(filepath.resolve()).startswith(str(reports_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    content = filepath.read_text(encoding="utf-8")
    return {"filename": filename, "content": content, "type": filepath.suffix.lstrip(".")}


@app.get("/api/session")
async def session_status():
    """Check if X session exists."""
    return {"has_session": has_session()}


# --- Static file serving (SPA) ---
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(_STATIC_DIR / "index.html"))
