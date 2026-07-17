"""Manual health checks for the local console.

The console only runs these checks when the user clicks the health-check
button. This keeps normal startup quiet and avoids accidental API usage.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.utils.config import get_project_root

PROJECT_ROOT = get_project_root()
HEALTH_DIR = PROJECT_ROOT / "output" / "health"
LATEST_HEALTH_PATH = HEALTH_DIR / "latest.json"


@dataclass
class HealthCheckResult:
    name: str
    status: str
    detail: str
    checked_at: str


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _has_env(*names: str) -> bool:
    return any(bool((os.getenv(name) or "").strip()) for name in names)


def _result(name: str, status: str, detail: str) -> HealthCheckResult:
    return HealthCheckResult(name=name, status=status, detail=detail, checked_at=_now())


def _configured_check(name: str, env_names: tuple[str, ...]) -> HealthCheckResult:
    if _has_env(*env_names):
        return _result(name, "ok", "已配置")
    return _result(name, "missing", "未配置")


def _source_health(
    name: str,
    configured: bool,
    missing_detail: str,
    check: Callable[[], bool],
) -> HealthCheckResult:
    if not configured:
        return _result(name, "missing", missing_detail)
    try:
        if check():
            return _result(name, "ok", "轻量连通性检查通过")
        return _result(name, "warning", "已配置，但轻量连通性检查未通过")
    except Exception as exc:
        return _result(name, "error", f"检查失败：{exc}")


def _tushare_health() -> HealthCheckResult:
    configured = _has_env("TUSHARE_FAST_TOKEN", "TUSHARE_SUPER_API_KEY", "TUSHARE_TOKEN")

    def _check() -> bool:
        from src.data.tushare_source import TushareSource

        return TushareSource().health_check()

    return _source_health("Tushare", configured, "未配置 Tushare token", _check)


def _qveris_health() -> HealthCheckResult:
    configured = _has_env("QVERIS_API_KEYS", "QVERIS_API_KEY")

    def _check() -> bool:
        from src.data.qveris_source import QVerisSource

        return QVerisSource().health_check()

    return _source_health("QVeris iFinD", configured, "未配置 QVeris API key", _check)


def _fmp_health() -> HealthCheckResult:
    configured = _has_env("FMP_API_KEY")

    def _check() -> bool:
        from src.data.fmp_source import FMPSource

        return FMPSource().health_check()

    return _source_health("FMP", configured, "未配置 FMP_API_KEY", _check)


def _telegram_health() -> HealthCheckResult:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        return _result("Telegram", "missing", "未配置 TELEGRAM_BOT_TOKEN")

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("ok") and chat_id:
            return _result("Telegram", "ok", "Bot token 可用，chat id 已配置")
        if payload.get("ok"):
            return _result("Telegram", "warning", "Bot token 可用，但未配置 chat id")
        return _result("Telegram", "error", "Telegram 返回失败")
    except urllib.error.HTTPError as exc:
        return _result("Telegram", "error", f"HTTP {exc.code}，请检查 Bot token")
    except Exception as exc:
        return _result("Telegram", "error", f"检查失败：{exc}")


def run_health_checks() -> list[dict]:
    """Run manual health checks and persist the latest result."""
    results = [
        _configured_check("OpenAI", ("OPENAI_API_KEY",)),
        _configured_check("DeepSeek", ("DEEPSEEK_API_KEY",)),
        _configured_check("Anthropic", ("ANTHROPIC_API_KEY",)),
        _configured_check("Tavily", ("TAVILY_API_KEY",)),
        _tushare_health(),
        _qveris_health(),
        _fmp_health(),
        _telegram_health(),
    ]
    payload = [asdict(item) for item in results]
    save_health_results(payload)
    return payload


def save_health_results(results: list[dict]) -> Path:
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_HEALTH_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return LATEST_HEALTH_PATH


def load_latest_health() -> list[dict]:
    if not LATEST_HEALTH_PATH.exists():
        return []
    try:
        data = json.loads(LATEST_HEALTH_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []
