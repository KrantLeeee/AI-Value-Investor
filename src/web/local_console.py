"""Minimal local web console.

This is intentionally dependency-free so the project has a reliable one-click
entry point before the full batch valuation workbench is built.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import sqlite3
import threading
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote
from uuid import uuid4

from src.utils.config import get_db_path, get_project_root

PROJECT_ROOT = get_project_root()
REPORT_DIR = PROJECT_ROOT / "output"
LEGACY_REPORT_DIR = REPORT_DIR / "reports"
CURRENT_JOB: dict = {
    "status": "idle",
    "message": "暂无运行中的任务",
    "started_at": "",
    "finished_at": "",
}
JOB_LOCK = threading.Lock()
ACTIVE_JOB_THREADS: dict[str, threading.Thread] = {}
REPORT_JOB: dict = {
    "job_id": "",
    "status": "idle",
    "message": "暂无运行中的研报任务",
    "stage": "",
    "progress_pct": 0,
    "ticker": "",
    "market": "",
    "name": "",
    "sector": "",
    "started_at": "",
    "updated_at": "",
    "finished_at": "",
    "report_path": "",
    "report_url": "",
    "error": "",
    "events": [],
}
REPORT_LOCK = threading.Lock()
STOCK_UNIVERSE_JOB: dict = {
    "status": "idle",
    "message": "暂无股票库刷新任务",
    "started_at": "",
    "finished_at": "",
    "count": 0,
    "error": "",
}
STOCK_UNIVERSE_LOCK = threading.Lock()
STOCK_UNIVERSE_THREAD: threading.Thread | None = None
SECRET_ENV_KEYS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "FMP_API_KEY",
    "QVERIS_API_KEYS",
    "TUSHARE_FAST_TOKEN",
    "TUSHARE_SUPER_API_KEY",
    "TUSHARE_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
RUNTIME_ENV_KEYS = [
    "DB_AUTO_MAINTENANCE",
    "DB_SIGNAL_RETENTION_DAYS",
    "DB_LOG_RETENTION_DAYS",
    "DB_PRICE_RETENTION_DAYS",
    "DB_FINANCIAL_RETENTION_DAYS",
    "TUSHARE_CLIENT_MODE",
    "TUSHARE_FAST_API_URL",
    "TUSHARE_SUPER_API_URL",
    "TUSHARE_TIMEOUT",
    "TUSHARE_DISABLE_PROXY",
    "USE_INDUSTRY_ENGINE_V3",
    "SKIP_AKSHARE",
    "FETCH_DELAY",
    "FETCH_DELAY_BETWEEN_SOURCES",
    "FETCH_DELAY_BETWEEN_TICKERS",
]


def _connect_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((_connect_host(host), port), timeout=0.25):
            return True
    except OSError:
        return False


def _looks_like_console(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            text = resp.read(4096).decode("utf-8", errors="replace")
        return "AI Value Investor 控制台" in text
    except Exception:
        return False


def _find_free_port(host: str, start_port: int, limit: int = 20) -> int:
    for port in range(start_port, start_port + limit):
        if not _is_port_open(host, port):
            return port
    raise OSError(f"No free local port found from {start_port} to {start_port + limit - 1}")


def _mask_env(name: str, fallback: str | None = None) -> dict:
    value = os.getenv(name) or (os.getenv(fallback) if fallback else "") or ""
    value = value.strip()
    if not value:
        return {"name": name, "status": "missing", "display": "未配置"}
    if len(value) <= 8:
        masked = "*" * len(value)
    else:
        masked = f"{value[:4]}...{value[-4:]}"
    return {"name": name, "status": "ok", "display": masked}


def _config_snapshot() -> dict:
    keys = [
        _mask_env("OPENAI_API_KEY"),
        _mask_env("DEEPSEEK_API_KEY"),
        _mask_env("ANTHROPIC_API_KEY"),
        _mask_env("TAVILY_API_KEY"),
        _mask_env("FMP_API_KEY"),
        _mask_env("QVERIS_API_KEYS", "QVERIS_API_KEY"),
        _mask_env("TUSHARE_FAST_TOKEN", "TUSHARE_TOKEN"),
        _mask_env("TUSHARE_SUPER_API_KEY"),
        _mask_env("TELEGRAM_BOT_TOKEN"),
        _mask_env("TELEGRAM_CHAT_ID"),
    ]
    runtime = {
        "DB_AUTO_MAINTENANCE": os.getenv("DB_AUTO_MAINTENANCE", "true"),
        "DB_SIGNAL_RETENTION_DAYS": os.getenv("DB_SIGNAL_RETENTION_DAYS", "30"),
        "DB_LOG_RETENTION_DAYS": os.getenv("DB_LOG_RETENTION_DAYS", "30"),
        "DB_PRICE_RETENTION_DAYS": os.getenv("DB_PRICE_RETENTION_DAYS", "2555"),
        "DB_FINANCIAL_RETENTION_DAYS": os.getenv("DB_FINANCIAL_RETENTION_DAYS", "3650"),
        "TUSHARE_CLIENT_MODE": os.getenv("TUSHARE_CLIENT_MODE", "auto"),
        "TUSHARE_FAST_API_URL": os.getenv(
            "TUSHARE_FAST_API_URL",
            "https://fastapic.stockai888.top",
        ),
        "TUSHARE_SUPER_API_URL": os.getenv(
            "TUSHARE_SUPER_API_URL",
            "https://ai-tool.indevs.in/tushare/pro",
        ),
        "TUSHARE_DISABLE_PROXY": os.getenv("TUSHARE_DISABLE_PROXY", "false"),
        "USE_INDUSTRY_ENGINE_V3": os.getenv("USE_INDUSTRY_ENGINE_V3", "false"),
        "SKIP_AKSHARE": os.getenv("SKIP_AKSHARE", "true"),
    }
    return {"keys": keys, "runtime": runtime}


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _read_env_lines() -> list[str]:
    path = _env_path()
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _parse_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read_env_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _clean_env_value(value.strip())
    return values


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _mask_value(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _write_env_updates(updates: dict[str, str]) -> Path:
    allowed = set(SECRET_ENV_KEYS + RUNTIME_ENV_KEYS)
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        raise ValueError("没有可保存的配置项")

    path = _env_path()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = PROJECT_ROOT / f".env.backup-{timestamp}"
    if path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        backup_path.write_text("", encoding="utf-8")

    lines = _read_env_lines()
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            new_lines.append(line)
            continue
        key, _value = line.split("=", 1)
        clean_key = key.strip()
        if clean_key in updates:
            new_lines.append(f"{clean_key}={updates[clean_key]}")
            seen.add(clean_key)
        else:
            new_lines.append(line)

    missing = [key for key in updates if key not in seen]
    if missing and new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key in missing:
        new_lines.append(f"{key}={updates[key]}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value
    return backup_path


def _save_settings_from_form(form: dict[str, list[str]]) -> tuple[bool, str]:
    updates: dict[str, str] = {}
    for key in SECRET_ENV_KEYS:
        value = (form.get(key) or [""])[0].strip()
        if value:
            updates[key] = value
    for key in RUNTIME_ENV_KEYS:
        if key in form:
            updates[key] = (form.get(key) or [""])[0].strip()

    try:
        backup_path = _write_env_updates(updates)
        return True, f"设置已保存，备份文件：{backup_path.name}"
    except Exception as e:
        return False, f"保存失败：{e}"


def _db_stats() -> dict:
    db_path = get_db_path()
    stats = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_mb": round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0,
        "tables": [],
    }
    if not db_path.exists():
        return stats

    table_defs = [
        ("daily_prices", "date"),
        ("income_statements", "period_end_date"),
        ("balance_sheets", "period_end_date"),
        ("cash_flows", "period_end_date"),
        ("financial_metrics", "date"),
        ("stock_universe", "updated_at"),
        ("agent_signals", "created_at"),
        ("scan_logs", "created_at"),
    ]
    with sqlite3.connect(str(db_path)) as conn:
        for table, date_col in table_defs:
            try:
                row = conn.execute(
                    f"SELECT COUNT(*), MIN({date_col}), MAX({date_col}) FROM {table}"
                ).fetchone()
                stats["tables"].append(
                    {
                        "name": table,
                        "count": row[0],
                        "min_date": row[1] or "-",
                        "max_date": row[2] or "-",
                    }
                )
            except sqlite3.Error:
                stats["tables"].append(
                    {"name": table, "count": "-", "min_date": "-", "max_date": "-"}
                )
    return stats


def _reports() -> list[dict]:
    items = []
    seen: set[Path] = set()
    report_paths = []
    if REPORT_DIR.exists():
        report_paths.extend(REPORT_DIR.glob("*.md"))
    if LEGACY_REPORT_DIR.exists():
        report_paths.extend(LEGACY_REPORT_DIR.glob("*.md"))
    for path in sorted(report_paths, key=lambda p: p.stat().st_mtime, reverse=True):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "url": f"/reports/{path.name}",
                "path": str(path),
                "size_kb": round(stat.st_size / 1024, 1),
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return items


def _report_url_from_path(path_value: str | Path | None) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    try:
        path = path.resolve()
        output_root = REPORT_DIR.resolve()
        legacy_root = LEGACY_REPORT_DIR.resolve()
        if path.parent not in {output_root, legacy_root}:
            return ""
    except Exception:
        return ""
    if path.suffix.lower() != ".md":
        return ""
    return f"/reports/{quote(path.name)}"


def _latest_valuation_scan() -> dict | None:
    try:
        from src.screening.batch_valuation import load_latest_scan

        return load_latest_scan()
    except Exception:
        return None


def _load_valuation_scan(scan_id: str) -> dict | None:
    path = PROJECT_ROOT / "output" / "valuation_scans" / f"{Path(scan_id).stem}.json"
    if not path.exists():
        return None
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _recent_jobs(limit: int = 8) -> list[dict]:
    try:
        from src.screening.jobs import list_jobs

        return list_jobs(limit=limit)
    except Exception:
        return []


def _job_is_stale(job: dict) -> bool:
    try:
        from src.screening.jobs import is_stale

        return is_stale(job)
    except Exception:
        return False


def _start_job_thread(job_id: str) -> None:
    existing = ACTIVE_JOB_THREADS.get(job_id)
    if existing and existing.is_alive():
        return
    thread = threading.Thread(target=_run_scan_job, args=({"job_id": job_id},), daemon=True)
    ACTIVE_JOB_THREADS[job_id] = thread
    thread.start()


def _latest_health() -> list[dict]:
    try:
        from src.web.health import load_latest_health

        return load_latest_health()
    except Exception:
        return []


def _latest_maintenance() -> dict | None:
    try:
        from src.data.maintenance import load_latest_maintenance

        return load_latest_maintenance()
    except Exception:
        return None


def _stock_universe_stats() -> dict:
    try:
        from src.data.database import get_stock_universe_stats

        return get_stock_universe_stats()
    except Exception as e:
        return {"total": 0, "updated_at": "", "by_market": [], "by_board": [], "error": str(e)}


def _job_snapshot() -> dict:
    try:
        from src.screening.jobs import latest_job

        job = latest_job()
        if job:
            return job
    except Exception:
        pass
    with JOB_LOCK:
        return dict(CURRENT_JOB)


def _set_job(**kwargs) -> None:
    with JOB_LOCK:
        CURRENT_JOB.update(kwargs)


def _report_job_dir() -> Path:
    path = PROJECT_ROOT / "output" / "report_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_job_path() -> Path:
    return _report_job_dir() / "latest.json"


def _save_persisted_report_job(job: dict) -> None:
    try:
        _report_job_path().write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_persisted_report_job() -> dict | None:
    path = _report_job_path()
    if not path.exists():
        reports = _reports()
        if not reports:
            return None
        latest = reports[0]
        report_path = Path(latest.get("path") or (REPORT_DIR / latest["name"]))
        return {
            "job_id": "",
            "status": "completed",
            "message": "最近一份研报可阅读",
            "stage": "已完成",
            "progress_pct": 100,
            "ticker": latest["name"].split("_", 1)[0],
            "market": "",
            "name": "",
            "sector": "",
            "started_at": "",
            "updated_at": latest["mtime"],
            "finished_at": latest["mtime"],
            "report_path": str(report_path),
            "report_url": latest["url"],
            "error": "",
            "events": [
                {
                    "time": latest["mtime"],
                    "message": f"发现最近研报：{latest['name']}",
                }
            ],
        }
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(job, dict):
        return job
    return None


def _report_snapshot() -> dict:
    with REPORT_LOCK:
        job = dict(REPORT_JOB)
    if job.get("status") != "idle":
        return job
    persisted = _load_persisted_report_job()
    return persisted or job


def _set_report(**kwargs) -> None:
    event = kwargs.pop("event", None)
    with REPORT_LOCK:
        kwargs.setdefault("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        REPORT_JOB.update(kwargs)
        if event:
            events = list(REPORT_JOB.get("events") or [])
            events.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "message": str(event),
            })
            REPORT_JOB["events"] = events[-30:]
        _save_persisted_report_job(REPORT_JOB)


def _stock_universe_job_snapshot() -> dict:
    with STOCK_UNIVERSE_LOCK:
        return dict(STOCK_UNIVERSE_JOB)


def _set_stock_universe_job(**kwargs) -> None:
    with STOCK_UNIVERSE_LOCK:
        STOCK_UNIVERSE_JOB.update(kwargs)


def _run_stock_universe_refresh() -> None:
    try:
        _set_stock_universe_job(
            status="running",
            message="正在从 Tushare 刷新 A 股股票库",
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            finished_at="",
            count=0,
            error="",
        )
        from src.screening.universe import refresh_a_share_universe_from_tushare

        count = refresh_a_share_universe_from_tushare()
        if count > 0:
            _set_stock_universe_job(
                status="completed",
                message=f"A 股股票库刷新完成：{count} 只",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                count=count,
                error="",
            )
        else:
            _set_stock_universe_job(
                status="failed",
                message="A 股股票库刷新失败：Tushare 未返回股票列表",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                count=0,
                error="Tushare fast/super/official 均未返回 stock_basic 数据，请检查网络或 token。",
            )
    except Exception as e:
        _set_stock_universe_job(
            status="failed",
            message=f"A 股股票库刷新失败：{e}",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            error=str(e),
        )


def _start_stock_universe_refresh() -> tuple[bool, str]:
    global STOCK_UNIVERSE_THREAD
    if STOCK_UNIVERSE_THREAD and STOCK_UNIVERSE_THREAD.is_alive():
        return False, "已有股票库刷新任务正在运行"
    STOCK_UNIVERSE_THREAD = threading.Thread(target=_run_stock_universe_refresh, daemon=True)
    STOCK_UNIVERSE_THREAD.start()
    return True, "股票库刷新已启动，页面会自动更新状态"


def _run_scan_job(options: dict) -> None:
    from src.screening.jobs import run_job

    try:
        run_job(options["job_id"])
        job = _job_snapshot()
        _set_job(
            status=job.get("status", "completed"),
            message=job.get("message", "扫描完成"),
            finished_at=job.get("finished_at", ""),
        )
    except Exception as e:
        _set_job(
            status="failed",
            message=f"扫描失败：{e}",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )


def _start_scan_from_form(form: dict[str, list[str]]) -> tuple[bool, str]:
    from src.screening.jobs import create_job, has_running_job

    if has_running_job():
        return False, "已有估值扫描正在运行"

    universe = (form.get("universe") or ["watchlist"])[0]
    if universe not in {"watchlist", "a_share", "hk", "all"}:
        return False, "未知股票池"

    limit_raw = (form.get("limit") or [""])[0].strip()
    limit = int(limit_raw) if limit_raw else None
    confirm_full = "confirm_full_scan" in form
    if universe != "watchlist" and limit is None and not confirm_full:
        return False, "全市场扫描需要填写 limit，或勾选确认全量扫描"

    markets = form.get("markets") or ["a_share", "hk"]
    options = {
        "universe": universe,
        "markets": markets,
        "limit": limit,
        "board": (form.get("board") or [""])[0].strip(),
        "sector": (form.get("sector") or [""])[0].strip(),
        "refresh_data": "refresh_data" in form,
        "use_llm": "use_llm" in form,
        "include_risk": "include_risk" in form,
    }
    job = create_job(options)
    options["job_id"] = job["job_id"]
    _set_job(
        status="running",
        message=f"估值扫描已启动：{job['job_id']}",
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        finished_at="",
    )
    _start_job_thread(job["job_id"])
    return True, "估值扫描已启动"


def _run_report_job(options: dict) -> None:
    ticker = options["ticker"]
    market = options["market"]
    name = options.get("name", "")
    sector = options.get("sector", "")
    try:
        _set_report(
            status="running",
            stage="准备公司信息",
            progress_pct=10,
            message=f"正在准备 {ticker} 的公司信息",
            event="开始准备公司信息",
        )
        from src.agents.registry import run_all_agents
        from src.data.fetcher import Fetcher

        basics = None
        try:
            basics = Fetcher().fetch_company_basics(ticker, market)
        except Exception:
            basics = None
        if basics and name:
            basics = dict(basics)
            basics["company_name"] = name
            if sector and not basics.get("industry"):
                basics["industry"] = sector
            basics["source"] = f"{basics.get('source', 'unknown')}+valuation_scan_override"
        elif not basics and name:
            basics = {
                "company_name": name,
                "industry": sector or "未知",
                "main_business": "",
                "source": "valuation_scan_override",
            }
        _set_report(
            status="running",
            stage="多 Agent 分析与研报写作",
            progress_pct=35,
            message=f"{ticker} 正在运行完整 Agent 流程，可能需要几分钟",
            event="进入多 Agent 分析与研报写作",
        )
        _, report_path = run_all_agents(
            ticker,
            market,
            quick=False,
            company_context_override=basics,
        )
        report_url = _report_url_from_path(report_path)
        _set_report(
            status="completed",
            stage="已完成",
            progress_pct=100,
            message=f"{ticker} 研报生成完成",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            report_path=str(report_path),
            report_url=report_url,
            error="",
            event="研报生成完成",
        )
    except Exception as e:
        _set_report(
            status="failed",
            stage="失败",
            progress_pct=100,
            message=f"{ticker} 研报生成失败：{e}",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            error=str(e),
            event=f"研报生成失败：{e}",
        )


def _start_report_from_form(form: dict[str, list[str]]) -> tuple[bool, str]:
    report_job = _report_snapshot()
    if report_job.get("status") == "running":
        return False, "已有研报任务正在运行"
    ticker = (form.get("ticker") or [""])[0].strip()
    market = (form.get("market") or [""])[0].strip()
    if not ticker or not market:
        return False, "缺少 ticker 或 market"
    options = {
        "ticker": ticker,
        "market": market,
        "name": (form.get("name") or [""])[0].strip(),
        "sector": (form.get("sector") or [""])[0].strip(),
    }
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
    _set_report(
        job_id=job_id,
        status="running",
        stage="排队启动",
        progress_pct=1,
        message=f"研报任务已启动：{ticker}",
        ticker=ticker,
        market=market,
        name=options["name"],
        sector=options["sector"],
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        finished_at="",
        report_path="",
        report_url="",
        error="",
        events=[],
        event=f"研报任务已创建：{ticker}",
    )
    thread = threading.Thread(target=_run_report_job, args=(options,), daemon=True)
    thread.start()
    return True, "研报任务已启动"


def _resume_job_from_form(form: dict[str, list[str]]) -> tuple[bool, str]:
    from src.screening.jobs import load_job, save_job

    job_id = (form.get("job_id") or [""])[0].strip()
    if not job_id:
        return False, "缺少任务 ID"
    job = load_job(job_id)
    if not job:
        return False, "任务不存在"
    existing = ACTIVE_JOB_THREADS.get(job_id)
    if existing and existing.is_alive():
        return True, "任务已经在运行"
    if job.get("status") == "completed":
        return False, "任务已完成，无需恢复"
    job.update(
        {
            "status": "queued",
            "message": "任务已恢复，等待后台执行",
            "started_at": "",
            "finished_at": "",
        }
    )
    save_job(job)
    _set_job(
        status="queued",
        message=f"估值扫描已恢复：{job_id}",
        started_at="",
        finished_at="",
    )
    _start_job_thread(job_id)
    return True, "估值扫描已恢复"


def _status_chip(status: str) -> str:
    labels = {
        "ok": "正常",
        "missing": "未配置",
        "warning": "警告",
        "error": "失败",
    }
    label = labels.get(status, status or "未知")
    cls = status if status in labels else "missing"
    return f'<span class="chip {cls}">{label}</span>'


def _auto_cleanup_chip(enabled: bool) -> str:
    if enabled:
        return '<span class="chip ok">启动自动</span>'
    return '<span class="chip missing">手动保留</span>'


def _fmt_num(value, digits: int = 2, suffix: str = "") -> str:
    if isinstance(value, int | float):
        return f"{value:.{digits}f}{suffix}"
    return "-"


def _fmt_pct(value, digits: int = 1) -> str:
    if isinstance(value, int | float):
        return f"{value * 100:.{digits}f}%"
    return "-"


def _fmt_pct_points(value, digits: int = 1) -> str:
    if isinstance(value, int | float):
        return f"{value:.{digits}f}%"
    return "-"


def _layout(title: str, body: str, *, auto_refresh_seconds: int | None = None) -> bytes:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    refresh_meta = (
        f'<meta http-equiv="refresh" content="{auto_refresh_seconds}">'
        if auto_refresh_seconds
        else ""
    )
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_meta}
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #697586;
      --line: #d8dee6;
      --accent: #126b5f;
      --accent-soft: #e2f2ef;
      --warn: #9a3412;
      --warn-soft: #fff0df;
      --danger: #b42318;
      --danger-soft: #fee4e2;
      --radius: 8px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 28px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    h1 {{ font-size: 20px; margin: 0; font-weight: 650; }}
    nav {{ display: flex; gap: 12px; align-items: center; }}
    nav a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 650;
      font-size: 14px;
    }}
    h2 {{ font-size: 16px; margin: 0 0 14px; }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px;
      max-width: 100%;
      overflow-x: auto;
    }}
    .span-4 {{ grid-column: span 4; }}
    .span-6 {{ grid-column: span 6; }}
    .span-8 {{ grid-column: span 8; }}
    .span-12 {{ grid-column: span 12; }}
    table {{ width: 100%; min-width: 720px; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line); }}
    td {{ vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{
      background: #eef1f5;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 5px;
    }}
    .muted {{ color: var(--muted); }}
    .chip {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 650;
    }}
    .chip.ok {{ color: var(--accent); background: var(--accent-soft); }}
    .chip.missing {{ color: var(--warn); background: var(--warn-soft); }}
    .chip.warning {{ color: var(--warn); background: var(--warn-soft); }}
    .chip.error {{ color: var(--danger); background: var(--danger-soft); }}
    .actions {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .action {{
      display: block;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 12px;
      color: inherit;
      text-decoration: none;
      background: #fbfcfd;
    }}
    .action strong {{ display: block; margin-bottom: 4px; }}
    .report-link {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    .button-link {{
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      padding: 7px 10px;
      background: var(--accent);
      color: #fff;
      text-decoration: none;
      font-weight: 650;
      margin-right: 8px;
    }}
    .button-link.secondary {{
      background: #fff;
      color: var(--accent);
    }}
    .progress {{
      height: 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #eef1f5;
      overflow: hidden;
      margin: 8px 0 12px;
    }}
    .progress-bar {{
      height: 100%;
      background: var(--accent);
      width: 0;
      transition: width 0.2s ease;
    }}
    form {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      align-items: end;
    }}
    label {{ display: grid; gap: 6px; font-size: 13px; color: var(--muted); }}
    input, select, button {{
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }}
    button {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
      font-weight: 650;
      cursor: pointer;
    }}
    .checks {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      grid-column: span 4;
      color: var(--text);
    }}
    .checks label {{ display: inline-flex; align-items: center; gap: 6px; }}
    .checks input {{ min-height: auto; }}
    .notice {{
      border: 1px solid var(--line);
      background: #fbfcfd;
      border-radius: var(--radius);
      padding: 10px 12px;
      color: var(--muted);
      margin: 0 0 14px;
      grid-column: span 12;
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      line-height: 1.55;
      font-size: 14px;
      background: var(--panel);
      margin: 0;
    }}
    @media (max-width: 840px) {{
      header {{ padding: 0 16px; }}
      main {{ padding: 16px; }}
      .span-4, .span-6, .span-8, .span-12 {{ grid-column: span 12; }}
      .actions {{ grid-template-columns: 1fr; }}
      form {{ grid-template-columns: 1fr; }}
      .checks {{ grid-column: span 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>AI Value Investor 控制台</h1>
    <nav>
      <a href="/">工作台</a>
      <a href="/maintenance">数据维护</a>
      <a href="/settings">设置</a>
      <span class="muted">{now}</span>
    </nav>
  </header>
  <main>{body}</main>
</body>
</html>"""
    return page.encode("utf-8")


