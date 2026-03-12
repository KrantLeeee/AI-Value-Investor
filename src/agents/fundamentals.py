"""Fundamentals Agent — pure Python scoring, no LLM required.

Scoring methodology (total 100 points):
  Profitability (30 pts):  ROE, net margin
  Growth       (30 pts):  Revenue YoY, Net Income YoY
  Safety       (20 pts):  Debt/Equity, Current Ratio
  Cash Quality (20 pts):  FCF > 0, FCF/Net Income coverage

Signal thresholds:
  score >= 70  → bullish
  score >= 45  → neutral
  score <  45  → bearish

NOTE: Financial stocks (banks/insurance) and utility stocks have different
      safety metric standards - high D/E is normal for these industries.
"""

from datetime import date
import json

from src.data.database import (
    get_income_statements,
    get_balance_sheets,
    get_cash_flows,
    get_financial_metrics,
    insert_agent_signal,
)
from src.data.models import AgentSignal
from src.utils.logger import get_logger
from src.utils.calculation_tracer import CalculationTracer

logger = get_logger(__name__)

AGENT_NAME = "fundamentals"


def _compute_5_year_trends(metrics_history: list[dict]) -> dict:
    """
    P0-1: Compute 5-year trend metrics.

    Returns dict with:
    - roe_trend: "improving" | "stable" | "declining"
    - roic_trend: "improving" | "stable" | "declining"
    - margin_trend: "improving" | "stable" | "declining"
    - avg_roe_5y: float
    - avg_roic_5y: float
    """
    if len(metrics_history) < 3:
        return {"insufficient_data": True}

    # Sort by date ascending for trend calculation
    sorted_metrics = sorted(metrics_history, key=lambda x: x.get("date", ""))

    def _get_trend(values: list[float]) -> str:
        if len(values) < 3:
            return "insufficient"
        # Simple linear trend: compare first half avg to second half avg
        mid = len(values) // 2
        first_half = sum(values[:mid]) / mid if mid > 0 else 0
        second_half = sum(values[mid:]) / (len(values) - mid) if (len(values) - mid) > 0 else 0
        diff_pct = (second_half - first_half) / first_half * 100 if first_half != 0 else 0
        if diff_pct > 10:
            return "improving"
        elif diff_pct < -10:
            return "declining"
        return "stable"

    roe_values = [m["roe"] for m in sorted_metrics if m.get("roe") is not None]
    roic_values = [m["roic"] for m in sorted_metrics if m.get("roic") is not None]
    margin_values = [m["gross_margin"] for m in sorted_metrics if m.get("gross_margin") is not None]

    return {
        "roe_trend": _get_trend(roe_values),
        "roic_trend": _get_trend(roic_values) if roic_values else "no_data",
        "margin_trend": _get_trend(margin_values) if margin_values else "no_data",
        "avg_roe_5y": round(sum(roe_values) / len(roe_values), 2) if roe_values else None,
        "avg_roic_5y": round(sum(roic_values) / len(roic_values), 2) if roic_values else None,
        "avg_margin_5y": round(sum(margin_values) / len(margin_values), 2) if margin_values else None,
    }

# Financial stock tickers - high D/E is normal for banks/insurance
_FINANCIAL_TICKERS = {
    "601318.SH", "600036.SH", "601398.SH", "601166.SH", "601628.SH",
    "601288.SH", "601988.SH", "601939.SH", "601328.SH", "600000.SH",
    "000001.SZ", "002142.SZ", "600016.SH", "601229.SH", "601818.SH",
}

# Utility/infrastructure tickers - high D/E and low current ratio is normal
_UTILITY_TICKERS = {
    "600900.SH", "601985.SH", "600023.SH", "600025.SH", "000027.SZ",
    "600011.SH", "600795.SH", "601991.SH", "600886.SH",
}


