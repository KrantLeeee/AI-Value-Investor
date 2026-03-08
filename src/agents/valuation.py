"""Valuation Agent — pure Python multi-method valuation, with optional LLM interpretation.

Methods implemented:
  1. DCF (Discounted Cash Flow) — 3 scenarios: bull/base/bear
  2. Graham Number — √(22.5 × EPS × BVPS)
  3. Owner Earnings — Buffett's formula: Net Income + D&A − CapEx
  4. EV/EBITDA — approximate from available data

If OPENAI_API_KEY is set, calls valuation_interpret LLM to narrate the findings.
If not set, returns data-only signal.
"""

import math
from datetime import date

from src.data.database import (
    get_income_statements,
    get_balance_sheets,
    get_cash_flows,
    get_financial_metrics,
    get_latest_prices,
    insert_agent_signal,
)
from src.data.models import AgentSignal
from src.agents.wacc import calculate_wacc, generate_sensitivity_matrix
from src.agents.industry_classifier import get_industry_from_watchlist
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "valuation"

# Default valuation assumptions (conservative value-investor settings)
WACC_DEFAULT     = 0.10   # 10% discount rate
WACC_CYCLE_ADDON = 0.005  # +50bp for highly cyclical sectors (oil services)
TERMINAL_GROWTH  = 0.025  # 2.5% perpetual growth — conservative (was 3%)
FCF_GROWTH_BULL  = 0.12   # 12% — optimistic scenario
FCF_GROWTH_BASE  = 0.07   # 7%  — base scenario
FCF_GROWTH_BEAR  = 0.02   # 2%  — pessimistic scenario
PROJECTION_YEARS = 10
INDUSTRY_EV_EBITDA_OIL = 6.0   # oil services sector multiple (3rd-party benchmark)
INDUSTRY_EV_EBITDA      = 8.0   # generic fallback multiple
# P/B midpoint targets by sector (industry research benchmarks)
PB_TARGET_OIL_SERVICES = 1.8    # oil services: 1.6-2.1x midpoint
PB_TARGET_DEFAULT      = 2.0    # generic fallback
# WACC = 0.10 kept as alias for backward compatibility
WACC = WACC_DEFAULT


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _dcf(base_fcf: float, growth_rate: float, wacc: float = WACC,
          terminal_growth: float = TERMINAL_GROWTH, years: int = PROJECTION_YEARS) -> float:
    """
    10-year DCF with terminal value.
    Returns total present value (NOT per share — divide by shares outstanding separately).
    """
    pv = 0.0
    fcf = base_fcf
    for yr in range(1, years + 1):
        fcf *= (1 + growth_rate)
        pv += fcf / ((1 + wacc) ** yr)
    # Terminal value (Gordon Growth Model)
    terminal_fcf = fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv += terminal_value / ((1 + wacc) ** years)
    return pv


def _get_current_price(ticker: str) -> float | None:
    """Approximate current price from most recent daily_prices row."""
    rows = get_latest_prices(ticker, limit=1)
    if rows:
        return _safe(rows[0].get("close"))
    return None


def _get_shares_outstanding(income_rows: list[dict], metric_rows: list[dict]) -> float | None:
    """Try to get shares outstanding from metrics or income statement."""
    for row in metric_rows:
        mc = _safe(row.get("market_cap"))
        price = _get_current_price(None)  # type: ignore — we'll compute below
        # Skip this path if no market cap
    # Direct from income statement
    for row in income_rows:
        s = _safe(row.get("shares_outstanding"))
        if s and s > 0:
            return s
    return None


def run(ticker: str, market: str, use_llm: bool = True) -> AgentSignal:
    """
    Run the Valuation Agent for a given ticker.
    Returns an AgentSignal and persists it to the database.
    """
    income_rows   = get_income_statements(ticker, limit=5, period_type="annual")
    balance_rows  = get_balance_sheets(ticker, limit=3, period_type="annual")
    cashflow_rows = get_cash_flows(ticker, limit=3, period_type="annual")
    metric_rows   = get_financial_metrics(ticker, limit=3)

    current_price = _get_current_price(ticker)

    # Get industry classification for WACC calculation
    industry = get_industry_from_watchlist(ticker)

    # Calculate industry-adapted WACC (P2-⑦)
    wacc_result = calculate_wacc(ticker, market, industry, current_price)
    wacc = wacc_result["wacc"]
    wacc_fallback = wacc_result.get("fallback_used", False)

    results: dict = {
        "wacc":           wacc * 100,
        "wacc_components": {
            "re": wacc_result.get("re") * 100 if wacc_result.get("re") else None,
            "rd": wacc_result.get("rd") * 100 if wacc_result.get("rd") else None,
            "tc": wacc_result.get("tc") * 100 if wacc_result.get("tc") else None,
            "beta": wacc_result.get("beta"),
            "equity_weight": wacc_result.get("equity_weight") * 100 if wacc_result.get("equity_weight") else None,
            "debt_weight": wacc_result.get("debt_weight") * 100 if wacc_result.get("debt_weight") else None,
        },
        "terminal_growth": TERMINAL_GROWTH * 100,
        "current_price":  current_price,
        "industry": industry,
    }

    # Apply cyclical sector WACC premium
    _is_cyclical = industry and any(k in (industry or "").lower() for k in ["oil", "energy", "mining", "steel"])
    if _is_cyclical:
        wacc = wacc + WACC_CYCLE_ADDON
        logger.info("[Valuation] %s: cyclical sector → WACC +50bp → %.2f%%", ticker, wacc * 100)
        results["wacc"] = wacc * 100
        results["wacc_cycle_premium"] = True
    detail_lines: list[str] = []

    # Add WACC breakdown to detail lines
    if not wacc_fallback:
        detail_lines.append(
            f"WACC: {wacc*100:.2f}% (股权成本={wacc_result.get('re', 0)*100:.2f}%, "
            f"债务成本={wacc_result.get('rd', 0)*100:.2f}%, "
            f"β={wacc_result.get('beta', 0):.2f}, "
            f"E/V={wacc_result.get('equity_weight', 0)*100:.0f}%, "
            f"D/V={wacc_result.get('debt_weight', 0)*100:.0f}%)"
        )
    else:
        detail_lines.append(f"⚠ WACC: {wacc*100:.2f}% (使用行业默认值: {wacc_result.get('note', '')})")

    # ── QVeris supplement: enrich shares + balance sheet ────────────────────
    # AKShare often lacks shares_outstanding and current_assets for A-shares.
    if market == "a_share":
        try:
            from src.data.qveris_source import QVerisSource
            qsrc = QVerisSource()
            if qsrc.health_check():
                # Income supplement (for shares derivation)
                if (not income_rows or not _safe(income_rows[0].get("eps"))):
                    qi = qsrc.get_income_statements(ticker, market, limit=1)
                    if qi and not income_rows:
                        income_rows = [{"revenue": qi[0].revenue, "net_income": qi[0].net_income,
                                        "eps": qi[0].eps}]
                # Balance supplement
                qb = qsrc.get_balance_sheets(ticker, market, limit=1)
                if qb:
                    if not balance_rows:
                        balance_rows = [{}]
                    _b = balance_rows[0]
                    for fld in ["total_equity", "current_assets", "current_liabilities",
                                "total_assets", "total_liabilities", "cash_and_equivalents"]:
                        if not _safe(_b.get(fld)) and getattr(qb[0], fld, None):
                            _b[fld] = getattr(qb[0], fld)
                    logger.info("[Valuation] %s: enriched from QVeris", ticker)
        except Exception as _e:
            logger.warning("[Valuation] QVeris enrichment failed: %s", _e)

    latest_ni = None
    if income_rows:
        latest_ni  = _safe(income_rows[0].get("net_income"))
        shares_raw = _safe(income_rows[0].get("shares_outstanding"))
        eps_raw    = _safe(income_rows[0].get("eps"))
        shares = shares_raw
        # Derive shares from net_income / EPS when not stored explicitly
        if (shares is None or shares == 0) and latest_ni and eps_raw and eps_raw != 0:
            shares = latest_ni / eps_raw
            logger.debug("[Valuation] %s: derived shares=%.0f from NI/EPS", ticker, shares)

    if cashflow_rows:
        ocf  = _safe(cashflow_rows[0].get("operating_cash_flow"))
        fcf  = _safe(cashflow_rows[0].get("free_cash_flow"))
        capex = _safe(cashflow_rows[0].get("capital_expenditure"))
        dep  = _safe(cashflow_rows[0].get("depreciation"))

        # Owner Earnings = Net Income + D&A − CapEx  (Buffett's formula)
        if latest_ni and capex is not None:
            da  = dep or 0
            cx  = abs(capex) if capex < 0 else capex
            owner_earnings = latest_ni + da - cx
            results["owner_earnings"] = owner_earnings
            detail_lines.append(f"Owner Earnings: {owner_earnings/1e8:.2f}亿元")

        # Use FCF for DCF; fall back to OCF if FCF unavailable
        base_fcf = fcf or ocf
        if base_fcf and base_fcf > 0:
            # Use calculated WACC instead of hardcoded value (P2-⑦)
            dcf_bull = _dcf(base_fcf, FCF_GROWTH_BULL, wacc=wacc)
            dcf_base = _dcf(base_fcf, FCF_GROWTH_BASE, wacc=wacc)
            dcf_bear = _dcf(base_fcf, FCF_GROWTH_BEAR, wacc=wacc)
            results.update({
                "base_fcf":     base_fcf,
                "dcf_bull":     dcf_bull,
                "dcf_base":     dcf_base,
                "dcf_bear":     dcf_bear,
                "fcf_growth_bull": FCF_GROWTH_BULL * 100,
                "fcf_growth_base": FCF_GROWTH_BASE * 100,
                "fcf_growth_bear": FCF_GROWTH_BEAR * 100,
            })
            detail_lines.append(f"DCF (乐观/基准/悲观): {dcf_bull/1e8:.0f}亿 / {dcf_base/1e8:.0f}亿 / {dcf_bear/1e8:.0f}亿元")

            # Generate sensitivity matrix (P2-⑦)
            if shares and shares > 0:
                sensitivity = generate_sensitivity_matrix(
                    base_fcf=base_fcf,
                    wacc_current=wacc,
                    shares=shares,
                    wacc_range=(wacc * 0.7, wacc * 1.3),  # ±30% around current WACC
                    growth_range=(0.0, 0.15),
                    terminal_growth=TERMINAL_GROWTH,
                    years=PROJECTION_YEARS,
                )
                results["sensitivity_matrix"] = sensitivity
                logger.debug(f"[Valuation] Generated sensitivity matrix for {ticker}")
        else:
            detail_lines.append("⚠ FCF/OCF 为负或缺失，无法进行 DCF 估值")

    # ── 2. Graham Number ──────────────────────────────────────────────────────
    graham_number_per_share = None
    eps  = _safe(income_rows[0].get("eps")) if income_rows else None
    bvps = _safe(balance_rows[0].get("book_value_per_share")) if balance_rows else None

    # Estimate BVPS from equity / shares if not stored directly
    if bvps is None and balance_rows and shares and shares > 0:
        equity = _safe(balance_rows[0].get("total_equity"))
        if equity:
            bvps = equity / shares

    if bvps:
        results["bvps"] = round(bvps, 2)

    if eps and bvps and eps > 0 and bvps > 0:
        graham_number_per_share = math.sqrt(22.5 * eps * bvps)
        results["graham_number"] = graham_number_per_share
        detail_lines.append(f"Graham Number: ¥{graham_number_per_share:.2f}/股 (EPS={eps:.2f}, BVPS={bvps:.2f})")
    else:
        results["graham_number"] = None
        detail_lines.append("- Graham Number 无法计算（缺 EPS 或 BVPS）")

    # ── 3. EV/EBITDA (per share) ──────────────────────────────────────────────
    ev_ebitda_value = None
    ev_ebitda_per_share = None

    # Try to get EBITDA; estimate from net income if unavailable
    ebitda = None
    if income_rows:
        ebitda = _safe(income_rows[0].get("ebitda"))
        if not ebitda:
            # Estimate: EBITDA ≈ net_income / (1 - tax_rate) + D&A
            # Rough heuristic: EBITDA ≈ net_income × 1.5 for oil services
            ni = _safe(income_rows[0].get("net_income"))
            if ni and ni > 0:
                ebitda = ni * 1.5
                logger.debug("[Valuation] %s: estimated EBITDA=%.0f亿 from NI×1.5", ticker, ebitda / 1e8)

    _is_oil = industry and any(k in (industry or "").lower() for k in ["oil", "energy"])
    _ev_multiple = INDUSTRY_EV_EBITDA_OIL if _is_oil else INDUSTRY_EV_EBITDA

    if ebitda and ebitda > 0:
        ev_ebitda_total = ebitda * _ev_multiple
        results["ev_ebitda_value"] = ev_ebitda_total
        detail_lines.append(f"EV/EBITDA ({_ev_multiple}x): 总企业价值≈{ev_ebitda_total/1e8:.0f}亿元")
        ev_ebitda_value = ev_ebitda_total
        # Per-share estimate
        if shares and shares > 0:
            ev_ebitda_per_share = ev_ebitda_total / shares
            results["ev_ebitda_per_share"] = round(ev_ebitda_per_share, 2)
            detail_lines.append(f"EV/EBITDA 每股隐含价值: ¥{ev_ebitda_per_share:.2f}")

        # P/B per share target
        if bvps:
            _pb = PB_TARGET_OIL_SERVICES if _is_oil else PB_TARGET_DEFAULT
            pb_target_per_share = bvps * _pb
            results["pb_target"] = round(pb_target_per_share, 2)
            detail_lines.append(f"P/B目标价 ({_pb}x BVPS={bvps:.2f}): ¥{pb_target_per_share:.2f}")
    else:
        results["ev_ebitda_value"] = None
        detail_lines.append("- EBITDA 数据缺失（注：已尝试用净利润×1.5估算，仍失败）")

    # ── 4. Net-Net ratio (Graham defensive check) ─────────────────────────────
    net_net_ratio = None
    if balance_rows:
        ca   = _safe(balance_rows[0].get("current_assets"))
        tl   = _safe(balance_rows[0].get("total_liabilities"))
        if ca and tl and shares and shares > 0:
            net_net_per_share = (ca - tl) / shares
            net_net_ratio = net_net_per_share / current_price if current_price else None
            results["net_net_per_share"] = net_net_per_share
            results["net_net_ratio"] = net_net_ratio
            detail_lines.append(f"Net-Net: (CA-TL)/股={net_net_per_share:.2f}, 价格比={net_net_ratio:.2f}" if net_net_ratio else f"Net-Net: {net_net_per_share:.2f}/股")

    # ── 5. Margin of Safety & Signal ─────────────────────────────────────────
    # Use DCF base as primary intrinsic value; fall back to Graham Number
    intrinsic_base = dcf_base
    primary_method = "DCF"
    if intrinsic_base is None and graham_number_per_share and shares:
        intrinsic_base = graham_number_per_share * shares
        primary_method = "Graham Number"

    margin_of_safety = None
    dcf_per_share = None

    if intrinsic_base and shares and shares > 0:
        if primary_method == "DCF":
            dcf_per_share = intrinsic_base / shares
            results["dcf_per_share"] = dcf_per_share
        results["shares_outstanding"] = shares  # expose for Ch7 weighted calc
        if current_price and dcf_per_share:
            margin_of_safety = (dcf_per_share - current_price) / dcf_per_share
            results["margin_of_safety"] = margin_of_safety
            mos_pct = margin_of_safety * 100
            detail_lines.append(f"安全边际 ({primary_method}): {mos_pct:.1f}% (DCF基准¥{dcf_per_share:.2f} vs 市价¥{current_price:.2f})")

    # Determine signal
    if margin_of_safety is not None:
        if margin_of_safety >= 0.30:
            signal, confidence = "bullish", min(0.90, 0.60 + margin_of_safety * 0.5)
        elif margin_of_safety >= 0.10:
            signal, confidence = "neutral", 0.55
        elif margin_of_safety >= -0.10:
            signal, confidence = "neutral", 0.45
        else:
            signal, confidence = "bearish", min(0.90, 0.55 + abs(margin_of_safety) * 0.5)
    elif graham_number_per_share and current_price:
        gn_mos = (graham_number_per_share - current_price) / graham_number_per_share
        if gn_mos >= 0.30:
            signal, confidence = "bullish", 0.65
        elif gn_mos >= 0:
            signal, confidence = "neutral", 0.50
        else:
            signal, confidence = "bearish", 0.55
    else:
        signal, confidence = "neutral", 0.30
        detail_lines.append("⚠ 估值数据不足，保持中性")

    reasoning = (
        f"估值分析结果（{primary_method}为主要依据）：\n"
        + "\n".join(detail_lines)
    )

    # ── 6. Optional LLM interpretation ────────────────────────────────────────
    if use_llm:
        try:
            from src.llm.router import call_llm, LLMError
            from src.llm.prompts import (
                VALUATION_INTERPRET_SYSTEM_PROMPT,
                VALUATION_INTERPRET_USER_TEMPLATE,
            )
            user_msg = VALUATION_INTERPRET_USER_TEMPLATE.format(
                ticker=ticker,
                current_price=f"¥{current_price:.2f}" if current_price else "未知",
                dcf_bull=f"¥{dcf_bull/shares:.2f}/股" if dcf_bull and shares else "N/A",
                dcf_base=f"¥{dcf_base/shares:.2f}/股" if dcf_base and shares else "N/A",
                dcf_bear=f"¥{dcf_bear/shares:.2f}/股" if dcf_bear and shares else "N/A",
                graham_number=f"¥{graham_number_per_share:.2f}" if graham_number_per_share else "N/A",
                owner_earnings_value=f"¥{owner_earnings/1e8:.1f}亿" if owner_earnings else "N/A",
                ev_ebitda_value=f"¥{ev_ebitda_per_share:.2f}/股" if 'ev_ebitda_per_share' in dir() and ev_ebitda_per_share else "N/A",
                wacc=wacc * 100,
                terminal_growth=TERMINAL_GROWTH * 100,
                fcf_growth=FCF_GROWTH_BASE * 100,
            )
            llm_text = call_llm("valuation_interpret", VALUATION_INTERPRET_SYSTEM_PROMPT, user_msg)

            # ── Parse JSON and convert to readable prose (fix raw-JSON output bug) ──
            import json as _json
            try:
                cleaned = llm_text.strip()
                if cleaned.startswith("```"):
                    cleaned = "\n".join(cleaned.split("\n")[1:])
                    cleaned = cleaned.replace("```", "")
                parsed = _json.loads(cleaned)
                llm_sig = parsed.get("signal", "").lower()
                if llm_sig in ("bullish", "neutral", "bearish"):
                    signal = llm_sig
                llm_conf = float(parsed.get("confidence", confidence))
                confidence = (confidence + llm_conf) / 2
                # Render as readable prose instead of raw JSON
                val_pos = parsed.get("valuation_position", "")
                iv_low  = parsed.get("intrinsic_value_range_low", "")
                iv_high = parsed.get("intrinsic_value_range_high", "")
                method  = parsed.get("most_relevant_method", "")
                prose   = parsed.get("reasoning", "")
                reasoning += (
                    f"\n\n**LLM估值解读**:\n"
                    f"最适方法: {method} | 估值立场: {val_pos} | "
                    f"内在价值区间: ¥{iv_low}-¥{iv_high}/股\n"
                    f"{prose}"
                )
            except Exception:
                # LLM returned prose — keep as-is (no raw JSON problem)
                reasoning += f"\n\n**LLM估值解读**:\n{llm_text}"

        except Exception as e:
            logger.warning("[Valuation] LLM call skipped: %s", e)
            reasoning += "\n\n(估值解读 LLM 暂不可用，仅显示代码计算结果)"

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=signal,
        confidence=round(confidence, 3),
        reasoning=reasoning,
        metrics=results,
    )
    insert_agent_signal(agent_signal)
    logger.info("[Valuation] %s: signal=%s confidence=%.2f mos=%s",
                ticker, signal, confidence,
                f"{margin_of_safety*100:.1f}%" if margin_of_safety else "N/A")
    return agent_signal