def _home(message: str = "") -> bytes:
    config = _config_snapshot()
    db = _db_stats()
    reports = _reports()
    valuation_scan = _latest_valuation_scan()
    job = _job_snapshot()
    jobs = _recent_jobs()
    report_job = _report_snapshot()
    maintenance = _latest_maintenance()
    stock_stats = _stock_universe_stats()
    stock_job = _stock_universe_job_snapshot()

    key_rows = "\n".join(
        f"<tr><td><code>{html.escape(k['name'])}</code></td>"
        f"<td>{_status_chip(k['status'])}</td><td>{html.escape(k['display'])}</td></tr>"
        for k in config["keys"]
    )
    runtime_rows = "\n".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{html.escape(v)}</td></tr>"
        for k, v in config["runtime"].items()
    )
    table_rows = "\n".join(
        f"<tr><td><code>{html.escape(t['name'])}</code></td><td>{t['count']}</td>"
        f"<td>{html.escape(str(t['min_date']))}</td><td>{html.escape(str(t['max_date']))}</td></tr>"
        for t in db["tables"]
    )
    report_rows = "\n".join(
        f'<tr><td><a class="report-link" href="{html.escape(r["url"])}">'
        f'{html.escape(r["name"])}</a></td><td>{r["mtime"]}</td><td>{r["size_kb"]} KB</td></tr>'
        for r in reports[:80]
    ) or '<tr><td colspan="3" class="muted">暂无报告</td></tr>'
    valuation_rows = ""
    valuation_meta = "暂无估值扫描结果"
    if valuation_scan:
        scan_id = str(valuation_scan.get("scan_id", ""))
        valuation_meta = (
            f"扫描: {html.escape(scan_id or '-')} | "
            f"时间: {html.escape(str(valuation_scan.get('created_at', '-')))}"
        )
        for row in valuation_scan.get("results", [])[:30]:
            mos = row.get("margin_of_safety_pct")
            mos_text = _fmt_pct_points(mos)
            intrinsic = row.get("intrinsic_value")
            intrinsic_text = f"{intrinsic:.2f}" if isinstance(intrinsic, int | float) else "-"
            price = row.get("current_price")
            price_text = f"{price:.2f}" if isinstance(price, int | float) else "-"
            ticker = html.escape(str(row.get("ticker", "")))
            market = html.escape(str(row.get("market", "")))
            name = html.escape(str(row.get("name") or ""))
            sector = html.escape(str(row.get("sector") or ""))
            report_form = (
                '<form method="post" action="/actions/run-report" style="display:block">'
                f'<input type="hidden" name="ticker" value="{ticker}">'
                f'<input type="hidden" name="market" value="{market}">'
                f'<input type="hidden" name="name" value="{name}">'
                f'<input type="hidden" name="sector" value="{sector}">'
                '<button type="submit">生成研报</button></form>'
            )
            detail_url = f"/valuation/{html.escape(scan_id)}/{ticker}"
            valuation_rows += (
                f'<tr><td><a class="report-link" href="{detail_url}">'
                f"<code>{html.escape(str(row.get('ticker', '-')))}</code></a></td>"
                f"<td>{html.escape(str(row.get('name') or '-'))}</td>"
                f"<td>{price_text}</td><td>{intrinsic_text}</td><td>{mos_text}</td>"
                f"<td>{html.escape(str(row.get('action', '-')))}</td>"
                f"<td>{html.escape(str(row.get('reason', '-')))}</td>"
                f"<td>{report_form}</td></tr>"
            )
    if not valuation_rows:
        valuation_rows = '<tr><td colspan="8" class="muted">暂无估值扫描结果</td></tr>'
    total = int(job.get("total") or 0)
    completed = int(job.get("completed") or 0)
    progress = f"{completed}/{total}" if total else "-"
    current_ticker = job.get("current_ticker") or "-"
    job_rows = "\n".join(
        f'<tr><td><a class="report-link" href="/jobs/{html.escape(str(j.get("job_id", "")))}">'
        f'<code>{html.escape(str(j.get("job_id", "-")))}</code></a></td>'
        f"<td>{html.escape(str(j.get('status', '-')))}</td>"
        f"<td>{j.get('completed', 0)}/{j.get('total', 0)}</td>"
        f"<td>{html.escape(str((j.get('options') or {}).get('universe', '-')))}</td>"
        f"<td>{len(j.get('errors') or [])}</td>"
        f"<td>{html.escape(str(j.get('updated_at') or j.get('created_at') or '-'))}</td>"
        f"<td>{html.escape(str(j.get('message', '-')))}</td></tr>"
        for j in jobs
    ) or '<tr><td colspan="7" class="muted">暂无任务历史</td></tr>'
    report_started = (
        " · 开始: " + html.escape(str(report_job.get("started_at")))
        if report_job.get("started_at")
        else ""
    )
    report_finished = (
        " · 结束: " + html.escape(str(report_job.get("finished_at")))
        if report_job.get("finished_at")
        else ""
    )
    report_file = (
        " · 文件: " + html.escape(str(report_job.get("report_path")))
        if report_job.get("report_path")
        else ""
    )
    report_url = str(
        report_job.get("report_url")
        or _report_url_from_path(report_job.get("report_path"))
    )
    report_actions = '<a class="button-link secondary" href="/report-job">查看进度</a>'
    if report_job.get("status") == "completed" and report_url:
        report_actions = (
            f'<a class="button-link" href="{html.escape(report_url)}">打开阅读</a>'
            '<a class="button-link secondary" href="/report-job">查看任务</a>'
        )
    stock_job_started = (
        " · 开始: " + html.escape(str(stock_job.get("started_at")))
        if stock_job.get("started_at")
        else ""
    )
    stock_job_finished = (
        " · 结束: " + html.escape(str(stock_job.get("finished_at")))
        if stock_job.get("finished_at")
        else ""
    )
    stock_job_error = (
        " · 错误: " + html.escape(str(stock_job.get("error")))
        if stock_job.get("error")
        else ""
    )

    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    body = f"""
<div class="grid">
  {notice}
  <section class="span-8">
    <h2>季度工作流</h2>
    <div class="actions">
      <div class="action">
        <strong>1. 更新数据</strong>
        <span class="muted">CLI: <code>poetry run invest fetch --all</code></span>
      </div>
      <div class="action">
        <strong>2. 批量估值扫描</strong>
        <span class="muted">使用下方表单启动；任务详情会显示进度和错误</span>
      </div>
      <div class="action">
        <strong>3. 深度研报</strong>
        <span class="muted">CLI: <code>poetry run invest report -t 601808.SH</code></span>
      </div>
      <div class="action">
        <strong>4. 数据维护</strong>
        <span class="muted">建议按季度手动清理日志和缓存</span>
      </div>
    </div>
  </section>
  <section class="span-4">
    <h2>数据库</h2>
    <p><strong>{db["size_mb"]} MB</strong></p>
    <p class="muted"><code>{html.escape(db["path"])}</code></p>
    <p class="muted">
      最近维护：
      {html.escape(str((maintenance or {}).get("created_at", "暂无")))}
      · 删除 {html.escape(str((maintenance or {}).get("total_deleted", 0)))} 行
      · <a class="report-link" href="/maintenance">打开数据维护</a>
    </p>
  </section>
  <section class="span-12">
    <h2>证券主数据</h2>
    <p class="muted">
      当前本地股票库：{html.escape(str(stock_stats.get("total", 0)))} 只
      · 更新时间：{html.escape(str(stock_stats.get("updated_at") or "暂无"))}
      · 扫描会优先使用本地股票库，并按 Tushare 行业/板块字段分组。
    </p>
    <p class="muted">
      刷新任务：{html.escape(str(stock_job.get("status", "idle")))}
      · {html.escape(str(stock_job.get("message", "")))}
      · 本次返回：{html.escape(str(stock_job.get("count", 0)))} 只
      {stock_job_started}
      {stock_job_finished}
      {stock_job_error}
    </p>
    <form method="post" action="/actions/refresh-stock-universe" style="display:block">
      <button type="submit">从 Tushare 刷新 A 股股票库</button>
    </form>
  </section>

  <section class="span-12">
    <h2>启动估值扫描</h2>
    <p class="muted">
      当前任务：{html.escape(str(job.get("status", "idle")))}
      · {html.escape(str(job.get("message", "")))}
      · 进度: {progress}
      · 当前: {html.escape(str(current_ticker))}
      {(" · 开始: " + html.escape(str(job.get("started_at")))) if job.get("started_at") else ""}
      {(" · 结束: " + html.escape(str(job.get("finished_at")))) if job.get("finished_at") else ""}
    </p>
    <form method="post" action="/actions/run-valuation-scan">
      <label>股票池
        <select name="universe">
          <option value="watchlist">Watchlist</option>
          <option value="a_share">全 A 股</option>
          <option value="hk">全港股</option>
          <option value="all">A 股 + 港股</option>
        </select>
      </label>
      <label>数量限制
        <input name="limit" type="number" min="1" placeholder="建议先填 20">
      </label>
      <label>市场
        <select name="markets" multiple size="3">
          <option value="a_share" selected>A 股</option>
          <option value="hk" selected>港股</option>
          <option value="us">美股</option>
        </select>
      </label>
      <label>板块
        <input name="board" type="text" placeholder="如 主板 / 创业板">
      </label>
      <label>行业
        <input name="sector" type="text" placeholder="如 银行 / 电力">
      </label>
      <button type="submit">启动扫描</button>
      <div class="checks">
        <label><input type="checkbox" name="refresh_data"> 扫描前刷新数据</label>
        <label><input type="checkbox" name="use_llm"> 启用 LLM 估值解读</label>
        <label><input type="checkbox" name="include_risk"> 包含 ST/退市风险名称</label>
        <label><input type="checkbox" name="confirm_full_scan"> 确认全量扫描</label>
      </div>
    </form>
  </section>

  <section class="span-12">
    <h2>估值任务历史</h2>
    <table><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>股票池</th>
    <th>错误</th><th>最近更新</th><th>消息</th></tr></thead>
    <tbody>{job_rows}</tbody></table>
  </section>

  <section class="span-12">
    <h2>研报生成任务</h2>
    <p class="muted">
      当前任务：{html.escape(str(report_job.get("status", "idle")))}
      · {html.escape(str(report_job.get("message", "")))}
      · 阶段: {html.escape(str(report_job.get("stage") or "-"))}
      · 进度: {html.escape(str(report_job.get("progress_pct", 0)))}%
      {report_started}
      {report_finished}
      {report_file}
    </p>
    <p>{report_actions}</p>
  </section>

  <section class="span-6">
    <h2>API Key 状态</h2>
    <table><thead><tr><th>配置项</th><th>状态</th><th>值</th></tr></thead>
    <tbody>{key_rows}</tbody></table>
  </section>
  <section class="span-6">
    <h2>运行开关</h2>
    <table><thead><tr><th>配置项</th><th>当前值</th></tr></thead>
    <tbody>{runtime_rows}</tbody></table>
  </section>

  <section class="span-12">
    <h2>数据表状态</h2>
    <table><thead><tr><th>表</th><th>行数</th><th>最早日期</th><th>最新日期</th></tr></thead>
    <tbody>{table_rows}</tbody></table>
  </section>

  <section class="span-12">
    <h2>最近估值扫描</h2>
    <p class="muted">{valuation_meta}</p>
    <table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>内在价值</th>
    <th>安全边际</th><th>动作</th><th>原因</th><th>研报</th></tr></thead>
    <tbody>{valuation_rows}</tbody></table>
  </section>

  <section class="span-12">
    <h2>历史分析报告</h2>
    <table><thead><tr><th>报告</th><th>更新时间</th><th>大小</th></tr></thead>
    <tbody>{report_rows}</tbody></table>
  </section>
</div>
"""
    should_refresh = (
        job.get("status") in {"queued", "running"}
        or report_job.get("status") == "running"
        or stock_job.get("status") == "running"
    )
    return _layout(
        "AI Value Investor 控制台",
        body,
        auto_refresh_seconds=5 if should_refresh else None,
    )


