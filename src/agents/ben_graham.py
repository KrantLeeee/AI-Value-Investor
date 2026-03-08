"""Ben Graham Agent — LLM-powered margin-of-safety analysis.

Graham's defensive investor criteria:
  1. Adequate size (not implemented — qualitative)
  2. Strong financial condition (Current Ratio ≥ 2.0, D/E ≤ 0.5)
  3. Earnings stability (positive EPS for 10+ years)
  4. Dividend record (optional)
  5. Earnings growth (EPS up ≥ 33% over 10 years)
  6. Moderate P/E (≤ 15)
  7. Moderate P/B (≤ 1.5, or P/E × P/B ≤ 22.5)

Net-Net check: (Current Assets − Total Liabilities) / Shares > Current Price → deep value
"""

import json
from datetime import date

from src.data.database import (
    get_income_statements,
    get_balance_sheets,
    get_financial_metrics,
    insert_agent_signal,
)
from src.data.models import AgentSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "ben_graham"

# Signal ordering for comparison and capping
SIGNAL_ORDER = {"bearish": 0, "neutral": 1, "bullish": 2}


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _apply_signal_cap(
    llm_signal: str,
    llm_confidence: float,
    criteria_passed: int,
    criteria_total: int,
    data_completeness: float,
) -> tuple[str, float]:
    """
    Apply hard signal caps based on criteria_passed count.

    Rules:
    - 0/7 criteria → hard bearish (0.70)
    - 1-2/7 → bearish with dynamic confidence (0.40 + 0.15 * data_completeness)
    - 3-4/7 → cap at neutral
    - 5-6/7 → cap at bullish (LLM can choose neutral/bullish)
    - 7/7 → no restrictions

    Args:
        llm_signal: Signal from LLM ("bearish", "neutral", "bullish")
        llm_confidence: Confidence from LLM (0.0-1.0)
        criteria_passed: Number of Graham criteria passed
        criteria_total: Total number of criteria evaluated
        data_completeness: Data completeness ratio (0.0-1.0)

    Returns:
        Tuple of (final_signal, final_confidence)
    """
    # Determine max allowed signal based on criteria_passed
    if criteria_total == 0:
        # No criteria evaluated → neutral with low confidence
        return "neutral", 0.30

    if criteria_passed == 0:
        # 0/7 → hard bearish
        return "bearish", 0.70

    if criteria_passed <= 2:
        # 1-2/7 → bearish with dynamic confidence
        confidence = 0.40 + 0.15 * data_completeness
        return "bearish", round(confidence, 3)

    if criteria_passed <= 4:
        # 3-4/7 → cap at neutral
        max_signal = "neutral"
        max_order = SIGNAL_ORDER[max_signal]
    elif criteria_passed <= 6:
        # 5-6/7 → cap at bullish (but LLM can choose neutral)
        max_signal = "bullish"
        max_order = SIGNAL_ORDER[max_signal]
    else:
        # 7/7 → no restrictions
        return llm_signal, llm_confidence

    # Downgrade signal if LLM exceeded the cap
    llm_order = SIGNAL_ORDER.get(llm_signal, SIGNAL_ORDER["neutral"])
    if llm_order > max_order:
        # Downgrade signal and reduce confidence
        final_signal = max_signal
        final_confidence = llm_confidence * 0.7  # Reduce confidence by 30%
        return final_signal, round(final_confidence, 3)

    # Signal is within cap
    return llm_signal, llm_confidence


