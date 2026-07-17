"""Persistent valuation scan jobs.

Jobs are stored as JSON files under output/valuation_jobs so long-running
quarterly scans remain auditable across console restarts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.screening.batch_valuation import run_batch_valuation, save_valuation_scan
from src.screening.universe import load_universe
from src.utils.config import get_output_dir
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _job_dir() -> Path:
    path = get_output_dir("valuation_jobs")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    return _job_dir() / f"{job_id}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_job(options: dict) -> dict:
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
    job = {
        "job_id": job_id,
        "status": "queued",
        "message": "任务已创建",
        "created_at": _now(),
        "started_at": "",
        "finished_at": "",
        "options": options,
        "total": 0,
        "completed": 0,
        "current_ticker": "",
        "results": [],
        "errors": [],
        "scan_id": "",
        "json_path": "",
        "csv_path": "",
    }
    save_job(job)
    return job


def save_job(job: dict) -> None:
    job["updated_at"] = _now()
    path = _job_path(job["job_id"])
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def load_job(job_id: str) -> dict | None:
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_jobs(limit: int = 20) -> list[dict]:
    files = sorted(_job_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs = []
    for path in files[:limit]:
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return jobs


def latest_job() -> dict | None:
    jobs = list_jobs(limit=1)
    return jobs[0] if jobs else None


def has_running_job() -> bool:
    return any(
        job.get("status") in {"queued", "running"} and not is_stale(job)
        for job in list_jobs(limit=10)
    )


def is_stale(job: dict, *, stale_minutes: int = 10) -> bool:
    if job.get("status") not in {"queued", "running"}:
        return False
    stamp = job.get("updated_at") or job.get("started_at") or job.get("created_at")
    if not stamp:
        return True
    try:
        updated_at = datetime.fromisoformat(str(stamp))
    except ValueError:
        return True
    return datetime.now() - updated_at > timedelta(minutes=stale_minutes)


def run_job(job_id: str) -> dict:
    job = load_job(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    options = job.get("options", {})
    try:
        universe = load_universe(
            options.get("universe", "watchlist"),
            markets=set(options.get("markets") or []) or None,
            limit=options.get("limit"),
            exclude_risk=not options.get("include_risk", False),
            board=options.get("board") or None,
            sector=options.get("sector") or None,
        )
        job.update(
            {
                "status": "running",
                "message": "估值扫描运行中",
                "started_at": job.get("started_at") or _now(),
                "total": len(universe),
                "completed": 0,
                "current_ticker": "",
                "results": [],
                "errors": [],
            }
        )
        save_job(job)

        results = []
        for idx, item in enumerate(universe, start=1):
            ticker = item["ticker"]
            job.update(
                {
                    "completed": idx - 1,
                    "current_ticker": ticker,
                    "message": f"正在估值 {idx}/{len(universe)}: {ticker}",
                }
            )
            save_job(job)

            result = run_batch_valuation(
                ticker,
                item["market"],
                name=item.get("name", ""),
                sector=item.get("sector", ""),
                use_llm=options.get("use_llm", False),
                refresh_data=options.get("refresh_data", False),
            )
            results.append(result)
            row = {
                "ticker": result.ticker,
                "name": result.name,
                "market": result.market,
                "sector": result.sector,
                "current_price": result.current_price,
                "intrinsic_value": result.intrinsic_value,
                "action": result.action,
                "margin_of_safety_pct": result.margin_of_safety_pct,
                "quality_score": result.quality_score,
                "data_completeness": result.data_completeness,
                "reason": result.reason,
                "error": result.error,
            }
            job["results"].append(row)
            if result.error:
                job["errors"].append({"ticker": result.ticker, "error": result.error})
            job.update({"completed": idx, "current_ticker": ticker})
            save_job(job)

        results.sort(
            key=lambda r: (
                r.action != "strong_candidate",
                r.action != "deep_research",
                -(r.margin_of_safety or -999),
                -r.quality_score,
            )
        )
        saved = save_valuation_scan(
            results,
            metadata={
                "job_id": job_id,
                "universe_source": options.get("universe", "watchlist"),
                "markets": options.get("markets") or [],
                "limit": options.get("limit"),
                "use_llm": options.get("use_llm", False),
                "refresh_data": options.get("refresh_data", False),
                "exclude_risk": not options.get("include_risk", False),
                "board": options.get("board") or "",
                "sector": options.get("sector") or "",
                "universe_size": len(universe),
            },
        )
        job.update(
            {
                "status": "completed",
                "message": f"扫描完成：{len(results)} 只",
                "finished_at": _now(),
                "current_ticker": "",
                "scan_id": saved["scan_id"],
                "json_path": str(saved["json_path"]),
                "csv_path": str(saved["csv_path"]),
            }
        )
        save_job(job)
        return job
    except Exception as e:
        logger.exception("[ValuationJob] Job failed: %s", job_id)
        job.update(
            {
                "status": "failed",
                "message": f"扫描失败：{e}",
                "finished_at": _now(),
            }
        )
        save_job(job)
        return job