def _report_page(name: str) -> bytes:
    safe_name = Path(name).name
    path = REPORT_DIR / safe_name
    if not path.exists():
        path = LEGACY_REPORT_DIR / safe_name
    if not path.exists() or path.suffix.lower() != ".md":
        return _layout("报告不存在", '<section><h2>报告不存在</h2><p>未找到该报告。</p></section>')
    text = path.read_text(encoding="utf-8", errors="replace")
    body = (
        f'<section class="span-12"><h2>{html.escape(safe_name)}</h2>'
        f'<p><a class="report-link" href="/">返回控制台</a></p>'
        f"<pre>{html.escape(text)}</pre></section>"
    )
    return _layout(safe_name, body)


def _report_job_page(message: str = "") -> bytes:
    job = _report_snapshot()
    status = str(job.get("status") or "idle")
    progress = int(job.get("progress_pct") or 0)
    progress = max(0, min(100, progress))
    report_url = str(job.get("report_url") or _report_url_from_path(job.get("report_path")))
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    open_link = (
        f'<a class="button-link" href="{html.escape(report_url)}">打开阅读</a>'
        if status == "completed" and report_url
        else ""
    )
    report_path = str(job.get("report_path") or "")
    path_row = (
        f"<tr><td>报告文件</td><td><code>{html.escape(report_path)}</code></td></tr>"
        if report_path
        else "<tr><td>报告文件</td><td>-</td></tr>"
    )
    event_rows = "\n".join(
        f"<tr><td>{html.escape(str(event.get('time', '-')))}</td>"
        f"<td>{html.escape(str(event.get('message', '-')))}</td></tr>"
        for event in (job.get("events") or [])[-20:]
    ) or '<tr><td colspan="2" class="muted">暂无事件</td></tr>'
    body = f"""
<div class="grid">
  {notice}
  <section class="span-12">
    <h2>研报生成任务</h2>
    <p><a class="report-link" href="/">返回控制台</a></p>
    <p class="muted">{html.escape(str(job.get("message") or ""))}</p>
    <div class="progress"><div class="progress-bar" style="width:{progress}%"></div></div>
    <p>
      {open_link}
      <a class="button-link secondary" href="/">返回首页</a>
    </p>
  </section>
  <section class="span-6">
    <h2>运行状态</h2>
    <table><tbody>
      <tr><td>任务 ID</td><td><code>{html.escape(str(job.get("job_id") or "-"))}</code></td></tr>
      <tr><td>状态</td><td>{html.escape(status)}</td></tr>
      <tr><td>阶段</td><td>{html.escape(str(job.get("stage") or "-"))}</td></tr>
      <tr><td>进度</td><td>{progress}%</td></tr>
      <tr><td>开始</td><td>{html.escape(str(job.get("started_at") or "-"))}</td></tr>
      <tr><td>最近更新</td><td>{html.escape(str(job.get("updated_at") or "-"))}</td></tr>
      <tr><td>结束</td><td>{html.escape(str(job.get("finished_at") or "-"))}</td></tr>
    </tbody></table>
  </section>
  <section class="span-6">
    <h2>标的</h2>
    <table><tbody>
      <tr><td>代码</td><td><code>{html.escape(str(job.get("ticker") or "-"))}</code></td></tr>
      <tr><td>名称</td><td>{html.escape(str(job.get("name") or "-"))}</td></tr>
      <tr><td>市场</td><td>{html.escape(str(job.get("market") or "-"))}</td></tr>
      <tr><td>行业</td><td>{html.escape(str(job.get("sector") or "-"))}</td></tr>
      {path_row}
      <tr><td>错误</td><td>{html.escape(str(job.get("error") or ""))}</td></tr>
    </tbody></table>
  </section>
  <section class="span-12">
    <h2>事件时间线</h2>
    <table><thead><tr><th>时间</th><th>事件</th></tr></thead>
    <tbody>{event_rows}</tbody></table>
  </section>
</div>
"""
    return _layout(
        "研报生成任务",
        body,
        auto_refresh_seconds=5 if status == "running" else None,
    )