def run(
    ticker: str,
    market: str,
    valuation_signal: AgentSignal | None = None,
    use_llm: bool = True,
) -> AgentSignal:
    """
    Run the Graham Agent.
    All numeric checks run in Python; LLM provides holistic judgment.
    """
    income_rows  = get_income_statements(ticker, limit=10, period_type="annual")
    balance_rows = get_balance_sheets(ticker, limit=5, period_type="annual")
    metric_rows  = get_financial_metrics(ticker, limit=5)

    # ── Pre-compute Graham criteria ────────────────────────────────────────────
    # Criterion: Strong financial condition
    current_ratio = _safe(metric_rows[0].get("current_ratio")) if metric_rows else None
    debt_to_equity = _safe(metric_rows[0].get("debt_to_equity")) if metric_rows else None

    # Estimate from balance sheet if not in metrics
    if (current_ratio is None or debt_to_equity is None) and balance_rows:
        ca = _safe(balance_rows[0].get("current_assets"))
        cl = _safe(balance_rows[0].get("current_liabilities"))
        td = _safe(balance_rows[0].get("total_debt")) or _safe(balance_rows[0].get("total_liabilities"))
        eq = _safe(balance_rows[0].get("total_equity"))
        if ca and cl and cl > 0:
            current_ratio = current_ratio or ca / cl
        if td and eq and eq > 0:
            debt_to_equity = debt_to_equity or td / eq

    # Criterion: Earnings stability — how many years of positive net income
    eps_values = [_safe(r.get("eps")) for r in income_rows]
    ni_values  = [_safe(r.get("net_income")) for r in income_rows]
    profitable_years = sum(1 for ni in ni_values if ni is not None and ni > 0)
    all_positive = profitable_years == len([ni for ni in ni_values if ni is not None])

    # Earnings growth: EPS(oldest) → EPS(latest) change
    eps_growth = None
    valid_eps = [(i, e) for i, e in enumerate(eps_values) if e is not None]
    if len(valid_eps) >= 2:
        latest_eps = valid_eps[0][1]
        oldest_eps = valid_eps[-1][1]
        if oldest_eps > 0:
            eps_growth = (latest_eps - oldest_eps) / abs(oldest_eps)

    # Earnings stability (std/mean, lower is better)
    earnings_stability_text = "数据不足"
    if len(ni_values) >= 3:
        valid_ni = [ni for ni in ni_values if ni is not None]
        import statistics
        if len(valid_ni) >= 2:
            ni_mean = statistics.mean(valid_ni)
            ni_std  = statistics.stdev(valid_ni)
            cv = ni_std / abs(ni_mean) if ni_mean != 0 else 999
            earnings_stability_text = f"变异系数(CV)={cv:.2f} ({'稳定' if cv < 0.3 else '波动较大'})"

    # Valuation multiples check
    pe  = _safe(metric_rows[0].get("pe_ratio")) if metric_rows else None
    pb  = _safe(metric_rows[0].get("pb_ratio")) if metric_rows else None
    pe_pb_product = (pe * pb) if (pe and pb) else None

    # Pull Graham Number and Net-Net from valuation agent if available
    graham_number = valuation_signal.metrics.get("graham_number") if valuation_signal else None
    net_net_ratio = valuation_signal.metrics.get("net_net_ratio") if valuation_signal else None
    dcf_value     = valuation_signal.metrics.get("dcf_per_share") if valuation_signal else None
    margin_of_safety = valuation_signal.metrics.get("margin_of_safety") if valuation_signal else None
    current_price = valuation_signal.metrics.get("current_price") if valuation_signal else None

    # Count how many Graham criteria are met
    criteria_passed = []
    if current_ratio and current_ratio >= 2.0:
        criteria_passed.append(f"✓ 流动比率={current_ratio:.2f} ≥ 2.0")
    elif current_ratio:
        criteria_passed.append(f"✗ 流动比率={current_ratio:.2f} < 2.0")

    if debt_to_equity and debt_to_equity <= 0.5:
        criteria_passed.append(f"✓ D/E={debt_to_equity:.2f} ≤ 0.5")
    elif debt_to_equity:
        criteria_passed.append(f"✗ D/E={debt_to_equity:.2f} > 0.5")

    if profitable_years >= 5:
        criteria_passed.append(f"✓ 连续盈利 {profitable_years} 年")
    else:
        criteria_passed.append(f"✗ 仅 {profitable_years} 年盈利")

    if eps_growth is not None and eps_growth >= 0.33:
        criteria_passed.append(f"✓ EPS增长 {eps_growth*100:.0f}% ≥ 33%")
    elif eps_growth is not None:
        criteria_passed.append(f"△ EPS增长 {eps_growth*100:.0f}% < 33%")

    if pe and pe <= 15:
        criteria_passed.append(f"✓ P/E={pe:.1f} ≤ 15")
    elif pe:
        criteria_passed.append(f"✗ P/E={pe:.1f} > 15")

    if pe_pb_product and pe_pb_product <= 22.5:
        criteria_passed.append(f"✓ P/E×P/B={pe_pb_product:.1f} ≤ 22.5")
    elif pe_pb_product:
        criteria_passed.append(f"✗ P/E×P/B={pe_pb_product:.1f} > 22.5")

    metrics_snapshot = {
        "current_ratio": round(current_ratio, 3) if current_ratio else None,
        "debt_to_equity": round(debt_to_equity, 3) if debt_to_equity else None,
        "profitable_years": profitable_years,
        "eps_growth_pct": round(eps_growth * 100, 1) if eps_growth else None,
        "pe_ratio": pe,
        "criteria_passed": len([c for c in criteria_passed if c.startswith("✓")]),
        "criteria_total": len(criteria_passed),
    }

    # ── Calculate data completeness ──────────────────────────────────────────
    # Used for dynamic confidence adjustment
    total_data_points = 6  # CR, D/E, profitable_years, eps_growth, pe, pe_pb_product
    available_data_points = sum([
        1 if current_ratio is not None else 0,
        1 if debt_to_equity is not None else 0,
        1 if profitable_years > 0 else 0,
        1 if eps_growth is not None else 0,
        1 if pe is not None else 0,
        1 if pe_pb_product is not None else 0,
    ])
    data_completeness = available_data_points / total_data_points if total_data_points > 0 else 0.0

    # ── Apply hard rules based on criteria_passed ────────────────────────────
    n_pass = metrics_snapshot["criteria_passed"]
    n_total = metrics_snapshot["criteria_total"]

    # Initialize default values
    signal, confidence, reasoning = "neutral", 0.40, "默认中性信号"

    # Check if we have sufficient data
    # If almost all key metrics are missing, treat as insufficient data
    has_valuation_data = pe is not None or pb is not None
    has_financial_data = current_ratio is not None or debt_to_equity is not None
    has_earnings_data = eps_growth is not None or profitable_years > 0

    insufficient_data = not (has_valuation_data or has_financial_data or has_earnings_data)

    # Hard rule: missing criteria_passed (insufficient data) → neutral (0.30)
    if insufficient_data or n_total == 0:
        signal, confidence = "neutral", 0.30
        reasoning = "数据不足，无法进行格雷厄姆分析"
        use_llm = False
    # Hard rule: 0/7 criteria → hard bearish, skip LLM
    elif n_pass == 0 and n_total > 0:
        signal, confidence = "bearish", 0.70
        reasoning = f"硬性规则: 0/{n_total} 条格雷厄姆标准通过 → 强烈看空\n\n" + "\n".join(criteria_passed)
        # Skip LLM for 0/7 case
        use_llm = False
    elif not use_llm:
        # If LLM is disabled and not 0/7 case, apply signal cap to default
        signal, confidence = _apply_signal_cap(
            llm_signal="neutral",
            llm_confidence=0.40,
            criteria_passed=n_pass,
            criteria_total=n_total,
            data_completeness=data_completeness,
        )
        reasoning = f"LLM 已禁用。格雷厄姆标准: {n_pass}/{n_total} 通过\n" + "\n".join(criteria_passed)

    # ── LLM call (if enabled and not skipped by hard rules) ──────────────────
    if use_llm:
        try:
            from src.llm.router import call_llm, LLMError
            from src.llm.prompts import GRAHAM_SYSTEM_PROMPT, GRAHAM_USER_TEMPLATE

            user_msg = GRAHAM_USER_TEMPLATE.format(
                ticker=ticker,
                analysis_date=str(date.today()),
                graham_number=f"¥{graham_number:.2f}" if graham_number else "N/A",
                dcf_value=f"¥{dcf_value:.2f}" if dcf_value else "N/A",
                current_price=f"¥{current_price:.2f}" if current_price else "N/A",
                margin_of_safety=f"{margin_of_safety*100:.1f}%" if margin_of_safety else "N/A",
                net_net_ratio=f"{net_net_ratio:.2f}" if net_net_ratio else "N/A",
                current_ratio=f"{current_ratio:.2f}" if current_ratio else "N/A",
                debt_to_equity=f"{debt_to_equity:.2f}" if debt_to_equity else "N/A",
                profitable_years=profitable_years,
                earnings_stability=earnings_stability_text,
            ) + "\n\n**格雷厄姆标准检查**:\n" + "\n".join(criteria_passed)

            llm_text = call_llm("graham_analysis", GRAHAM_SYSTEM_PROMPT, user_msg)

            try:
                parsed = json.loads(llm_text)
                llm_signal     = parsed.get("signal", "neutral").lower()
                llm_confidence = float(parsed.get("confidence", 0.5))
                reasoning  = parsed.get("reasoning", llm_text)
                metrics_snapshot.update({
                    "margin_of_safety_adequate": parsed.get("margin_of_safety_adequate"),
                    "is_net_net": parsed.get("is_net_net", False),
                    "defensive_characteristics": parsed.get("defensive_characteristics", []),
                })
            except Exception:
                text_lower = llm_text.lower()
                llm_signal = "bullish" if ("bullish" in text_lower or "低估" in llm_text) else \
                         "bearish" if ("bearish" in text_lower or "高估" in llm_text) else "neutral"
                llm_confidence = 0.50
                reasoning = llm_text

            # Apply signal cap based on criteria_passed
            signal, confidence = _apply_signal_cap(
                llm_signal=llm_signal,
                llm_confidence=llm_confidence,
                criteria_passed=n_pass,
                criteria_total=n_total,
                data_completeness=data_completeness,
            )

        except Exception as e:
            logger.warning("[Graham] LLM call failed: %s", e)
            # Code-only fallback signal
            if n_total > 0 and n_pass / n_total >= 0.6:
                llm_signal, llm_confidence = "bullish", 0.45
            elif n_total > 0 and n_pass / n_total <= 0.3:
                llm_signal, llm_confidence = "bearish", 0.45
            else:
                llm_signal, llm_confidence = "neutral", 0.35

            # Apply signal cap even for fallback
            signal, confidence = _apply_signal_cap(
                llm_signal=llm_signal,
                llm_confidence=llm_confidence,
                criteria_passed=n_pass,
                criteria_total=n_total,
                data_completeness=data_completeness,
            )
            reasoning = f"LLM 不可用。格雷厄姆标准: {n_pass}/{n_total} 通过\n" + "\n".join(criteria_passed)

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=signal,
        confidence=round(confidence, 3),
        reasoning=reasoning,
        metrics=metrics_snapshot,
    )
    insert_agent_signal(agent_signal)
    logger.info("[Graham] %s: signal=%s confidence=%.2f criteria=%s/%s",
                ticker, signal, confidence,
                metrics_snapshot["criteria_passed"], metrics_snapshot["criteria_total"])
    return agent_signal