def _is_financial_or_utility(ticker: str) -> tuple[bool, bool]:
    """Check if ticker is financial or utility stock (high leverage is normal)."""
    is_financial = ticker in _FINANCIAL_TICKERS
    is_utility = ticker in _UTILITY_TICKERS
    return is_financial, is_utility


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _yoy(values: list[float | None]) -> float | None:
    """Year-over-year growth rate: (current - prior) / |prior|"""
    if len(values) < 2:
        return None
    cur, prior = _safe(values[0]), _safe(values[1])
    if cur is None or prior is None or prior == 0:
        return None
    return (cur - prior) / abs(prior)


def run(ticker: str, market: str) -> AgentSignal:
    """
    Run the Fundamentals Agent for a given ticker.
    Reads data from SQLite (must be fetched first via `invest fetch`).
    Returns an AgentSignal and persists it to the database.
    """
    income_rows  = get_income_statements(ticker, limit=5, period_type="annual")
    balance_rows = get_balance_sheets(ticker, limit=3, period_type="annual")
    cashflow_rows = get_cash_flows(ticker, limit=3, period_type="annual")
    metric_rows  = get_financial_metrics(ticker, limit=3)

    # ── QVeris supplement: fill missing balance sheet fields ───────────────────
    # AKShare often lacks current_assets / current_liabilities for A-shares.
    # Fetch from QVeris iFinD as a fallback enrichment (not DB write).
    if market == "a_share" and balance_rows:
        import os
        has_ca = any(_safe(r.get("current_assets")) for r in balance_rows)
        has_cl = any(_safe(r.get("current_liabilities")) for r in balance_rows)
        if not (has_ca and has_cl):
            try:
                from src.data.qveris_source import QVerisSource
                qsrc = QVerisSource()
                if qsrc.health_check():
                    qb = qsrc.get_balance_sheets(ticker, market, limit=1)
                    if qb and balance_rows:
                        # Patch the first row dict with QVeris values
                        if not has_ca and qb[0].current_assets:
                            balance_rows[0]["current_assets"] = qb[0].current_assets
                        if not has_cl and qb[0].current_liabilities:
                            balance_rows[0]["current_liabilities"] = qb[0].current_liabilities
                        if not _safe(balance_rows[0].get("cash_and_equivalents")) and qb[0].cash_and_equivalents:
                            balance_rows[0]["cash_and_equivalents"] = qb[0].cash_and_equivalents
                        logger.info("[Fundamentals] Enriched balance sheet from QVeris")
            except Exception as _e:
                logger.debug("[Fundamentals] QVeris balance enrichment failed: %s", _e)

    # ── QVeris supplement: fill income YoY gap ────────────────────────────────
    # If fewer than 2 annual income rows exist, YoY is impossible.
    if market == "a_share" and len(income_rows) < 2:
        try:
            from src.data.qveris_source import QVerisSource
            qsrc = QVerisSource()
            if qsrc.health_check():
                qi = qsrc.get_income_statements(ticker, market, limit=3)
                if len(qi) >= 2:
                    synthetic = [
                        {"revenue": s.revenue, "net_income": s.net_income,
                         "eps": s.eps, "period_end_date": str(s.period_end_date)}
                        for s in qi
                    ]
                    income_rows = income_rows + synthetic[len(income_rows):]
                    logger.info("[Fundamentals] Enriched income from QVeris (now %d rows)", len(income_rows))
        except Exception as _e:
            logger.debug("[Fundamentals] QVeris income enrichment failed: %s", _e)


    score = 0
    detail_lines: list[str] = []
    metrics_snapshot: dict = {}

    # P0-2: Initialize calculation tracer for transparency
    tracer = CalculationTracer()

    # P0-1: Compute 5-year trends from metrics history
    metrics_for_trends = get_financial_metrics(ticker, limit=5)
    five_year_trends = _compute_5_year_trends(metrics_for_trends)
    metrics_snapshot["5_year_trends"] = five_year_trends

    # Check if this is a financial or utility stock (different safety standards)
    is_financial, is_utility = _is_financial_or_utility(ticker)
    if is_financial:
        detail_lines.append("🏦 金融股：高杠杆是银行/保险商业模式本身，D/E和流动比率不适用常规标准")
    elif is_utility:
        detail_lines.append("⚡ 公用事业股：资本密集型行业，高D/E和低流动比率是行业常态")

    # ── 1. Profitability (30 pts) ──────────────────────────────────────────────
    roe = _safe(metric_rows[0]["roe"]) if metric_rows else None

    # Derive ROE from income + balance if not stored in metrics table
    if roe is None and income_rows and balance_rows:
        ni  = _safe(income_rows[0].get("net_income"))
        eq  = _safe(balance_rows[0].get("total_equity"))
        if ni and eq and eq != 0:
            roe = tracer.trace_calculation(
                metric_name="ROE",
                formula="net_income / total_equity * 100",
                inputs={
                    "net_income": {"value": ni, "source": "database", "period": income_rows[0].get("period_end_date")},
                    "total_equity": {"value": eq, "source": "database", "period": balance_rows[0].get("period_end_date")},
                },
                result=(ni / eq) * 100,
                unit="%",
            )

    net_margin_direct = _safe(metric_rows[0].get("operating_margin")) if metric_rows else None

    # Estimate net margin from income statement if not in metrics
    if net_margin_direct is None and income_rows:
        rev = _safe(income_rows[0].get("revenue"))
        ni  = _safe(income_rows[0].get("net_income"))
        if rev and ni and rev != 0:
            net_margin = tracer.trace_calculation(
                metric_name="净利率",
                formula="net_income / revenue * 100",
                inputs={
                    "net_income": {"value": ni, "source": "database", "period": income_rows[0].get("period_end_date")},
                    "revenue": {"value": rev, "source": "database", "period": income_rows[0].get("period_end_date")},
                },
                result=(ni / rev) * 100,
                unit="%",
            )
        else:
            net_margin = None
    else:
        net_margin = net_margin_direct

    if roe is not None:
        roe_pct = roe if roe > 1 else roe * 100  # normalise to %
        if roe_pct >= 20:
            score += 20; detail_lines.append(f"✓ ROE={roe_pct:.1f}% (≥20%, +20)")
        elif roe_pct >= 15:
            score += 15; detail_lines.append(f"✓ ROE={roe_pct:.1f}% (≥15%, +15)")
        elif roe_pct >= 10:
            score += 8; detail_lines.append(f"△ ROE={roe_pct:.1f}% (≥10%, +8)")
        else:
            detail_lines.append(f"✗ ROE={roe_pct:.1f}% (<10%, +0)")
        metrics_snapshot["roe"] = round(roe_pct, 2)
    else:
        detail_lines.append("- ROE 数据缺失")

    if net_margin is not None:
        nm_pct = net_margin if net_margin > 1 else net_margin * 100
        if nm_pct >= 15:
            score += 10; detail_lines.append(f"✓ 净利率={nm_pct:.1f}% (≥15%, +10)")
        elif nm_pct >= 8:
            score += 6;  detail_lines.append(f"△ 净利率={nm_pct:.1f}% (≥8%, +6)")
        else:
            detail_lines.append(f"✗ 净利率={nm_pct:.1f}% (<8%, +0)")
        metrics_snapshot["net_margin_pct"] = round(nm_pct, 2)
    else:
        detail_lines.append("- 净利率数据缺失（从财务指标表）")

    # ── 2. Growth (30 pts) ────────────────────────────────────────────────────
    revenues = [_safe(r.get("revenue")) for r in income_rows]
    net_incomes = [_safe(r.get("net_income")) for r in income_rows]

    rev_yoy  = _yoy(revenues)
    ni_yoy   = _yoy(net_incomes)

    if rev_yoy is not None:
        rev_pct = rev_yoy * 100
        if rev_pct >= 15:
            score += 15; detail_lines.append(f"✓ 营收YoY={rev_pct:.1f}% (≥15%, +15)")
        elif rev_pct >= 8:
            score += 10; detail_lines.append(f"△ 营收YoY={rev_pct:.1f}% (≥8%, +10)")
        elif rev_pct >= 0:
            score += 5;  detail_lines.append(f"△ 营收YoY={rev_pct:.1f}% (≥0%, +5)")
        else:
            detail_lines.append(f"✗ 营收YoY={rev_pct:.1f}% (负增长, +0)")
        metrics_snapshot["revenue_yoy_pct"] = round(rev_pct, 2)
    else:
        detail_lines.append("- 营收增速数据不足")

    if ni_yoy is not None:
        ni_pct = ni_yoy * 100
        if ni_pct >= 15:
            score += 15; detail_lines.append(f"✓ 净利YoY={ni_pct:.1f}% (≥15%, +15)")
        elif ni_pct >= 8:
            score += 10; detail_lines.append(f"△ 净利YoY={ni_pct:.1f}% (≥8%, +10)")
        elif ni_pct >= 0:
            score += 5;  detail_lines.append(f"△ 净利YoY={ni_pct:.1f}% (≥0%, +5)")
        else:
            detail_lines.append(f"✗ 净利YoY={ni_pct:.1f}% (负增长, +0)")
        metrics_snapshot["net_income_yoy_pct"] = round(ni_pct, 2)
    else:
        detail_lines.append("- 净利增速数据不足")

    # ── 3. Financial Safety (20 pts) ─────────────────────────────────────────
    de = _safe(metric_rows[0].get("debt_to_equity")) if metric_rows else None
    cr = _safe(metric_rows[0].get("current_ratio")) if metric_rows else None

    # Estimate D/E from balance sheet if not in metrics
    if de is None and balance_rows:
        total_debt   = _safe(balance_rows[0].get("total_debt"))
        total_equity = _safe(balance_rows[0].get("total_equity"))
        if total_debt and total_equity and total_equity != 0:
            de = tracer.trace_calculation(
                metric_name="D/E比率",
                formula="total_debt / total_equity",
                inputs={
                    "total_debt": {"value": total_debt, "source": "database", "period": balance_rows[0].get("period_end_date")},
                    "total_equity": {"value": total_equity, "source": "database", "period": balance_rows[0].get("period_end_date")},
                },
                result=total_debt / total_equity,
                unit="x",
            )
        else:
            de = None

    # Estimate current ratio from balance sheet
    if cr is None and balance_rows:
        ca = _safe(balance_rows[0].get("current_assets"))
        cl = _safe(balance_rows[0].get("current_liabilities"))
        if ca and cl and cl != 0:
            cr = tracer.trace_calculation(
                metric_name="流动比率",
                formula="current_assets / current_liabilities",
                inputs={
                    "current_assets": {"value": ca, "source": "database", "period": balance_rows[0].get("period_end_date")},
                    "current_liabilities": {"value": cl, "source": "database", "period": balance_rows[0].get("period_end_date")},
                },
                result=ca / cl,
                unit="x",
            )
        else:
            cr = None

    if de is not None:
        # BUG-FIX: Financial/utility stocks have high D/E by design - don't penalize
        if is_financial or is_utility:
            score += 10  # Full points - high leverage is expected
            detail_lines.append(f"✓ D/E={de:.2f} (行业特性，满分+10)")
        elif de <= 0.3:
            score += 10; detail_lines.append(f"✓ D/E={de:.2f} (≤0.3, +10)")
        elif de <= 0.5:
            score += 7;  detail_lines.append(f"△ D/E={de:.2f} (≤0.5, +7)")
        elif de <= 1.0:
            score += 3;  detail_lines.append(f"△ D/E={de:.2f} (≤1.0, +3)")
        else:
            detail_lines.append(f"✗ D/E={de:.2f} (>1.0, +0)")
        metrics_snapshot["debt_to_equity"] = round(de, 3)
    else:
        detail_lines.append("- 负债/权益数据缺失")

    if cr is not None:
        # BUG-FIX: Financial/utility stocks have low current ratio by design - don't penalize
        if is_financial or is_utility:
            score += 10  # Full points - low liquidity ratio is expected
            detail_lines.append(f"✓ 流动比率={cr:.2f} (行业特性，满分+10)")
        elif cr >= 2.0:
            score += 10; detail_lines.append(f"✓ 流动比率={cr:.2f} (≥2.0, +10)")
        elif cr >= 1.5:
            score += 7;  detail_lines.append(f"△ 流动比率={cr:.2f} (≥1.5, +7)")
        elif cr >= 1.0:
            score += 4;  detail_lines.append(f"△ 流动比率={cr:.2f} (≥1.0, +4)")
        else:
            detail_lines.append(f"✗ 流动比率={cr:.2f} (<1.0, +0)")
        metrics_snapshot["current_ratio"] = round(cr, 3)
    else:
        # For financial/utility stocks, missing current ratio is still ok
        if is_financial or is_utility:
            score += 10
            detail_lines.append("✓ 流动比率数据缺失（行业特性，不扣分）")
        else:
            detail_lines.append("- 流动比率数据缺失")

    # ── 4. Cash Quality (20 pts) ──────────────────────────────────────────────
    if cashflow_rows:
        ocf = _safe(cashflow_rows[0].get("operating_cash_flow"))
        fcf = _safe(cashflow_rows[0].get("free_cash_flow"))
        latest_ni = _safe(income_rows[0].get("net_income")) if income_rows else None

        if fcf is not None and fcf > 0:
            score += 10; detail_lines.append(f"✓ FCF={fcf/1e8:.1f}亿 (>0, +10)")
        elif ocf is not None and ocf > 0:
            score += 5;  detail_lines.append(f"△ OCF={ocf/1e8:.1f}亿>0但FCF未知 (+5)")
        else:
            detail_lines.append("✗ FCF/OCF ≤0 (+0)")

        if fcf is not None and latest_ni is not None and latest_ni != 0:
            fcf_cover = tracer.trace_calculation(
                metric_name="FCF/净利覆盖率",
                formula="free_cash_flow / abs(net_income)",
                inputs={
                    "free_cash_flow": {"value": fcf, "source": "database", "period": cashflow_rows[0].get("period_end_date")},
                    "net_income": {"value": latest_ni, "source": "database", "period": income_rows[0].get("period_end_date") if income_rows else "unknown"},
                },
                result=fcf / abs(latest_ni),
                unit="x",
            )
            if fcf_cover >= 0.8:
                score += 10; detail_lines.append(f"✓ FCF/净利={fcf_cover:.2f} (≥0.8, +10)")
            elif fcf_cover >= 0.5:
                score += 6;  detail_lines.append(f"△ FCF/净利={fcf_cover:.2f} (≥0.5, +6)")
            else:
                detail_lines.append(f"✗ FCF/净利={fcf_cover:.2f} (<0.5, +0)")
            metrics_snapshot["fcf_to_net_income"] = round(fcf_cover, 3)
        else:
            detail_lines.append("- FCF/净利比率数据不足")
    else:
        detail_lines.append("- 现金流数据缺失")

    # ── Signal determination ──────────────────────────────────────────────────
    score = min(score, 100)

    # Count how many core metrics have data
    # Required for meaningful assessment: ROE, net_margin, revenue_yoy, net_income_yoy
    data_fields_available = sum([
        roe is not None,
        net_margin is not None,
        rev_yoy is not None,
        ni_yoy is not None,
        de is not None or cr is not None,  # at least one safety metric
        len(cashflow_rows) > 0,  # any cashflow data
    ])

    # BUG-FIX: If insufficient data fields, force INSUFFICIENT_DATA signal
    # This prevents misleading BEARISH with 90% confidence on zero data
    MIN_REQUIRED_FIELDS = 3  # Need at least 3 of 6 core metrics for meaningful signal

    if data_fields_available < MIN_REQUIRED_FIELDS:
        signal = "insufficient_data"
        confidence = 0.0
        logger.warning(
            "[Fundamentals] %s: Only %d/%d data fields available, forcing insufficient_data signal",
            ticker, data_fields_available, 6
        )
        detail_lines.insert(0, f"⚠️ 数据不足：仅有 {data_fields_available}/6 项核心指标，无法进行可靠评估")
    elif score >= 70:
        signal, confidence = "bullish", min(0.9, 0.5 + (score - 70) / 100)
    elif score >= 45:
        signal, confidence = "neutral", 0.5
    else:
        signal, confidence = "bearish", min(0.9, 0.5 + (45 - score) / 100)

    metrics_snapshot["total_score"] = score
    metrics_snapshot["available_periods"] = len(income_rows)
    metrics_snapshot["data_fields_available"] = data_fields_available

    # P0-2: Add calculation traces for transparency
    metrics_snapshot["calculation_traces"] = [
        {"metric": t.metric_name, "explanation": tracer.explain(t.metric_name)}
        for t in tracer.get_traces()
    ]

    # P0-1: Add 5-year trend summary to detail lines
    if not five_year_trends.get("insufficient_data"):
        trend_labels = {"improving": "↑改善", "stable": "→稳定", "declining": "↓下滑", "no_data": "数据不足"}
        detail_lines.append(f"\n【5年趋势分析】")
        detail_lines.append(f"  ROE趋势: {trend_labels.get(five_year_trends.get('roe_trend'), '未知')}")
        if five_year_trends.get("avg_roe_5y"):
            detail_lines.append(f"  5年平均ROE: {five_year_trends['avg_roe_5y']:.1f}%")
        if five_year_trends.get("roic_trend") != "no_data":
            detail_lines.append(f"  ROIC趋势: {trend_labels.get(five_year_trends.get('roic_trend'), '未知')}")
        if five_year_trends.get("margin_trend") != "no_data":
            detail_lines.append(f"  毛利率趋势: {trend_labels.get(five_year_trends.get('margin_trend'), '未知')}")

    # Expose raw revenue for Ch1 template (format_yuan needs absolute value)
    if income_rows and _safe(income_rows[0].get("revenue")):
        metrics_snapshot["revenue"] = _safe(income_rows[0].get("revenue"))
    # Expose revenue_yoy_pct if not already set (it's set above if rows>=2)
    if "revenue_yoy_pct" not in metrics_snapshot and rev_yoy is not None:
        metrics_snapshot["revenue_yoy_pct"] = round(rev_yoy * 100, 2)

    reasoning = (
        f"基本面评分 {score}/100。\n"
        + "\n".join(detail_lines)
    )

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=signal,
        confidence=round(confidence, 3),
        reasoning=reasoning,
        metrics=metrics_snapshot,
    )
    insert_agent_signal(agent_signal)
    logger.info("[Fundamentals] %s: score=%d signal=%s", ticker, score, signal)
    return agent_signal