def _job_detail_page(job_id: str, message: str = "") -> bytes:
    try:
        from src.screening.jobs import load_job

        job = load_job(job_id)
    except Exception:
        job = None
    if not job:
        return _layout("任务不存在", "<section><h2>任务不存在</h2></section>")

    total = int(job.get("total") or 0)
    completed = int(job.get("completed") or 0)
    percent = f"{(completed / total * 100):.1f}%" if total else "-"
    stale = _job_is_stale(job)
    can_resume = job.get("status") in {"queued", "failed"} or stale
    resume_form = ""
    if can_resume and job.get("status") != "completed":
        resume_form = (
            '<form method="post" action="/actions/resume-job" style="display:block">'
            f'<input type="hidden" name="job_id" value="{html.escape(job_id)}">'
            '<button type="submit">恢复/重跑任务</button></form>'
        )
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    stale_notice = (
        '<p class="notice">这个任务较久没有更新，后台线程可能已经中断，可以尝试恢复。</p>'
        if stale
        else ""
    )

    scan = _load_valuation_scan(str(job.get("scan_id") or "")) if job.get("scan_id") else None
    result_source = scan.get("results", []) if scan else (job.get("results") or [])
    result_source_label = "最终扫描结果" if scan else "运行中临时结果"
    result_rows = "\n".join(
        "<tr>"
        f"<td><code>{html.escape(str(row.get('ticker', '-')))}</code></td>"
        f"<td>{html.escape(str(row.get('name') or '-'))}</td>"
        f"<td>{_fmt_num(row.get('current_price'))}</td>"
        f"<td>{_fmt_num(row.get('intrinsic_value'))}</td>"
        f"<td>{_fmt_pct_points(row.get('margin_of_safety_pct'))}</td>"
        f"<td>{html.escape(str(row.get('action', '-')))}</td>"
        f"<td>{_fmt_pct(row.get('quality_score'), 0)}</td>"
        f"<td>{_fmt_pct(row.get('data_completeness'), 0)}</td>"
        f"<td>{html.escape(str(row.get('reason') or '-'))}</td>"
        f"<td>{html.escape(str(row.get('error') or ''))}</td>"
        "</tr>"
        for row in result_source[:30]
    ) or '<tr><td colspan="10" class="muted">暂无结果，任务可能还在准备股票池</td></tr>'
    error_rows = "\n".join(
        f"<tr><td><code>{html.escape(str(row.get('ticker', '-')))}</code></td>"
        f"<td>{html.escape(str(row.get('error', '-')))}</td></tr>"
        for row in (job.get("errors") or [])[-20:]
    ) or '<tr><td colspan="2" class="muted">暂无错误</td></tr>'
    file_rows = "\n".join(
        f"<tr><td>{label}</td><td><code>{html.escape(str(path))}</code></td></tr>"
        for label, path in [
            ("JSON", job.get("json_path")),
            ("CSV", job.get("csv_path")),
            ("Scan ID", job.get("scan_id")),
        ]
        if path
    ) or '<tr><td colspan="2" class="muted">任务完成后会显示输出文件</td></tr>'

    body = f"""
<div class="grid">
  <section class="span-12">
    <h2>估值任务 {html.escape(job_id)}</h2>
    {notice}
    {stale_notice}
    <p><a class="report-link" href="/">返回控制台</a></p>
    {resume_form}
  </section>
  <section class="span-6">
    <h2>运行状态</h2>
    <table><tbody>
      <tr><td>状态</td><td>{html.escape(str(job.get("status", "-")))}</td></tr>
      <tr><td>消息</td><td>{html.escape(str(job.get("message", "-")))}</td></tr>
      <tr><td>进度</td><td>{completed}/{total} · {percent}</td></tr>
      <tr><td>当前标的</td><td>{html.escape(str(job.get("current_ticker") or "-"))}</td></tr>
      <tr><td>错误数</td><td>{len(job.get("errors") or [])}</td></tr>
      <tr><td>创建</td><td>{html.escape(str(job.get("created_at") or "-"))}</td></tr>
      <tr><td>开始</td><td>{html.escape(str(job.get("started_at") or "-"))}</td></tr>
      <tr><td>最近更新</td><td>{html.escape(str(job.get("updated_at") or "-"))}</td></tr>
      <tr><td>结束</td><td>{html.escape(str(job.get("finished_at") or "-"))}</td></tr>
    </tbody></table>
  </section>
  <section class="span-6">
    <h2>扫描参数</h2>
    <pre>{html.escape(str(job.get("options") or {}))}</pre>
  </section>
  <section class="span-12">
    <h2>输出文件</h2>
    <table><tbody>{file_rows}</tbody></table>
  </section>
  <section class="span-12">
    <h2>最近结果</h2>
    <p class="muted">来源：{html.escape(result_source_label)}</p>
    <table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>内在价值</th>
    <th>安全边际</th><th>动作</th><th>质量</th><th>完整度</th><th>原因</th><th>错误</th></tr></thead>
    <tbody>{result_rows}</tbody></table>
  </section>
  <section class="span-12">
    <h2>最近错误</h2>
    <table><thead><tr><th>代码</th><th>错误</th></tr></thead>
    <tbody>{error_rows}</tbody></table>
  </section>
</div>
"""
    return _layout(
        f"估值任务 {job_id}",
        body,
        auto_refresh_seconds=5 if job.get("status") in {"queued", "running"} else None,
    )


def _valuation_detail_page(scan_id: str, ticker: str) -> bytes:
    scan = _load_valuation_scan(scan_id)
    if not scan:
        return _layout("估值扫描不存在", "<section><h2>估值扫描不存在</h2></section>")

    target = None
    for row in scan.get("results", []):
        if str(row.get("ticker")) == ticker:
            target = row
            break
    if not target:
        return _layout("标的不存在", "<section><h2>该扫描中未找到标的</h2></section>")

    validation = target.get("validation") or {}
    validated_methods = validation.get("validated_methods") or []
    method_rows = "\n".join(
        f"<tr><td>{html.escape(str(m.get('method', '-')))}</td>"
        f"<td>{_fmt_num(m.get('target_price'))}</td>"
        f"<td>{html.escape(str(m.get('valid', '-')))}</td>"
        f"<td>{html.escape(str(m.get('excluded', '-')))}</td></tr>"
        for m in validated_methods
    ) or '<tr><td colspan="4" class="muted">暂无方法明细，请重新运行估值扫描生成详情</td></tr>'

    flags = target.get("quality_flags") or []
    flag_rows = "\n".join(
        f"<tr><td>{html.escape(str(f.get('severity', '-')))}</td>"
        f"<td>{html.escape(str(f.get('flag', '-')))}</td>"
        f"<td>{html.escape(str(f.get('field', '-')))}</td>"
        f"<td>{html.escape(str(f.get('detail', '-')))}</td></tr>"
        for f in flags
    ) or '<tr><td colspan="4" class="muted">暂无质量明细</td></tr>'

    metrics = target.get("valuation_metrics") or {}
    metric_keys = [
        "valuation_mode",
        "industry",
        "wacc",
        "terminal_growth",
        "current_price",
        "margin_of_safety",
        "owner_earnings",
        "shares_outstanding",
    ]
    metric_rows = "\n".join(
        f"<tr><td><code>{html.escape(k)}</code></td>"
        f"<td>{html.escape(str(metrics.get(k, target.get(k, '-'))))}</td></tr>"
        for k in metric_keys
        if metrics.get(k) is not None or target.get(k) is not None
    ) or '<tr><td colspan="2" class="muted">暂无关键指标</td></tr>'

    report_form = (
        '<form method="post" action="/actions/run-report">'
        f'<input type="hidden" name="ticker" value="{html.escape(str(target.get("ticker", "")))}">'
        f'<input type="hidden" name="market" value="{html.escape(str(target.get("market", "")))}">'
        f'<input type="hidden" name="name" value="{html.escape(str(target.get("name") or ""))}">'
        f'<input type="hidden" name="sector" '
        f'value="{html.escape(str(target.get("sector") or ""))}">'
        '<button type="submit">生成完整研报</button></form>'
    )
    reasoning = (
        target.get("valuation_reasoning")
        or "暂无完整估值推理，请重新运行估值扫描生成详情。"
    )
    quality_pct = _fmt_num((target.get("quality_score") or 0) * 100, 0, "%")
    completeness_pct = _fmt_num((target.get("data_completeness") or 0) * 100, 0, "%")
    confidence_pct = _fmt_pct(target.get("confidence"), 0)

    body = f"""
<div class="grid">
  <section class="span-12">
    <h2>{html.escape(ticker)} 估值详情</h2>
    <p><a class="report-link" href="/">返回控制台</a></p>
    {report_form}
  </section>
  <section class="span-6">
    <h2>结论</h2>
    <table><tbody>
      <tr><td>名称</td><td>{html.escape(str(target.get("name") or "-"))}</td></tr>
      <tr><td>市场</td><td>{html.escape(str(target.get("market") or "-"))}</td></tr>
      <tr><td>行业</td><td>{html.escape(str(target.get("sector") or "-"))}</td></tr>
      <tr><td>现价</td><td>{_fmt_num(target.get("current_price"))}</td></tr>
      <tr><td>内在价值</td><td>{_fmt_num(target.get("intrinsic_value"))}</td></tr>
      <tr><td>安全边际</td><td>{_fmt_num(target.get("margin_of_safety_pct"), 1, "%")}</td></tr>
      <tr><td>动作</td><td>{html.escape(str(target.get("action") or "-"))}</td></tr>
      <tr><td>原因</td><td>{html.escape(str(target.get("reason") or "-"))}</td></tr>
    </tbody></table>
  </section>
  <section class="span-6">
    <h2>置信度</h2>
    <table><tbody>
      <tr><td>估值信号</td><td>{html.escape(str(target.get("signal") or "-"))}</td></tr>
      <tr><td>估值置信度</td><td>{confidence_pct}</td></tr>
      <tr><td>数据质量</td><td>{quality_pct}</td></tr>
      <tr><td>数据完整度</td><td>{completeness_pct}</td></tr>
      <tr><td>估值模式</td><td>{html.escape(str(target.get("valuation_mode") or "-"))}</td></tr>
      <tr><td>降级模式</td><td>{html.escape(str(target.get("degraded")))}</td></tr>
    </tbody></table>
  </section>
  <section class="span-12">
    <h2>估值方法校验</h2>
    <p class="muted">有效方法：{html.escape(str(target.get("valid_methods") or "-"))}
    · 排除方法：{html.escape(str(target.get("excluded_methods") or "-"))}</p>
    <table><thead><tr><th>方法</th><th>目标价</th><th>有效</th><th>排除</th></tr></thead>
    <tbody>{method_rows}</tbody></table>
  </section>
  <section class="span-12">
    <h2>关键指标</h2>
    <table><tbody>{metric_rows}</tbody></table>
  </section>
  <section class="span-12">
    <h2>数据质量 Flags</h2>
    <table><thead><tr><th>级别</th><th>类型</th><th>字段</th><th>详情</th></tr></thead>
    <tbody>{flag_rows}</tbody></table>
  </section>
  <section class="span-12">
    <h2>估值推理</h2>
    <pre>{html.escape(reasoning)}</pre>
  </section>
</div>
"""
    return _layout(f"{ticker} 估值详情", body)


