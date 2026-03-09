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
import statistics
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
from src.agents.industry_classifier import (
    get_industry_from_watchlist,
    detect_loss_making_tech_stock,
    get_loss_making_tech_valuation_config,
    detect_growth_stock,
    get_growth_tech_valuation_config,
    detect_financial_stock,
    get_financial_stock_valuation_config,
    detect_cyclical_stock,
    get_cyclical_stock_valuation_config,
    detect_healthcare_stock,
    detect_healthcare_rd_stage,
    get_healthcare_rd_valuation_config,
    get_healthcare_mature_valuation_config,
    get_ev_ebitda_multiple,
    classify_industry,
)
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

# BUG-03A: PS (Price-to-Sales) multiples for loss-making tech stocks
# Based on A-share tech sector medians (2023-2025 data)
PS_MULTIPLE_TECH_AI = 8.0       # AI/Voice tech: 6-10x median
PS_MULTIPLE_TECH_SOFTWARE = 6.0 # Software/SaaS: 4-8x median
PS_MULTIPLE_DEFAULT = 4.0       # Generic tech fallback

# EV/Sales multiples (for loss-making tech stocks)
EV_SALES_TECH = 6.0             # Tech sector EV/Sales median

# BUG-03B: PEG (Price/Earnings-to-Growth) parameters for growth stocks
# PEG = PE / EPS Growth Rate
# Fair PEG for A-share growth stocks is typically 1.0-1.5x
# Premium quality growth stocks can justify PEG up to 2.0x
PEG_FAIR_VALUE = 1.2            # A-share quality growth premium
PEG_MAX_REASONABLE = 2.0        # Above this, overvalued even for growth
PEG_BARGAIN = 0.8               # Below this, potentially undervalued

# Phase 2: Financial stock (bank/insurance) valuation parameters
# P/B valuation: Fair PB = ROE / Ke (cost of equity)
# Insurance embedded value typically trades at 0.6-1.2x EV
FINANCIAL_COST_OF_EQUITY = 0.08    # 8% cost of equity assumption
FINANCIAL_DDM_GROWTH = 0.03        # 3% long-term dividend growth
PB_MIN_FINANCIAL = 0.5             # Minimum reasonable P/B for banks
PB_MAX_FINANCIAL = 3.0             # Maximum reasonable P/B

# Phase 2: Cyclical stock valuation parameters
# Use cycle-bottom multiples, not current period
EV_EBITDA_CYCLE_BOTTOM = 5.0       # Cycle trough EV/EBITDA for oil services
EV_EBITDA_CYCLE_NORMAL = 7.0       # Mid-cycle EV/EBITDA
EV_EBITDA_CYCLE_PEAK = 10.0        # Cycle peak EV/EBITDA
PB_CYCLE_BOTTOM = 0.7              # Cycle trough P/B for resources

# Phase 2: Healthcare stock valuation parameters
# R&D stage uses PS (like loss-making tech), mature uses PE
PS_MULTIPLE_HEALTHCARE_RD = 8.0    # R&D stage biotech/pharma PS multiple
PS_MULTIPLE_HEALTHCARE_MATURE = 4.0  # Mature pharma PS multiple
PE_MULTIPLE_HEALTHCARE = 30.0      # Mature healthcare PE multiple (higher than general)
EV_EBITDA_HEALTHCARE = 18.0        # Healthcare EV/EBITDA (higher than general)


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


def _validate_valuation_result(
    method_name: str,
    target_price: float,
    current_price: float,
    all_results: list[float]
) -> dict:
    """
    Validate a valuation result against outlier detection rules.

    BUG-02 FIX: Changed from market price baseline to method median baseline.
    Old rules incorrectly excluded valid valuations for undervalued/overvalued stocks.

    Rules (any violation triggers exclusion):
    1. Negative or zero target price → exclude
    2. Deviation from method median > 60% → exclude (BUG-02 fix: uses median, not market price)
    3. EXCEPTION: If all methods agree on direction (all above or all below market),
       skip rule 2 (don't exclude consistent signals)

    Args:
        method_name: Name of the valuation method (e.g., "DCF", "Graham")
        target_price: Target price from this method
        current_price: Current market price
        all_results: List of all target prices (for median calculation)

    Returns:
        dict with:
            - method: str
            - target_price: float
            - valid: bool (True if passes all rules)
            - warnings: list[str] (reasons for exclusion)
            - exclude_from_weighted: bool (True if should be excluded)
    """
    warnings = []
    valid = True

    # Rule 1: Negative or zero target price
    if target_price <= 0:
        warnings.append(f"{method_name}: negative or zero target price (¥{target_price:.2f})")
        valid = False

    # BUG-02 FIX: Rule 2 now uses method median as baseline, not market price
    # Also check for directional consensus before excluding
    if all_results and len(all_results) > 0:
        # Filter out invalid values for median calculation
        valid_prices = [p for p in all_results if p is not None and p > 0]

        if len(valid_prices) >= 2:
            # Check for directional consensus (all above or all below market price)
            # If all methods agree on direction, don't exclude any of them
            all_above_market = all(p > current_price for p in valid_prices) if current_price > 0 else False
            all_below_market = all(p < current_price for p in valid_prices) if current_price > 0 else False
            directional_consensus = all_above_market or all_below_market

            median_price = statistics.median(valid_prices)
            deviation_from_median = abs(target_price - median_price) / median_price if median_price > 0 else 0

            # BUG-02 FIX: Use 60% threshold (was 50%), and skip if directional consensus
            if deviation_from_median > 0.60 and not directional_consensus:
                warnings.append(
                    f"{method_name}: deviation from median "
                    f"{deviation_from_median*100:.1f}% exceeds 60% threshold "
                    f"(target=¥{target_price:.2f} vs median=¥{median_price:.2f})"
                )
                valid = False
            elif deviation_from_median > 0.60 and directional_consensus:
                # Log but don't exclude - all methods agree on direction
                warnings.append(
                    f"{method_name}: deviation {deviation_from_median*100:.1f}% but retained "
                    f"(all methods {'above' if all_above_market else 'below'} market price)"
                )
                # valid remains True

    return {
        "method": method_name,
        "target_price": target_price,
        "valid": valid,
        "warnings": warnings,
        "exclude_from_weighted": not valid,
    }


