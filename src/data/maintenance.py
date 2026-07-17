"""SQLite maintenance helpers.

Value-investing analysis needs multi-year fundamentals and price history, so
startup cleanup is deliberately limited to low-risk operational tables.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from src.utils.config import get_db_path, get_project_root

PROJECT_ROOT = get_project_root()
MAINTENANCE_DIR = PROJECT_ROOT / "output" / "maintenance"
LATEST_MAINTENANCE_PATH = MAINTENANCE_DIR / "latest.json"


@dataclass(frozen=True)
class RetentionRule:
    table: str
    date_column: str
    retention_days: int
    auto_cleanup: bool
    description: str


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _rules() -> list[RetentionRule]:
    return [
        RetentionRule(
            table="agent_signals",
            date_column="created_at",
            retention_days=_env_int("DB_SIGNAL_RETENTION_DAYS", 30),
            auto_cleanup=True,
            description="Agent 临时判断结果，可由完整流程重新生成",
        ),
        RetentionRule(
            table="scan_logs",
            date_column="created_at",
            retention_days=_env_int("DB_LOG_RETENTION_DAYS", 30),
            auto_cleanup=True,
            description="扫描运行日志，只保留近期排障信息",
        ),
        RetentionRule(
            table="daily_prices",
            date_column="date",
            retention_days=_env_int("DB_PRICE_RETENTION_DAYS", 2555),
            auto_cleanup=False,
            description="行情序列，估值和回测需要多年历史，默认保留约 7 年",
        ),
        RetentionRule(
            table="financial_metrics",
            date_column="date",
            retention_days=_env_int("DB_PRICE_RETENTION_DAYS", 2555),
            auto_cleanup=False,
            description="估值倍数和财务指标，默认跟随行情保留约 7 年",
        ),
        RetentionRule(
            table="income_statements",
            date_column="period_end_date",
            retention_days=_env_int("DB_FINANCIAL_RETENTION_DAYS", 3650),
            auto_cleanup=False,
            description="利润表，价值投资需要长周期对比，默认保留约 10 年",
        ),
        RetentionRule(
            table="balance_sheets",
            date_column="period_end_date",
            retention_days=_env_int("DB_FINANCIAL_RETENTION_DAYS", 3650),
            auto_cleanup=False,
            description="资产负债表，价值投资需要长周期对比，默认保留约 10 年",
        ),
        RetentionRule(
            table="cash_flows",
            date_column="period_end_date",
            retention_days=_env_int("DB_FINANCIAL_RETENTION_DAYS", 3650),
            auto_cleanup=False,
            description="现金流量表，价值投资需要长周期对比，默认保留约 10 年",
        ),
    ]


def _cutoff(retention_days: int) -> str:
    return str(date.today() - timedelta(days=retention_days))


def _db_size_mb(path: Path) -> float:
    return round(path.stat().st_size / 1024 / 1024, 2) if path.exists() else 0.0


def _count_candidates(conn: sqlite3.Connection, rule: RetentionRule) -> int:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {rule.table} WHERE {rule.date_column} < ?",
            (_cutoff(rule.retention_days),),
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def get_maintenance_preview(include_core: bool = True) -> dict:
    db_path = get_db_path()
    rules = _rules()
    if not include_core:
        rules = [rule for rule in rules if rule.auto_cleanup]

    payload = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "size_before_mb": _db_size_mb(db_path),
        "size_after_mb": _db_size_mb(db_path),
        "dry_run": True,
        "vacuum": False,
        "rules": [],
        "total_candidates": 0,
    }
    if not db_path.exists():
        return payload

    with sqlite3.connect(str(db_path)) as conn:
        for rule in rules:
            candidates = _count_candidates(conn, rule)
            payload["rules"].append(
                {
                    **asdict(rule),
                    "cutoff": _cutoff(rule.retention_days),
                    "candidates": candidates,
                    "deleted": 0,
                }
            )
            payload["total_candidates"] += candidates
    return payload


def run_database_maintenance(
    *,
    dry_run: bool = True,
    include_core: bool = False,
    vacuum: bool = False,
    reason: str = "manual",
) -> dict:
    db_path = get_db_path()
    rules = _rules()
    if not include_core:
        rules = [rule for rule in rules if rule.auto_cleanup]

    payload = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "size_before_mb": _db_size_mb(db_path),
        "size_after_mb": _db_size_mb(db_path),
        "dry_run": dry_run,
        "vacuum": vacuum,
        "rules": [],
        "total_candidates": 0,
        "total_deleted": 0,
    }
    if not db_path.exists():
        save_maintenance_result(payload)
        return payload

    with sqlite3.connect(str(db_path)) as conn:
        for rule in rules:
            cutoff = _cutoff(rule.retention_days)
            candidates = _count_candidates(conn, rule)
            deleted = 0
            if not dry_run and candidates:
                cursor = conn.execute(
                    f"DELETE FROM {rule.table} WHERE {rule.date_column} < ?",
                    (cutoff,),
                )
                deleted = int(cursor.rowcount or 0)
            payload["rules"].append(
                {
                    **asdict(rule),
                    "cutoff": cutoff,
                    "candidates": candidates,
                    "deleted": deleted,
                }
            )
            payload["total_candidates"] += candidates
            payload["total_deleted"] += deleted
        conn.commit()
        if vacuum and not dry_run and payload["total_deleted"]:
            conn.execute("VACUUM")

    payload["size_after_mb"] = _db_size_mb(db_path)
    save_maintenance_result(payload)
    return payload


def save_maintenance_result(result: dict) -> Path:
    MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_MAINTENANCE_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return LATEST_MAINTENANCE_PATH


def load_latest_maintenance() -> dict | None:
    if not LATEST_MAINTENANCE_PATH.exists():
        return None
    try:
        data = json.loads(LATEST_MAINTENANCE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def startup_maintenance_enabled() -> bool:
    return (os.getenv("DB_AUTO_MAINTENANCE", "true").lower() in {"1", "true", "yes"})
