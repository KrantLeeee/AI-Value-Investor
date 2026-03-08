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

logger = get_logger(__name__)

AGENT_NAME = "fundamentals"


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

    # ── 1. Profitability (30 pts) ──────────────────────────────────────────────
    roe = _safe(metric_rows[0]["roe"]) if metric_rows else None

    # Derive ROE from income + balance if not stored in metrics table
    if roe is None and income_rows and balance_rows:
        ni  = _safe(income_rows[0].get("net_income"))
        eq  = _safe(balance_rows[0].get("total_equity"))
        if ni and eq and eq != 0:
            roe = ni / eq * 100  # store as %

    net_margin_direct = _safe(metric_rows[0].get("operating_margin")) if metric_rows else None

    # Estimate net margin from income statement if not in metrics
    if net_margin_direct is None and income_rows:
        rev = _safe(income_rows[0].get("revenue"))
        ni  = _safe(income_rows[0].get("net_income"))
        net_margin = (ni / rev) if (rev and ni and rev != 0) else None
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
        de = (total_debt / total_equity) if (total_debt and total_equity and total_equity != 0) else None

    # Estimate current ratio from balance sheet
    if cr is None and balance_rows:
        ca = _safe(balance_rows[0].get("current_assets"))
        cl = _safe(balance_rows[0].get("current_liabilities"))
        cr = (ca / cl) if (ca and cl and cl != 0) else None

    if de is not None:
        if de <= 0.3:
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
        if cr >= 2.0:
            score += 10; detail_lines.append(f"✓ 流动比率={cr:.2f} (≥2.0, +10)")
        elif cr >= 1.5:
            score += 7;  detail_lines.append(f"△ 流动比率={cr:.2f} (≥1.5, +7)")
        elif cr >= 1.0:
            score += 4;  detail_lines.append(f"△ 流动比率={cr:.2f} (≥1.0, +4)")
        else:
            detail_lines.append(f"✗ 流动比率={cr:.2f} (<1.0, +0)")
        metrics_snapshot["current_ratio"] = round(cr, 3)
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
            fcf_cover = fcf / abs(latest_ni)
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
    if score >= 70:
        signal, confidence = "bullish", min(0.9, 0.5 + (score - 70) / 100)
    elif score >= 45:
        signal, confidence = "neutral", 0.5
    else:
        signal, confidence = "bearish", min(0.9, 0.5 + (45 - score) / 100)

    metrics_snapshot["total_score"] = score
    metrics_snapshot["available_periods"] = len(income_rows)

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