def _settings_page(message: str = "") -> bytes:
    env_values = _parse_env_values()
    health_results = _latest_health()

    secret_rows = "\n".join(
        f"<tr><td><code>{html.escape(key)}</code></td>"
        f"<td>{html.escape(_mask_value(env_values.get(key) or os.getenv(key, '')))}</td>"
        f'<td><input type="password" name="{html.escape(key)}" '
        f'placeholder="留空则不修改"></td></tr>'
        for key in SECRET_ENV_KEYS
    )

    def _runtime_input(key: str, default: str = "") -> str:
        value = env_values.get(key) or os.getenv(key, default)
        return (
            f'<input type="text" name="{html.escape(key)}" '
            f'value="{html.escape(str(value))}">'
        )

    runtime_rows = "\n".join(
        f"<tr><td><code>{html.escape(key)}</code></td>"
        f"<td>{_runtime_input(key, _default_runtime_value(key))}</td></tr>"
        for key in RUNTIME_ENV_KEYS
    )
    health_rows = "\n".join(
        f"<tr><td>{html.escape(str(item.get('name', '-')))}</td>"
        f"<td>{_status_chip(str(item.get('status', 'missing')))}</td>"
        f"<td>{html.escape(str(item.get('detail', '-')))}</td>"
        f"<td>{html.escape(str(item.get('checked_at', '-')))}</td></tr>"
        for item in health_results
    ) or '<tr><td colspan="4" class="muted">还没有运行过健康检查</td></tr>'
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    body = f"""
<div class="grid">
  <section class="span-12">
    <h2>设置</h2>
    {notice}
    <p class="muted">
      密钥不会明文显示。保存时会先创建 <code>.env.backup-时间戳</code>。
      密钥输入框留空表示不修改。部分依赖模块会在下次启动后完整读取新配置。
    </p>
    <section class="span-12" style="margin-bottom:16px">
      <h2>数据源健康检查</h2>
      <p class="muted">
        手动运行。Tushare、FMP、Telegram 会做轻量连通性检查；
        LLM 与 Tavily 只检查是否配置，避免自检消耗 token 或搜索额度。
      </p>
      <form method="post" action="/actions/run-health-check" style="display:block">
        <button type="submit">运行健康检查</button>
      </form>
      <table style="margin-top:12px"><thead>
        <tr><th>服务</th><th>状态</th><th>详情</th><th>检查时间</th></tr>
      </thead><tbody>{health_rows}</tbody></table>
    </section>
    <form method="post" action="/actions/save-settings" style="display:block">
      <section class="span-12" style="margin-bottom:16px">
        <h2>API Key</h2>
        <table><thead><tr><th>配置项</th><th>当前值</th><th>新值</th></tr></thead>
        <tbody>{secret_rows}</tbody></table>
      </section>
      <section class="span-12">
        <h2>运行开关</h2>
        <table><thead><tr><th>配置项</th><th>值</th></tr></thead>
        <tbody>{runtime_rows}</tbody></table>
      </section>
      <p><button type="submit">保存设置</button></p>
    </form>
  </section>
</div>
"""
    return _layout("设置", body)