def _calculate_weighted_target(
    results: list[dict],
    current_price: float,
    weights: dict[str, float] | None = None
) -> dict:
    """
    Calculate weighted target price from validated results.

    Args:
        results: List of validation results from _validate_valuation_result
        current_price: Current market price
        weights: Optional dict of method weights (defaults to equal weights)

    Returns:
        dict with:
            - weighted_target: float | None
            - valid_methods: list[str]
            - excluded_methods: list[str]
            - degraded: bool (True if <=1 valid method)
            - confidence: float (0.25 if degraded, None otherwise)
            - warning: str (if degraded mode)
    """
    # Filter valid methods
    valid_results = [r for r in results if not r.get("exclude_from_weighted", True)]
    excluded_results = [r for r in results if r.get("exclude_from_weighted", True)]

    valid_methods = [r["method"] for r in valid_results]
    excluded_methods = [r["method"] for r in excluded_results]

    # Check for degraded mode (<=1 valid method)
    if len(valid_results) <= 1:
        if len(valid_results) == 1:
            target = valid_results[0]["target_price"]
            warning = (
                f"⚠ Degraded mode: only 1 valid method ({valid_results[0]['method']}) "
                f"remaining after outlier filtering. "
                f"Excluded: {', '.join(excluded_methods)}"
            )
        else:
            target = None
            warning = (
                f"⚠ Degraded mode: 0 valid methods remaining after outlier filtering. "
                f"All methods excluded: {', '.join(excluded_methods)}"
            )

        return {
            "weighted_target": target,
            "valid_methods": valid_methods,
            "excluded_methods": excluded_methods,
            "degraded": True,
            "confidence": 0.25,
            "warning": warning,
        }

    # Normal mode: calculate weighted average
    # Default to equal weights if not provided
    if weights is None:
        weights = {r["method"]: 1.0 / len(valid_results) for r in valid_results}

    # Normalize weights for valid methods only
    valid_weights = {m: weights.get(m, 0) for m in valid_methods}
    total_weight = sum(valid_weights.values())

    if total_weight == 0:
        # Fallback to equal weights
        valid_weights = {m: 1.0 / len(valid_methods) for m in valid_methods}
        total_weight = 1.0

    normalized_weights = {m: w / total_weight for m, w in valid_weights.items()}

    # ── DEBUG: Log weighted calculation inputs ────────────────────────────────
    logger.info("[Weighted Calc] Valid methods entering weighted calculation:")
    for r in valid_results:
        method_name = r["method"]
        price = r["target_price"]
        orig_weight = weights.get(method_name, 0)
        norm_weight = normalized_weights.get(method_name, 0)
        logger.info(
            f"  [{method_name}] price=¥{price:.2f}, "
            f"original_weight={orig_weight:.4f}, "
            f"normalized_weight={norm_weight:.4f}"
        )

    # Calculate weighted average
    weighted_target = sum(
        r["target_price"] * normalized_weights.get(r["method"], 0)
        for r in valid_results
    )

    # ── DEBUG: Log weighted calculation output ────────────────────────────────
    manual_calc = sum(
        r["target_price"] * normalized_weights.get(r["method"], 0)
        for r in valid_results
    )
    logger.info(f"[Weighted Calc] Final weighted price: ¥{weighted_target:.2f}")
    logger.info(f"[Weighted Calc] Manual verification: ¥{manual_calc:.2f}")
    logger.info(
        f"[Weighted Calc] Detailed calculation: "
        + " + ".join([
            f"¥{r['target_price']:.2f}×{normalized_weights.get(r['method'], 0):.4f}"
            for r in valid_results
        ])
    )

    return {
        "weighted_target": weighted_target,
        "valid_methods": valid_methods,
        "excluded_methods": excluded_methods,
        "degraded": False,
    }


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
    # Try watchlist first, then fallback to company info
    industry = get_industry_from_watchlist(ticker)

    # BUG-03A: Use company info fallback for industry detection if watchlist doesn't have it
    if industry == "default":
        try:
            from src.data.fetcher import _COMPANY_INFO_FALLBACK
            fallback_info = _COMPANY_INFO_FALLBACK.get(ticker, {})
            if fallback_info.get("industry"):
                industry = fallback_info["industry"]
                logger.info(f"[Valuation] {ticker}: Using fallback industry: {industry}")
        except ImportError:
            pass

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

    # Initialize all valuation variables to None for defensive coding
    # These will be set conditionally based on data availability
    dcf_bull = None
    dcf_base = None
    dcf_bear = None
    shares = None
    graham_number = None
    graham_number_per_share = None
    ev_ebitda_per_share = None
    pb_target = None

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

    owner_earnings = None
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

    # ── BUG-03A: Detect loss-making tech stocks ───────────────────────────────
    # These need PS/EV-Sales valuation instead of Graham Number/EV-EBITDA
    is_loss_making_tech = False
    net_margin = None
    revenue_growth = None

    if income_rows and len(income_rows) >= 1:
        revenue = _safe(income_rows[0].get("revenue"))
        net_income = _safe(income_rows[0].get("net_income"))

        if revenue and revenue > 0 and net_income is not None:
            net_margin = net_income / revenue

        # Calculate revenue growth if we have historical data
        if len(income_rows) >= 2:
            revenue_prev = _safe(income_rows[1].get("revenue"))
            if revenue and revenue_prev and revenue_prev > 0:
                revenue_growth = (revenue - revenue_prev) / revenue_prev

    # R&D ratio (optional, may not be available)
    rd_ratio = None
    if metric_rows:
        rd_ratio = _safe(metric_rows[0].get("rd_expense_ratio"))

    # Detect loss-making tech
    is_loss_making_tech = detect_loss_making_tech_stock(
        net_income=latest_ni,
        net_margin=net_margin,
        revenue_growth=revenue_growth,
        rd_ratio=rd_ratio,
        industry=industry,
    )

    if is_loss_making_tech:
        results["is_loss_making_tech"] = True
        results["valuation_mode"] = "loss_making_tech"
        detail_lines.append("⚠ 亏损期科技股：使用PS/EV-Sales估值方法，禁用Graham Number")

    # ── BUG-03B: Detect profitable growth stocks ─────────────────────────────
    # These need PEG valuation instead of Graham Number
    is_growth_stock = False
    revenue_cagr_3y = None
    pe_ratio = None
    eps_growth = None

    # Calculate 3-year revenue CAGR if we have enough historical data
    if income_rows and len(income_rows) >= 3:
        revenue_current = _safe(income_rows[0].get("revenue"))
        revenue_3y_ago = _safe(income_rows[2].get("revenue"))  # 3 years ago
        if revenue_current and revenue_3y_ago and revenue_3y_ago > 0:
            # CAGR = (End/Start)^(1/n) - 1
            revenue_cagr_3y = (revenue_current / revenue_3y_ago) ** (1/3) - 1
            results["revenue_cagr_3y"] = round(revenue_cagr_3y * 100, 2)

    # Get PE ratio from metrics or calculate from price/EPS
    if metric_rows:
        pe_ratio = _safe(metric_rows[0].get("pe_ratio"))

    # Fallback: calculate PE from current price and EPS
    eps_for_pe = _safe(income_rows[0].get("eps")) if income_rows else None
    if pe_ratio is None and current_price and eps_for_pe and eps_for_pe > 0:
        pe_ratio = current_price / eps_for_pe
        logger.debug(f"[Valuation] {ticker}: calculated PE={pe_ratio:.2f} from price/EPS")

    # Calculate EPS growth for PEG calculation
    if income_rows and len(income_rows) >= 2:
        eps_current = _safe(income_rows[0].get("eps"))
        eps_prev = _safe(income_rows[1].get("eps"))
        if eps_current and eps_prev and eps_prev > 0:
            eps_growth = (eps_current - eps_prev) / abs(eps_prev)
            results["eps_growth"] = round(eps_growth * 100, 2)

    # Detect growth stock (only if NOT already classified as loss-making tech)
    if not is_loss_making_tech:
        is_growth_stock = detect_growth_stock(
            pe_ratio=pe_ratio,
            revenue_cagr_3y=revenue_cagr_3y,
            net_income=latest_ni,
            eps=eps_for_pe,
            industry=industry,
        )

    if is_growth_stock:
        results["is_growth_stock"] = True
        results["valuation_mode"] = "growth_stock"
        results["pe_ratio"] = round(pe_ratio, 2) if pe_ratio else None
        detail_lines.append(
            f"📈 盈利成长股：使用PEG/DCF估值方法，禁用Graham Number "
            f"(PE={pe_ratio:.1f}x, CAGR={revenue_cagr_3y*100:.1f}%)"
        )

    # ── Phase 2: Detect financial stocks (banks/insurance) ─────────────────────
    is_financial_stock = False
    roe = None
    dividend_yield = None

    if metric_rows:
        roe = _safe(metric_rows[0].get("roe"))
        dividend_yield = _safe(metric_rows[0].get("dividend_yield"))

    # Only check financial if not already classified
    if not is_loss_making_tech and not is_growth_stock:
        is_financial_stock = detect_financial_stock(
            industry=industry,
            roe=roe,
            dividend_yield=dividend_yield,
        )

    if is_financial_stock:
        results["is_financial_stock"] = True
        results["valuation_mode"] = "financial"
        results["roe"] = round(roe * 100, 2) if roe else None
        results["dividend_yield"] = round(dividend_yield * 100, 2) if dividend_yield else None
        detail_lines.append(
            f"🏦 金融股：使用P/B-ROE模型+DDM估值方法，禁用EV/EBITDA "
            f"(ROE={roe*100:.1f}%)" if roe else "🏦 金融股：使用P/B-ROE模型+DDM估值方法"
        )

    # ── Phase 2: Detect cyclical stocks (resources/commodities) ────────────────
    is_cyclical_stock = False

    # Only check cyclical if not already classified
    if not is_loss_making_tech and not is_growth_stock and not is_financial_stock:
        is_cyclical_stock = detect_cyclical_stock(
            industry=industry,
        )

    if is_cyclical_stock:
        results["is_cyclical_stock"] = True
        results["valuation_mode"] = "cyclical"
        detail_lines.append(
            "🔄 周期股：使用正常化DCF+周期底部EV/EBITDA估值方法，"
            "禁用成长性DCF（避免高估周期顶部）"
        )

    # ── Phase 2: Detect healthcare stocks ──────────────────────────────────────
    # Healthcare stocks need different valuation methods based on development stage:
    # - R&D stage (loss-making/low profit): PS/EV-Sales (like loss-making tech)
    # - Mature stage (profitable): PE/DCF/EV-EBITDA
    is_healthcare_stock = False
    is_healthcare_rd = False

    if not is_loss_making_tech and not is_growth_stock and not is_financial_stock and not is_cyclical_stock:
        is_healthcare_stock = detect_healthcare_stock(industry=industry)

    if is_healthcare_stock:
        # Determine development stage
        is_healthcare_rd = detect_healthcare_rd_stage(
            net_income=net_income,
            net_margin=net_margin,
            rd_ratio=None,  # TODO: Get R&D ratio from income statement if available
            revenue_growth=revenue_yoy,
        )

        results["is_healthcare_stock"] = True
        if is_healthcare_rd:
            results["valuation_mode"] = "healthcare_rd"
            results["healthcare_stage"] = "R&D"
            detail_lines.append(
                "💊 研发期医药股：使用PS/EV-Sales估值方法（管线价值难以用盈利反映），"
                "禁用PE类方法（亏损期PE无意义）"
            )
        else:
            results["valuation_mode"] = "healthcare_mature"
            results["healthcare_stage"] = "mature"
            net_margin_pct = net_margin * 100 if net_margin else None
            detail_lines.append(
                f"💊 成熟期医药股：使用PE/DCF估值方法（盈利稳定可比较）"
                + (f" (净利率={net_margin_pct:.1f}%)" if net_margin_pct else "")
            )

    if cashflow_rows:
        # Use FCF for DCF; fall back to OCF if FCF is negative or unavailable
        # Note: `fcf or ocf` fails when fcf is negative (truthy), so explicit check needed
        if fcf is not None and fcf > 0:
            base_fcf = fcf
            fcf_source = "FCF"
        elif ocf is not None and ocf > 0:
            base_fcf = ocf
            fcf_source = "OCF"
        else:
            base_fcf = None
            fcf_source = None

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
            detail_lines.append(f"DCF基于{fcf_source} (乐观/基准/悲观): {dcf_bull/1e8:.0f}亿 / {dcf_base/1e8:.0f}亿 / {dcf_bear/1e8:.0f}亿元")

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
            fcf_str = f"FCF={fcf/1e8:.1f}亿" if fcf is not None else "FCF缺失"
            ocf_str = f"OCF={ocf/1e8:.1f}亿" if ocf is not None else "OCF缺失"
            detail_lines.append(f"⚠ {fcf_str}, {ocf_str} — 均为负或缺失，无法进行 DCF 估值")

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

    # Phase 3: Use industry-specific EV/EBITDA multiple from industry_profiles.yaml
    industry_class = classify_industry(industry) if industry else "default"
    _ev_multiple = get_ev_ebitda_multiple(industry_class, cycle_phase="normal")

    if ebitda and ebitda > 0:
        ev_ebitda_total = ebitda * _ev_multiple
        results["ev_ebitda_value"] = ev_ebitda_total
        results["ev_ebitda_multiple"] = _ev_multiple  # Store for reporting
        detail_lines.append(f"EV/EBITDA ({_ev_multiple:.1f}x行业倍数): 总企业价值≈{ev_ebitda_total/1e8:.0f}亿元")
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

    # ── 3b. PS (Price-to-Sales) valuation (BUG-03A: for loss-making tech stocks) ──
    ps_per_share = None
    revenue = _safe(income_rows[0].get("revenue")) if income_rows else None

    if revenue and revenue > 0 and shares and shares > 0:
        # Determine PS multiple based on industry
        _is_ai = industry and any(k in (industry or "").lower() for k in ["ai", "人工智能", "语音"])
        _is_software = industry and any(k in (industry or "").lower() for k in ["软件", "software", "saas"])

        if _is_ai:
            ps_multiple = PS_MULTIPLE_TECH_AI
        elif _is_software:
            ps_multiple = PS_MULTIPLE_TECH_SOFTWARE
        else:
            ps_multiple = PS_MULTIPLE_DEFAULT

        ps_value = revenue * ps_multiple
        ps_per_share = ps_value / shares
        results["ps_per_share"] = round(ps_per_share, 2)
        results["ps_multiple"] = ps_multiple
        detail_lines.append(f"PS估值 ({ps_multiple}x 营收): ¥{ps_per_share:.2f}/股")

    # ── 3c. EV/Sales valuation (BUG-03A: for loss-making tech stocks) ─────────
    ev_sales_per_share = None

    if revenue and revenue > 0 and shares and shares > 0:
        # Calculate Enterprise Value: Market Cap + Debt - Cash
        total_debt = _safe(balance_rows[0].get("total_debt")) if balance_rows else None
        cash = _safe(balance_rows[0].get("cash_and_equivalents")) if balance_rows else None

        # Estimate market cap from current price
        market_cap = current_price * shares if current_price else None

        if market_cap:
            ev = market_cap + (total_debt or 0) - (cash or 0)
            implied_ev = revenue * EV_SALES_TECH
            ev_sales_per_share = implied_ev / shares
            results["ev_sales_per_share"] = round(ev_sales_per_share, 2)
            results["ev_sales_multiple"] = EV_SALES_TECH
            detail_lines.append(f"EV/Sales估值 ({EV_SALES_TECH}x 营收): ¥{ev_sales_per_share:.2f}/股")

    # ── 3d. PEG valuation (BUG-03B: for growth stocks) ───────────────────────
    # PEG = PE / EPS Growth Rate
    # Fair value = Fair PEG × EPS Growth Rate × EPS
    peg_per_share = None
    peg_ratio = None

    if eps_growth and eps_growth > 0.10 and eps_for_pe and eps_for_pe > 0:
        # Calculate current PEG ratio
        eps_growth_pct = eps_growth * 100  # Convert to percentage for PEG calculation
        if pe_ratio and pe_ratio > 0:
            peg_ratio = pe_ratio / eps_growth_pct
            results["peg_ratio"] = round(peg_ratio, 2)

        # Calculate fair value using PEG method
        # Fair PE = Fair PEG × EPS Growth Rate (in %)
        # E.g., if EPS growth = 25% and Fair PEG = 1.2, then Fair PE = 30x
        fair_pe = PEG_FAIR_VALUE * eps_growth_pct
        peg_per_share = fair_pe * eps_for_pe
        results["peg_per_share"] = round(peg_per_share, 2)
        results["peg_fair_pe"] = round(fair_pe, 1)

        # Add detail line
        peg_status = ""
        if peg_ratio:
            if peg_ratio < PEG_BARGAIN:
                peg_status = "低估"
            elif peg_ratio > PEG_MAX_REASONABLE:
                peg_status = "高估"
            else:
                peg_status = "合理"

        detail_lines.append(
            f"PEG估值 (EPS增速{eps_growth_pct:.1f}%, 合理PEG={PEG_FAIR_VALUE}): "
            f"¥{peg_per_share:.2f}/股 (当前PEG={peg_ratio:.2f}x {peg_status})"
        )
    elif is_growth_stock:
        # Growth stock but missing EPS growth data
        detail_lines.append("⚠ PEG估值无法计算（缺少EPS增速数据或EPS为负）")

    # ── 3e. P/B-ROE valuation (Phase 2: for financial stocks) ────────────────
    # Fair P/B = ROE / Cost of Equity (Ke)
    # For banks/insurance, P/B is the primary valuation method
    pb_roe_per_share = None

    if is_financial_stock and bvps and bvps > 0:
        if roe and roe > 0:
            # Fair P/B = ROE / Ke (cost of equity)
            # Ke is typically 8-10% for financial stocks
            fair_pb = roe / FINANCIAL_COST_OF_EQUITY
            # Cap fair P/B within reasonable range
            fair_pb = max(PB_MIN_FINANCIAL, min(fair_pb, PB_MAX_FINANCIAL))
            pb_roe_per_share = bvps * fair_pb
            results["pb_roe_per_share"] = round(pb_roe_per_share, 2)
            results["fair_pb_roe"] = round(fair_pb, 2)
            detail_lines.append(
                f"P/B-ROE估值 (ROE={roe*100:.1f}%, Ke={FINANCIAL_COST_OF_EQUITY*100:.0f}%): "
                f"合理PB={fair_pb:.2f}x → ¥{pb_roe_per_share:.2f}/股"
            )
        else:
            detail_lines.append("⚠ P/B-ROE估值无法计算（缺少ROE数据）")

    # ── 3f. DDM valuation (Phase 2: for financial stocks) ────────────────────
    # DDM = D1 / (Ke - g), where D1 is next year's dividend
    ddm_per_share = None

    if is_financial_stock:
        # Try to get dividend per share
        dps = _safe(metric_rows[0].get("dividend_per_share")) if metric_rows else None

        # Fallback: estimate DPS from dividend yield and current price
        if dps is None and dividend_yield and current_price:
            dps = dividend_yield * current_price

        if dps and dps > 0:
            # DDM formula: P = D1 / (Ke - g)
            # D1 = DPS × (1 + g)
            d1 = dps * (1 + FINANCIAL_DDM_GROWTH)
            if FINANCIAL_COST_OF_EQUITY > FINANCIAL_DDM_GROWTH:
                ddm_per_share = d1 / (FINANCIAL_COST_OF_EQUITY - FINANCIAL_DDM_GROWTH)
                results["ddm_per_share"] = round(ddm_per_share, 2)
                results["dps"] = round(dps, 2)
                detail_lines.append(
                    f"DDM股息折现 (DPS=¥{dps:.2f}, g={FINANCIAL_DDM_GROWTH*100:.0f}%, "
                    f"Ke={FINANCIAL_COST_OF_EQUITY*100:.0f}%): ¥{ddm_per_share:.2f}/股"
                )
        else:
            detail_lines.append("⚠ DDM估值无法计算（缺少股息数据）")

    # ── 3g. Cycle-adjusted EV/EBITDA (Phase 2: for cyclical stocks) ──────────
    # Use cycle-bottom multiples instead of current period from industry_profiles.yaml
    ev_ebitda_cycle_per_share = None

    if is_cyclical_stock and ebitda and ebitda > 0 and shares and shares > 0:
        # Use industry-specific cycle multiples
        _ev_cycle_bottom = get_ev_ebitda_multiple(industry_class, cycle_phase="bottom")
        _ev_cycle_normal = get_ev_ebitda_multiple(industry_class, cycle_phase="normal")
        _ev_cycle_peak = get_ev_ebitda_multiple(industry_class, cycle_phase="peak")

        ev_ebitda_cycle_total = ebitda * _ev_cycle_bottom
        ev_ebitda_cycle_per_share = ev_ebitda_cycle_total / shares
        results["ev_ebitda_cycle_per_share"] = round(ev_ebitda_cycle_per_share, 2)
        results["ev_ebitda_cycle_multiple"] = _ev_cycle_bottom
        detail_lines.append(
            f"周期底部EV/EBITDA ({_ev_cycle_bottom:.1f}x): "
            f"¥{ev_ebitda_cycle_per_share:.2f}/股 (vs 正常{_ev_cycle_normal:.1f}x, 顶部{_ev_cycle_peak:.1f}x)"
        )

    # ── 3h. Cycle-bottom P/B (Phase 2: for cyclical stocks) ──────────────────
    pb_cycle_per_share = None

    if is_cyclical_stock and bvps and bvps > 0:
        pb_cycle_per_share = bvps * PB_CYCLE_BOTTOM
        results["pb_cycle_per_share"] = round(pb_cycle_per_share, 2)
        results["pb_cycle_multiple"] = PB_CYCLE_BOTTOM
        detail_lines.append(
            f"周期底部P/B ({PB_CYCLE_BOTTOM}x BVPS=¥{bvps:.2f}): ¥{pb_cycle_per_share:.2f}/股"
        )

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

    # ── 5. Outlier Detection & Weighted Target Price ──────────────────────────
    # Collect all per-share target prices for validation
    valuation_methods = []
    dcf_per_share = None
    pb_target_per_share = None

    # BUG-03A: For loss-making tech stocks, use PS/EV-Sales as primary methods
    if is_loss_making_tech:
        # PS valuation (primary for loss-making tech)
        if ps_per_share:
            valuation_methods.append(("PS", ps_per_share))

        # EV/Sales valuation (secondary for loss-making tech)
        if ev_sales_per_share:
            valuation_methods.append(("EV/Sales", ev_sales_per_share))

        # DCF base case (per share) - still useful with turnaround assumptions
        if dcf_base and shares and shares > 0:
            dcf_per_share = dcf_base / shares
            results["dcf_per_share"] = dcf_per_share
            valuation_methods.append(("DCF", dcf_per_share))

        # P/B target per share - floor value only
        if bvps:
            _pb = PB_TARGET_OIL_SERVICES if _is_oil else PB_TARGET_DEFAULT
            pb_target_per_share = bvps * _pb
            if "pb_target" not in results:
                results["pb_target"] = round(pb_target_per_share, 2)
            valuation_methods.append(("P/B", pb_target_per_share))

        # NOTE: Graham Number and EV/EBITDA are DISABLED for loss-making tech
        # (Graham requires positive EPS, EV/EBITDA requires positive EBITDA)

    elif is_growth_stock:
        # BUG-03B: Growth stock valuation methods
        # Uses PEG instead of Graham Number, disables Graham Number

        # DCF base case (per share) - primary method with growth assumptions
        if dcf_base and shares and shares > 0:
            dcf_per_share = dcf_base / shares
            results["dcf_per_share"] = dcf_per_share
            valuation_methods.append(("DCF", dcf_per_share))

        # PEG valuation - core method for growth stocks
        if peg_per_share:
            valuation_methods.append(("PEG", peg_per_share))

        # EV/Sales valuation - industry comparison
        if ev_sales_per_share:
            valuation_methods.append(("EV/Sales", ev_sales_per_share))

        # P/B target per share - growth ROE adjusted
        if bvps:
            _pb = PB_TARGET_OIL_SERVICES if _is_oil else PB_TARGET_DEFAULT
            pb_target_per_share = bvps * _pb
            if "pb_target" not in results:
                results["pb_target"] = round(pb_target_per_share, 2)
            valuation_methods.append(("P/B", pb_target_per_share))

        # NOTE: Graham Number is DISABLED for growth stocks
        # (Graham Number is designed for defensive undervalued stocks, not growth)

    elif is_financial_stock:
        # Phase 2: Financial stock valuation methods
        # Uses P/B-ROE and DDM, disables EV/EBITDA and standard DCF

        # P/B-ROE valuation - primary method for financial stocks
        if pb_roe_per_share:
            valuation_methods.append(("P/B_ROE", pb_roe_per_share))

        # DDM valuation - dividend-based for stable dividend payers
        if ddm_per_share:
            valuation_methods.append(("DDM", ddm_per_share))

        # P/E using operational profit (if available)
        if pe_ratio and pe_ratio > 0 and eps_for_pe and eps_for_pe > 0:
            # Use a reasonable PE multiple for financial stocks (typically 8-12x)
            fair_pe_financial = 10.0
            pe_target = fair_pe_financial * eps_for_pe
            results["pe_financial_per_share"] = round(pe_target, 2)
            valuation_methods.append(("P/E", pe_target))

        # NOTE: EV/EBITDA and standard DCF are DISABLED for financial stocks
        # (Financial company "debt" is the business itself, FCF definition differs)

    elif is_cyclical_stock:
        # Phase 2: Cyclical stock valuation methods
        # Uses normalized DCF and cycle-bottom multiples

        # DCF base case (per share) - use as normalized DCF
        if dcf_base and shares and shares > 0:
            dcf_per_share = dcf_base / shares
            results["dcf_per_share"] = dcf_per_share
            valuation_methods.append(("DCF_Normalized", dcf_per_share))

        # Cycle-adjusted EV/EBITDA
        if ev_ebitda_cycle_per_share:
            valuation_methods.append(("EV/EBITDA_Cycle", ev_ebitda_cycle_per_share))

        # Cycle-bottom P/B
        if pb_cycle_per_share:
            valuation_methods.append(("P/B_Cycle", pb_cycle_per_share))

        # NOTE: Growth-oriented DCF is DISABLED for cyclical stocks
        # (Would overestimate value at cycle peak)

    elif is_healthcare_stock:
        # Phase 2: Healthcare stock valuation methods
        # R&D stage uses PS/EV-Sales (like loss-making tech)
        # Mature stage uses PE/DCF/EV-EBITDA

        if is_healthcare_rd:
            # R&D stage healthcare - similar to loss-making tech
            # PS valuation - primary for R&D stage
            if ps_per_share:
                valuation_methods.append(("PS", ps_per_share))

            # EV/Sales valuation
            if ev_sales_per_share:
                valuation_methods.append(("EV/Sales", ev_sales_per_share))

            # DCF with pipeline adjustments (use base case as proxy)
            if dcf_base and shares and shares > 0:
                dcf_per_share = dcf_base / shares
                results["dcf_per_share"] = dcf_per_share
                valuation_methods.append(("Pipeline_DCF", dcf_per_share))

            # P/B as floor value
            if bvps:
                pb_target_per_share = bvps * 1.5  # Conservative multiple for R&D stage
                valuation_methods.append(("P/B", pb_target_per_share))

            # NOTE: PE methods are DISABLED for R&D stage (unprofitable)
        else:
            # Mature healthcare - PE/DCF based
            # PE valuation - primary for mature healthcare
            if eps and eps > 0 and current_price and current_price > 0:
                pe_ratio = current_price / eps
                # Use healthcare-specific PE multiple
                pe_target = eps * PE_MULTIPLE_HEALTHCARE
                results["pe_target"] = round(pe_target, 2)
                results["pe_ratio"] = round(pe_ratio, 2)
                valuation_methods.append(("P/E", pe_target))

            # DCF base case (per share)
            if dcf_base and shares and shares > 0:
                dcf_per_share = dcf_base / shares
                results["dcf_per_share"] = dcf_per_share
                valuation_methods.append(("DCF", dcf_per_share))

            # EV/EBITDA with healthcare multiple
            if ebitda and ebitda > 0 and shares and shares > 0:
                healthcare_ev = ebitda * EV_EBITDA_HEALTHCARE
                healthcare_ev_per_share = healthcare_ev / shares
                results["ev_ebitda_healthcare_per_share"] = round(healthcare_ev_per_share, 2)
                valuation_methods.append(("EV/EBITDA", healthcare_ev_per_share))

            # PS as secondary
            if ps_per_share:
                valuation_methods.append(("PS", ps_per_share))

    else:
        # Standard valuation methods for traditional value stocks
        # DCF base case (per share)
        if dcf_base and shares and shares > 0:
            dcf_per_share = dcf_base / shares
            results["dcf_per_share"] = dcf_per_share
            valuation_methods.append(("DCF", dcf_per_share))

        # Graham Number - only for traditional value stocks
        if graham_number_per_share:
            valuation_methods.append(("Graham", graham_number_per_share))

        # EV/EBITDA per share - only for profitable companies
        if ev_ebitda_per_share:
            valuation_methods.append(("EV/EBITDA", ev_ebitda_per_share))

        # P/B target per share
        if bvps:
            _pb = PB_TARGET_OIL_SERVICES if _is_oil else PB_TARGET_DEFAULT
            pb_target_per_share = bvps * _pb
            # Only add to results if not already added in EV/EBITDA section
            if "pb_target" not in results:
                results["pb_target"] = round(pb_target_per_share, 2)
            valuation_methods.append(("P/B", pb_target_per_share))

    # Validate each method and calculate weighted target
    validated_results = []
    all_target_prices = [price for _, price in valuation_methods if price and price > 0]

    for method_name, target_price in valuation_methods:
        validation = _validate_valuation_result(
            method_name=method_name,
            target_price=target_price,
            current_price=current_price or 0,
            all_results=all_target_prices
        )
        validated_results.append(validation)

        # Log warnings
        for warning in validation["warnings"]:
            logger.warning("[Valuation] %s: %s", ticker, warning)
            detail_lines.append(f"⚠ {warning}")

    # Select appropriate valuation weights based on stock type
    if is_loss_making_tech:
        # BUG-03A: Use loss-making tech weights
        valuation_config = get_loss_making_tech_valuation_config()
        default_weights = valuation_config["weights"]
        logger.info("[Valuation] %s: Using loss-making tech weights: %s", ticker, default_weights)
    elif is_growth_stock:
        # BUG-03B: Use growth stock weights (PEG-focused)
        valuation_config = get_growth_tech_valuation_config()
        default_weights = valuation_config["weights"]
        logger.info("[Valuation] %s: Using growth stock weights: %s", ticker, default_weights)
    elif is_financial_stock:
        # Phase 2: Use financial stock weights (P/B-ROE + DDM focused)
        valuation_config = get_financial_stock_valuation_config()
        default_weights = valuation_config["weights"]
        logger.info("[Valuation] %s: Using financial stock weights: %s", ticker, default_weights)
    elif is_cyclical_stock:
        # Phase 2: Use cyclical stock weights (normalized DCF + cycle-bottom multiples)
        valuation_config = get_cyclical_stock_valuation_config()
        default_weights = valuation_config["weights"]
        logger.info("[Valuation] %s: Using cyclical stock weights: %s", ticker, default_weights)
    elif is_healthcare_stock:
        # Phase 2: Use healthcare stock weights based on development stage
        if is_healthcare_rd:
            valuation_config = get_healthcare_rd_valuation_config()
            default_weights = valuation_config["weights"]
            logger.info("[Valuation] %s: Using healthcare R&D stage weights: %s", ticker, default_weights)
        else:
            valuation_config = get_healthcare_mature_valuation_config()
            default_weights = valuation_config["weights"]
            logger.info("[Valuation] %s: Using healthcare mature stage weights: %s", ticker, default_weights)
    else:
        # Standard valuation weights for traditional value stocks
        # Prefer DCF > Graham > EV/EBITDA > P/B (following value investing principles)
        default_weights = {
            "DCF": 0.40,
            "Graham": 0.25,
            "EV/EBITDA": 0.20,
            "P/B": 0.15,
        }

    weighted_result = _calculate_weighted_target(
        results=validated_results,
        current_price=current_price or 0,
        weights=default_weights
    )

    # Store validation results in metrics
    results["validation"] = {
        "validated_methods": [
            {
                "method": v["method"],
                "target_price": v["target_price"],
                "valid": v["valid"],
                "excluded": v["exclude_from_weighted"]
            }
            for v in validated_results
        ],
        "weighted_target": weighted_result["weighted_target"],
        "valid_methods": weighted_result["valid_methods"],
        "excluded_methods": weighted_result["excluded_methods"],
        "degraded": weighted_result["degraded"],
    }
    results["shares_outstanding"] = shares  # expose for Ch7 weighted calc

    # Use weighted target for margin of safety calculation
    weighted_target = weighted_result["weighted_target"]
    margin_of_safety = None
    primary_method = "Weighted Average"

    if weighted_target and current_price and current_price > 0:
        margin_of_safety = (weighted_target - current_price) / weighted_target
        results["margin_of_safety"] = margin_of_safety
        mos_pct = margin_of_safety * 100

        if weighted_result["degraded"]:
            detail_lines.append(f"\n{weighted_result['warning']}")
            detail_lines.append(f"安全边际 (单一方法): {mos_pct:.1f}% (目标¥{weighted_target:.2f} vs 市价¥{current_price:.2f})")
        else:
            valid_method_list = ", ".join(weighted_result["valid_methods"])
            detail_lines.append(
                f"\n✓ 加权目标价: ¥{weighted_target:.2f} (基于 {len(weighted_result['valid_methods'])} 个有效方法: {valid_method_list})"
            )
            if weighted_result["excluded_methods"]:
                excluded_list = ", ".join(weighted_result["excluded_methods"])
                detail_lines.append(f"  已排除异常值: {excluded_list}")
            detail_lines.append(f"安全边际 (加权): {mos_pct:.1f}% (目标¥{weighted_target:.2f} vs 市价¥{current_price:.2f})")
    elif not weighted_target and current_price:
        # Degraded mode with 0 valid methods
        if weighted_result.get("warning"):
            detail_lines.append(f"\n{weighted_result['warning']}")
        detail_lines.append("⚠ 所有估值方法均被排除，无法计算目标价")

    # Determine signal based on margin of safety
    if margin_of_safety is not None:
        # Adjust confidence based on degraded mode
        base_confidence_multiplier = 0.5 if weighted_result["degraded"] else 1.0

        if margin_of_safety >= 0.30:
            signal = "bullish"
            confidence = min(0.90, (0.60 + margin_of_safety * 0.5) * base_confidence_multiplier)
        elif margin_of_safety >= 0.10:
            signal = "neutral"
            confidence = 0.55 * base_confidence_multiplier
        elif margin_of_safety >= -0.10:
            signal = "neutral"
            confidence = 0.45 * base_confidence_multiplier
        else:
            signal = "bearish"
            confidence = min(0.90, (0.55 + abs(margin_of_safety) * 0.5) * base_confidence_multiplier)

        # Override with degraded confidence if applicable
        if weighted_result["degraded"]:
            confidence = min(confidence, weighted_result["confidence"])
    else:
        signal, confidence = "neutral", 0.30
        if not weighted_target:
            confidence = 0.25  # Very low confidence when no valid methods
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
            # Format validation context for LLM
            valid_methods_str = ", ".join(weighted_result["valid_methods"]) if weighted_result["valid_methods"] else "无"
            excluded_methods_str = ", ".join(weighted_result["excluded_methods"]) if weighted_result["excluded_methods"] else "无"
            weighted_target_str = f"¥{weighted_target:.2f}" if weighted_target else "N/A"
            validation_mode = "降级模式（≤1个有效方法）" if weighted_result["degraded"] else "正常模式"

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
                valid_methods=valid_methods_str,
                excluded_methods=excluded_methods_str,
                weighted_target=weighted_target_str,
                validation_mode=validation_mode,
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
