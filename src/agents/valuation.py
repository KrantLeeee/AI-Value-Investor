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


def _validate_valuation_result(
    method_name: str,
    target_price: float,
    current_price: float,
    all_results: list[float]
) -> dict:
    """
    Validate a valuation result against three outlier detection rules.

    Rules (any violation triggers exclusion):
    1. Negative or zero target price → exclude
    2. Deviation from current price > 80% → exclude
    3. Deviation from median of all results > 50% → exclude

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

    # Rule 2: Deviation from current price > 80%
    if current_price and current_price > 0:
        deviation_from_current = abs(target_price - current_price) / current_price
        if deviation_from_current > 0.80:
            warnings.append(
                f"{method_name}: deviation from current price "
                f"{deviation_from_current*100:.1f}% exceeds 80% threshold "
                f"(target=¥{target_price:.2f} vs current=¥{current_price:.2f})"
            )
            valid = False

    # Rule 3: Deviation from median > 50%
    # Use statistics.median (resistant to outliers), NOT mean
    if all_results and len(all_results) > 0:
        # Filter out invalid values for median calculation
        valid_prices = [p for p in all_results if p is not None and p > 0]
        if len(valid_prices) >= 2:
            median_price = statistics.median(valid_prices)
            deviation_from_median = abs(target_price - median_price) / median_price
            if deviation_from_median > 0.50:
                warnings.append(
                    f"{method_name}: deviation from median "
                    f"{deviation_from_median*100:.1f}% exceeds 50% threshold "
                    f"(target=¥{target_price:.2f} vs median=¥{median_price:.2f})"
                )
                valid = False

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

    # ── 5. Outlier Detection & Weighted Target Price ──────────────────────────
    # Collect all per-share target prices for validation
    valuation_methods = []
    dcf_per_share = None
    pb_target_per_share = None

    # DCF base case (per share)
    if dcf_base and shares and shares > 0:
        dcf_per_share = dcf_base / shares
        results["dcf_per_share"] = dcf_per_share
        valuation_methods.append(("DCF", dcf_per_share))

    # Graham Number
    if graham_number_per_share:
        valuation_methods.append(("Graham", graham_number_per_share))

    # EV/EBITDA per share
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

    # Calculate weighted target price with default weights
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
