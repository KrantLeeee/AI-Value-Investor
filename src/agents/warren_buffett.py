"""Warren Buffett Agent — LLM-powered qualitative analysis.

Focuses on:
  - Moat type (brand / cost / switching costs / network effects / scale)
  - Management quality (capital allocation, ROE consistency)
  - Pricing power

All numeric calculations are done BEFORE calling LLM.
LLM only provides qualitative judgment based on pre-computed numbers.
"""

import json
from datetime import date

from src.data.database import (
    get_income_statements,
    get_financial_metrics,
    get_latest_agent_signals,
    insert_agent_signal,
)
from src.data.models import AgentSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "warren_buffett"


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _roe_consistency(metric_rows: list[dict]) -> tuple[float | None, bool]:
    """Return (mean ROE, is_consistent) — consistent = all years ≥ 15%."""
    roe_vals = [_safe(r.get("roe")) for r in metric_rows if _safe(r.get("roe")) is not None]
    if not roe_vals:
        return None, False
    # normalise to % if still in decimal
    roe_vals = [r if r > 1 else r * 100 for r in roe_vals]
    mean_roe = sum(roe_vals) / len(roe_vals)
    consistent = all(r >= 15 for r in roe_vals)
    return mean_roe, consistent


def run(
    ticker: str,
    market: str,
    fundamentals_signal: AgentSignal | None = None,
    valuation_signal: AgentSignal | None = None,
    use_llm: bool = True,
) -> AgentSignal:
    """
    Run the Buffett Agent.
    Reads financial data from SQLite, computes pre-input metrics,
    then calls LLM for qualitative judgment.
    """
    income_rows = get_income_statements(ticker, limit=5, period_type="annual")
    metric_rows = get_financial_metrics(ticker, limit=5)

    # ── Pre-compute metrics for LLM context ──────────────────────────────────
    mean_roe, roe_consistent = _roe_consistency(metric_rows)

    net_incomes = [_safe(r.get("net_income")) for r in income_rows if _safe(r.get("net_income")) is not None]
    ni_stable = False
    if len(net_incomes) >= 3:
        import statistics
        ni_mean = statistics.mean(net_incomes)
        ni_std  = statistics.stdev(net_incomes) if len(net_incomes) >= 2 else 0
        ni_cv   = (ni_std / abs(ni_mean)) if ni_mean != 0 else 999
        ni_stable = ni_cv < 0.30 and all(n > 0 for n in net_incomes)
    else:
        ni_cv = None

    # Build metrics summary table (last 3 years)
    metrics_table = "| 年份 | ROE | 营业利润率 | 净利润(亿) |\n|-----|-----|------|------|\n"
    for i, (metric_row, income_row) in enumerate(zip(metric_rows[:3], income_rows[:3])):
        yr = metric_row.get("date", "")[:4]
        roe_v = _safe(metric_row.get("roe"))
        om_v  = _safe(metric_row.get("operating_margin"))
        ni_v  = _safe(income_row.get("net_income"))
        roe_s = f"{roe_v:.1f}%" if roe_v else "N/A"
        om_s  = f"{om_v:.1f}%" if om_v else "N/A"
        ni_s  = f"{ni_v/1e8:.1f}" if ni_v else "N/A"
        metrics_table += f"| {yr} | {roe_s} | {om_s} | {ni_s} |\n"

    net_income_trend = " → ".join([
        f"{ni/1e8:.1f}亿" for ni in reversed(net_incomes[:5]) if ni
    ]) or "数据不足"

    # Build context from other agents (if available)
    fund_summary = ""
    val_summary  = ""
    if fundamentals_signal:
        fund_summary = f"评分 {fundamentals_signal.metrics.get('total_score', 'N/A')}/100, 信号: {fundamentals_signal.signal}"
    if valuation_signal:
        mos = valuation_signal.metrics.get("margin_of_safety")
        val_summary = f"安全边际 {mos*100:.1f}%" if mos else "信号: " + valuation_signal.signal

    additional_context = (
        f"净利润变异系数: {ni_cv:.2f}" if ni_cv is not None else "净利润稳定性: 数据不足"
    ) + f"\nROE均值: {mean_roe:.1f}%" if mean_roe else "\nROE: 数据不足"
    additional_context += f"\nROE是否稳定(连续≥15%): {'是' if roe_consistent else '否'}"
    additional_context += f"\n净利润是否持续为正: {'是' if ni_stable else '否'}"

    metrics_snapshot = {
        "mean_roe": round(mean_roe, 2) if mean_roe else None,
        "roe_consistent": roe_consistent,
        "ni_stable": ni_stable,
        "ni_coefficient_of_variation": round(ni_cv, 3) if ni_cv is not None else None,
    }

    # ── LLM call ──────────────────────────────────────────────────────────────
    signal, confidence, reasoning = "neutral", 0.40, "LLM 分析暂不可用（未配置 API Key）"

    if use_llm:
        try:
            from src.llm.router import call_llm, LLMError
            from src.llm.prompts import BUFFETT_SYSTEM_PROMPT, BUFFETT_USER_TEMPLATE

            user_msg = BUFFETT_USER_TEMPLATE.format(
                ticker=ticker,
                analysis_date=str(date.today()),
                fundamentals_summary=fund_summary or "未运行",
                valuation_summary=val_summary or "未运行",
                metrics_table=metrics_table,
                net_income_trend=net_income_trend,
            ) + f"\n\n**补充统计数据**:\n{additional_context}"

            llm_text = call_llm("buffett_analysis", BUFFETT_SYSTEM_PROMPT, user_msg)

            try:
                parsed = json.loads(llm_text)
                signal     = parsed.get("signal", "neutral").lower()
                confidence = float(parsed.get("confidence", 0.5))
                reasoning  = parsed.get("reasoning", llm_text)
                metrics_snapshot.update({
                    "moat_type": parsed.get("moat_type"),
                    "management_quality": parsed.get("management_quality"),
                    "has_pricing_power": parsed.get("has_pricing_power"),
                })
            except Exception:
                # LLM returned prose instead of JSON — parse signal from text
                text_lower = llm_text.lower()
                if "bullish" in text_lower or "看多" in llm_text:
                    signal = "bullish"
                elif "bearish" in text_lower or "看空" in llm_text:
                    signal = "bearish"
                else:
                    signal = "neutral"
                confidence = 0.55
                reasoning = llm_text

        except Exception as e:
            logger.warning("[Buffett] LLM call failed: %s", e)
            reasoning = f"LLM 调用失败: {e}\n\n基于代码指标的初步判断：{'ROE稳定' if roe_consistent else 'ROE不稳定'}，净利润{'稳定' if ni_stable else '不稳定'}"
            signal = "bullish" if (roe_consistent and ni_stable) else "neutral"
            confidence = 0.35

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=signal,
        confidence=round(confidence, 3),
        reasoning=reasoning,
        metrics=metrics_snapshot,
    )
    insert_agent_signal(agent_signal)
    logger.info("[Buffett] %s: signal=%s confidence=%.2f", ticker, signal, confidence)
    return agent_signal