def calculate_fundamentals_score(metrics: dict, industry_config: dict) -> dict:
    """
    Calculate fundamentals score with cycle adjustment for cyclical industries.

    Args:
        metrics: Financial metrics dict
        industry_config: Industry configuration dict

    Returns:
        dict with scoring metrics and adjustments
    """
    adjustments = []
    result = {}

    # Check if cycle-adjusted mode
    if industry_config.get('scoring_mode') == 'cycle_adjusted':
        # Use 5-year averages for cyclical industries
        roe_for_scoring = metrics.get('roe_5yr_avg', metrics.get('roe'))
        net_margin_for_scoring = metrics.get('net_margin_5yr_avg', metrics.get('net_margin'))

        if roe_for_scoring != metrics.get('roe'):
            adjustments.append({
                'metric': 'ROE',
                'original': metrics.get('roe'),
                'adjusted': roe_for_scoring,
                'reason': '周期行业使用5年均值评分'
            })

        if net_margin_for_scoring != metrics.get('net_margin'):
            adjustments.append({
                'metric': 'Net Margin',
                'original': metrics.get('net_margin'),
                'adjusted': net_margin_for_scoring,
                'reason': '周期行业使用5年均值评分'
            })

        result['roe_for_scoring'] = roe_for_scoring
        result['net_margin_for_scoring'] = net_margin_for_scoring
    else:
        # Use current values for non-cyclical
        result['roe_for_scoring'] = metrics.get('roe')
        result['net_margin_for_scoring'] = metrics.get('net_margin')

    result['adjustments'] = adjustments
    return result
