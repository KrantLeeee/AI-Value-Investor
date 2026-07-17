"""Accuracy-first batch valuation scanner.

This module runs the valuation workflow without generating a full report.
It is designed for quarterly research: reuse the project valuation engine,
record enough evidence to audit the result, and export a table that helps
select companies for deeper reports.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from src.data import database
from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    IncomeStatement,
    QualityReport,
)
from src.data.quality import run_quality_checks
from src.screening.universe import UniverseSource, load_universe
from src.utils.config import get_output_dir
from src.utils.logger import get_logger

logger = get_logger(__name__)

ActionType = Literal["strong_candidate", "deep_research", "watch", "reject", "error"]


@dataclass
class ValuationSnapshot:
    ticker: str
    name: str
    market: str
    sector: str
    current_price: float | None
    intrinsic_value: float | None
    margin_of_safety: float | None
    signal: str
    confidence: float
    quality_score: float
    data_completeness: float
    valuation_mode: str
    valid_methods: str
    excluded_methods: str
    degraded: bool
    action: ActionType
    reason: str
    error: str = ""
    quality_flags: list[dict] | None = None
    validation: dict | None = None
    valuation_metrics: dict | None = None
    valuation_reasoning: str = ""

    @property
    def margin_of_safety_pct(self) -> float | None:
        return self.margin_of_safety * 100 if self.margin_of_safety is not None else None


def _to_models(rows: list[dict], model_cls):
    result = []
    for row in rows:
        try:
            result.append(
                model_cls(
                    **{k: v for k, v in row.items() if k in model_cls.model_fields}
                )
            )
        except Exception:
            continue
    return result


def _core_data_counts(ticker: str) -> dict[str, int]:
    return {
        "prices": len(database.get_latest_prices(ticker, limit=1)),
        "income": len(database.get_income_statements(ticker, limit=1, period_type="annual")),
        "balance": len(database.get_balance_sheets(ticker, limit=1, period_type="annual")),
        "cashflow": len(database.get_cash_flows(ticker, limit=1, period_type="annual")),
        "metrics": len(database.get_financial_metrics(ticker, limit=1)),
    }


def _ensure_core_data(ticker: str, market: str, *, force: bool) -> dict[str, int]:
    counts = _core_data_counts(ticker)
    missing = [name for name, count in counts.items() if count <= 0]
    if not force and not missing:
        return counts

    reason = "forced refresh" if force else f"missing {', '.join(missing)}"
    logger.info("[BatchValuation] Fetching %s before valuation (%s)", ticker, reason)
    try:
        from src.data.fetcher import Fetcher

        Fetcher().fetch_all(ticker, market)
    except Exception as e:
        logger.warning("[BatchValuation] Data refresh failed for %s: %s", ticker, e)
    return _core_data_counts(ticker)


def _quality_report(ticker: str, market: str) -> QualityReport:
    annual_income = database.get_income_statements(ticker, limit=10, period_type="annual")
    quarterly_income = database.get_income_statements(ticker, limit=4, period_type="quarterly")
    annual_balance = database.get_balance_sheets(ticker, limit=10, period_type="annual")
    quarterly_balance = database.get_balance_sheets(ticker, limit=4, period_type="quarterly")
    annual_cashflow = database.get_cash_flows(ticker, limit=10, period_type="annual")
    quarterly_cashflow = database.get_cash_flows(ticker, limit=4, period_type="quarterly")

    raw_data = {
        "income": _to_models(annual_income + quarterly_income, IncomeStatement),
        "balance": _to_models(annual_balance + quarterly_balance, BalanceSheet),
        "cashflow": _to_models(annual_cashflow + quarterly_cashflow, CashFlow),
        "prices": _to_models(database.get_latest_prices(ticker, limit=10), DailyPrice),
        "metrics": database.get_financial_metrics(ticker, limit=5),
    }
    return run_quality_checks(ticker, market, raw_data)


def _choose_action(
    *,
    margin_of_safety: float | None,
    confidence: float,
    quality_score: float,
    data_completeness: float,
    valid_method_count: int,
    degraded: bool,
    error: str = "",
) -> tuple[ActionType, str]:
    if error:
        return "error", error
    if data_completeness < 0.35:
        return "reject", "数据完整度低于 35%，估值不可依赖"
    if margin_of_safety is None:
        return "reject", "无法计算安全边际"
    if margin_of_safety < 0:
        return "reject", "当前价格高于估算内在价值"
    if degraded or valid_method_count < 2:
        if margin_of_safety >= 0.30 and quality_score >= 0.55:
            return "deep_research", "安全边际较高，但有效估值方法不足，需要深挖"
        return "watch", "估值方法支持不足，先观察"
    if margin_of_safety >= 0.30 and quality_score >= 0.70 and confidence >= 0.55:
        return "strong_candidate", "安全边际、数据质量和置信度均达标"
    if margin_of_safety >= 0.15 and quality_score >= 0.50:
        return "deep_research", "存在低估迹象，建议生成完整研报验证"
    if margin_of_safety >= 0:
        return "watch", "价格未明显高估，但安全边际不足"
    return "reject", "当前价格高于估算内在价值"


def run_batch_valuation(
    ticker: str,
    market: str,
    *,
    name: str = "",
    sector: str = "",
    use_llm: bool = False,
    refresh_data: bool = False,
) -> ValuationSnapshot:
    _ensure_core_data(ticker, market, force=refresh_data)

    try:
        quality = _quality_report(ticker, market)
    except Exception as e:
        logger.warning("[BatchValuation] Quality check failed for %s: %s", ticker, e)
        quality = QualityReport(
            ticker=ticker,
            market=market,
            flags=[],
            overall_quality_score=0.0,
            data_completeness=0.0,
            stale_fields=[],
            records_checked={},
        )

    try:
        from src.agents import valuation

        signal = valuation.run(ticker, market, use_llm=use_llm)
        metrics = signal.metrics or {}
        validation = metrics.get("validation") or {}
        valid_methods = validation.get("valid_methods") or []
        excluded_methods = validation.get("excluded_methods") or []
        intrinsic_value = validation.get("weighted_target")
        margin_of_safety = metrics.get("margin_of_safety")
        degraded = bool(validation.get("degraded", False))

        action, reason = _choose_action(
            margin_of_safety=margin_of_safety,
            confidence=signal.confidence,
            quality_score=quality.overall_quality_score,
            data_completeness=quality.data_completeness,
            valid_method_count=len(valid_methods),
            degraded=degraded,
        )

        return ValuationSnapshot(
            ticker=ticker,
            name=name,
            market=market,
            sector=sector or str(metrics.get("industry") or ""),
            current_price=metrics.get("current_price"),
            intrinsic_value=intrinsic_value,
            margin_of_safety=margin_of_safety,
            signal=signal.signal,
            confidence=signal.confidence,
            quality_score=quality.overall_quality_score,
            data_completeness=quality.data_completeness,
            valuation_mode=str(metrics.get("valuation_mode") or "standard"),
            valid_methods=", ".join(valid_methods),
            excluded_methods=", ".join(excluded_methods),
            degraded=degraded,
            action=action,
            reason=reason,
            quality_flags=[flag.model_dump() for flag in quality.flags],
            validation=validation,
            valuation_metrics=metrics,
            valuation_reasoning=signal.reasoning,
        )
    except Exception as e:
        logger.exception("[BatchValuation] Valuation failed for %s", ticker)
        return ValuationSnapshot(
            ticker=ticker,
            name=name,
            market=market,
            sector=sector,
            current_price=None,
            intrinsic_value=None,
            margin_of_safety=None,
            signal="neutral",
            confidence=0.0,
            quality_score=quality.overall_quality_score,
            data_completeness=quality.data_completeness,
            valuation_mode="unknown",
            valid_methods="",
            excluded_methods="",
            degraded=True,
            action="error",
            reason="估值失败",
            error=str(e),
            quality_flags=[flag.model_dump() for flag in quality.flags],
        )


def save_valuation_scan(
    results: list[ValuationSnapshot],
    scan_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    scan_id = scan_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = get_output_dir("valuation_scans")
    json_path = out_dir / f"{scan_id}.json"
    csv_path = out_dir / f"{scan_id}.csv"

    payload = {
        "scan_id": scan_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata or {},
        "results": [asdict(r) | {"margin_of_safety_pct": r.margin_of_safety_pct} for r in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "ticker",
        "name",
        "market",
        "sector",
        "current_price",
        "intrinsic_value",
        "margin_of_safety_pct",
        "signal",
        "confidence",
        "quality_score",
        "data_completeness",
        "valuation_mode",
        "valid_methods",
        "excluded_methods",
        "degraded",
        "action",
        "reason",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in payload["results"]:
            writer.writerow({k: result.get(k) for k in fieldnames})

    return {"scan_id": scan_id, "json_path": json_path, "csv_path": csv_path}


def run_watchlist_valuation_scan(
    *,
    markets: set[str] | None = None,
    limit: int | None = None,
    use_llm: bool = False,
    refresh_data: bool = False,
) -> tuple[list[ValuationSnapshot], dict]:
    return run_valuation_scan(
        universe_source="watchlist",
        markets=markets,
        limit=limit,
        use_llm=use_llm,
        refresh_data=refresh_data,
    )


def run_valuation_scan(
    *,
    universe_source: UniverseSource = "watchlist",
    markets: set[str] | None = None,
    limit: int | None = None,
    use_llm: bool = False,
    refresh_data: bool = False,
    exclude_risk: bool = True,
    board: str | None = None,
    sector: str | None = None,
) -> tuple[list[ValuationSnapshot], dict]:
    universe = load_universe(
        universe_source,
        markets=markets,
        limit=limit,
        exclude_risk=exclude_risk,
        board=board,
        sector=sector,
    )
    results: list[ValuationSnapshot] = []
    for idx, item in enumerate(universe, start=1):
        ticker = item["ticker"]
        market = item["market"]
        logger.info(
            "[BatchValuation] %d/%d %s (%s)",
            idx,
            len(universe),
            ticker,
            market,
        )
        results.append(
            run_batch_valuation(
                ticker,
                market,
                name=item.get("name", ""),
                sector=item.get("sector", ""),
                use_llm=use_llm,
                refresh_data=refresh_data,
            )
        )

    results.sort(
        key=lambda r: (
            r.action != "strong_candidate",
            r.action != "deep_research",
            -(r.margin_of_safety or -999),
            -r.quality_score,
        )
    )
    saved = save_valuation_scan(results, metadata={
        "universe_source": universe_source,
        "markets": sorted(markets) if markets else [],
        "limit": limit,
        "use_llm": use_llm,
        "refresh_data": refresh_data,
        "exclude_risk": exclude_risk,
        "board": board,
        "sector": sector,
        "universe_size": len(universe),
    })
    return results, saved


def latest_scan_file() -> Path | None:
    out_dir = get_output_dir("valuation_scans")
    files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_latest_scan() -> dict | None:
    path = latest_scan_file()
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