def _maintenance_page(message: str = "") -> bytes:
    try:
        from src.data.maintenance import get_maintenance_preview

        preview = get_maintenance_preview(include_core=True)
    except Exception as e:
        preview = {
            "rules": [],
            "total_candidates": 0,
            "size_before_mb": 0,
            "size_after_mb": 0,
            "created_at": "-",
            "error": str(e),
        }
    latest = _latest_maintenance() or {}
    preview_total = html.escape(str(preview.get("total_candidates", 0)))
    latest_deleted = html.escape(str(latest.get("total_deleted", 0)))
    rows = "\n".join(
        f"<tr><td><code>{html.escape(str(rule.get('table', '-')))}</code></td>"
        f"<td>{html.escape(str(rule.get('description', '-')))}</td>"
        f"<td>{html.escape(str(rule.get('retention_days', '-')))} 天</td>"
        f"<td>{_auto_cleanup_chip(bool(rule.get('auto_cleanup')))}</td>"
        f"<td>{html.escape(str(rule.get('cutoff', '-')))}</td>"
        f"<td>{html.escape(str(rule.get('candidates', 0)))}</td>"
        f"<td>{html.escape(str(rule.get('deleted', 0)))}</td></tr>"
        for rule in preview.get("rules", [])
    ) or '<tr><td colspan="7" class="muted">暂无维护策略</td></tr>'
    latest_rows = "\n".join(
        f"<tr><td><code>{html.escape(str(rule.get('table', '-')))}</code></td>"
        f"<td>{html.escape(str(rule.get('cutoff', '-')))}</td>"
        f"<td>{html.escape(str(rule.get('candidates', 0)))}</td>"
        f"<td>{html.escape(str(rule.get('deleted', 0)))}</td></tr>"
        for rule in latest.get("rules", [])
    ) or '<tr><td colspan="4" class="muted">暂无维护记录</td></tr>'
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    body = f"""
<div class="grid">
  <section class="span-12">
    <h2>数据维护</h2>
    {notice}
    <p class="muted">
      Best practice：启动时只自动清理低风险运行数据，例如 Agent 信号和扫描日志。
      行情、财务指标、三张财报是估值基础，默认保留多年，避免为了释放少量空间牺牲准确性。
    </p>
    <table><tbody>
      <tr><td>数据库大小</td><td>{html.escape(str(preview.get("size_before_mb", 0)))} MB</td></tr>
      <tr><td>本次预估可清理</td><td>{preview_total} 行</td></tr>
      <tr><td>上次维护</td><td>{html.escape(str(latest.get("created_at", "暂无")))}</td></tr>
      <tr><td>上次删除</td><td>{latest_deleted} 行</td></tr>
    </tbody></table>
    <form method="post" action="/actions/run-maintenance">
      <button type="submit" name="mode" value="dry_run">重新预估</button>
      <button type="submit" name="mode" value="safe_cleanup">执行安全清理</button>
      <label style="display:inline-flex;align-items:center;gap:6px">
        <input type="checkbox" name="vacuum"> 清理后压缩 SQLite 文件
      </label>
    </form>
  </section>
  <section class="span-12">
    <h2>当前维护策略</h2>
    <table><thead>
      <tr><th>表</th><th>说明</th><th>保留期</th><th>启动自动</th><th>清理阈值</th><th>命中行数</th><th>本页删除</th></tr>
    </thead><tbody>{rows}</tbody></table>
  </section>
  <section class="span-12">
    <h2>上次维护明细</h2>
    <table><thead><tr><th>表</th><th>清理阈值</th><th>命中行数</th><th>删除行数</th></tr></thead>
    <tbody>{latest_rows}</tbody></table>
  </section>
</div>
"""
    return _layout("数据维护", body)


def _default_runtime_value(key: str) -> str:
    defaults = {
        "DB_AUTO_MAINTENANCE": "true",
        "DB_SIGNAL_RETENTION_DAYS": "30",
        "DB_LOG_RETENTION_DAYS": "30",
        "DB_PRICE_RETENTION_DAYS": "2555",
        "DB_FINANCIAL_RETENTION_DAYS": "3650",
        "TUSHARE_CLIENT_MODE": "auto",
        "TUSHARE_FAST_API_URL": "https://fastapic.stockai888.top",
        "TUSHARE_SUPER_API_URL": "https://ai-tool.indevs.in/tushare/pro",
        "TUSHARE_TIMEOUT": "10",
        "TUSHARE_DISABLE_PROXY": "false",
        "USE_INDUSTRY_ENGINE_V3": "false",
        "SKIP_AKSHARE": "true",
        "FETCH_DELAY": "3.0",
        "FETCH_DELAY_BETWEEN_SOURCES": "2.0",
        "FETCH_DELAY_BETWEEN_TICKERS": "5.0",
    }
    return defaults.get(key, "")


class ConsoleHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = parse_qs(query)
        if path == "/":
            message = (params.get("message") or [""])[0]
            content = _home(message=message)
            status = 200
        elif path == "/settings":
            message = (params.get("message") or [""])[0]
            content = _settings_page(message=message)
            status = 200
        elif path == "/maintenance":
            message = (params.get("message") or [""])[0]
            content = _maintenance_page(message=message)
            status = 200
        elif path == "/report-job":
            message = (params.get("message") or [""])[0]
            content = _report_job_page(message=message)
            status = 200
        elif path.startswith("/jobs/"):
            message = (params.get("message") or [""])[0]
            content = _job_detail_page(Path(path).name, message=message)
            status = 200
        elif path.startswith("/valuation/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                content = _valuation_detail_page(parts[1], parts[2])
                status = 200
            else:
                content = _layout("Not Found", "<section><h2>404</h2></section>")
                status = 404
        elif path.startswith("/reports/"):
            content = _report_page(path.removeprefix("/reports/"))
            status = 200
        else:
            content = _layout("Not Found", "<section><h2>404</h2><p>页面不存在。</p></section>")
            status = 404

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(raw)

        if path == "/actions/run-valuation-scan":
            ok, message = _start_scan_from_form(form)
            _set_job(message=message if not ok else _job_snapshot().get("message", message))
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if path == "/actions/run-report":
            _ok, message = _start_report_from_form(form)
            self.send_response(303)
            self.send_header("Location", f"/report-job?message={quote(message)}")
            self.end_headers()
            return

        if path == "/actions/resume-job":
            _ok, message = _resume_job_from_form(form)
            job_id = (form.get("job_id") or [""])[0].strip()
            target = f"/jobs/{quote(job_id)}" if job_id else "/"
            self.send_response(303)
            self.send_header("Location", f"{target}?message={quote(message)}")
            self.end_headers()
            return

        if path == "/actions/refresh-stock-universe":
            _ok, message = _start_stock_universe_refresh()
            self.send_response(303)
            self.send_header("Location", f"/?message={quote(message)}")
            self.end_headers()
            return

        if path == "/actions/save-settings":
            _ok, message = _save_settings_from_form(form)
            self.send_response(303)
            self.send_header("Location", f"/settings?message={quote(message)}")
            self.end_headers()
            return

        if path == "/actions/run-health-check":
            try:
                from src.web.health import run_health_checks

                results = run_health_checks()
                failures = sum(
                    1
                    for item in results
                    if item.get("status") in {"warning", "error", "missing"}
                )
                message = f"健康检查完成：{len(results)} 项，需关注 {failures} 项"
            except Exception as e:
                message = f"健康检查失败：{e}"
            self.send_response(303)
            self.send_header("Location", f"/settings?message={quote(message)}")
            self.end_headers()
            return

        if path == "/actions/run-maintenance":
            try:
                from src.data.maintenance import run_database_maintenance

                mode = (form.get("mode") or ["dry_run"])[0]
                dry_run = mode != "safe_cleanup"
                result = run_database_maintenance(
                    dry_run=dry_run,
                    include_core=False,
                    vacuum="vacuum" in form,
                    reason="manual_console",
                )
                if dry_run:
                    message = f"维护预估完成：可安全清理 {result.get('total_candidates', 0)} 行"
                else:
                    message = f"安全清理完成：删除 {result.get('total_deleted', 0)} 行"
            except Exception as e:
                message = f"数据维护失败：{e}"
            self.send_response(303)
            self.send_header("Location", f"/maintenance?message={quote(message)}")
            self.end_headers()
            return

        content = _layout("Not Found", "<section><h2>404</h2><p>操作不存在。</p></section>")
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[Console] {self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local AI Value Investor console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the browser automatically.")
    args = parser.parse_args()

    url = f"http://{_connect_host(args.host)}:{args.port}"
    if _is_port_open(args.host, args.port):
        if _looks_like_console(url):
            print(f"AI Value Investor console is already running: {url}")
            if args.open:
                webbrowser.open(url)
            return
        new_port = _find_free_port(args.host, args.port + 1)
        print(f"Port {args.port} is already in use; starting console on {new_port}.")
        args.port = new_port
        url = f"http://{_connect_host(args.host)}:{args.port}"

    try:
        from src.data.maintenance import run_database_maintenance, startup_maintenance_enabled

        if startup_maintenance_enabled():
            result = run_database_maintenance(
                dry_run=False,
                include_core=False,
                vacuum=False,
                reason="console_startup",
            )
            if result.get("total_deleted"):
                print(f"Startup DB maintenance deleted {result['total_deleted']} old rows.")
    except Exception as e:
        print(f"Startup DB maintenance skipped: {e}")

    server = ThreadingHTTPServer((args.host, args.port), ConsoleHandler)
    print(f"AI Value Investor console: {url}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping console...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
