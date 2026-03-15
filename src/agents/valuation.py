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
from src.data.industry_mapping import REAL_ESTATE_CONFIG, get_industry_type
from src.agents.wacc import (
    calculate_wacc,
    generate_sensitivity_matrix,
    generate_sensitivity_heatmap,
    format_sensitivity_heatmap,
)
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
    classify_industry_v3,
)
from src.utils.logger import get_logger
from src.utils.config import get_feature_flags

logger = get_logger(__name__)

AGENT_NAME = "valuation"

# Real estate industry keywords for detection
REAL_ESTATE_KEYWORDS = ["房地产", "地产", "住宅", "物业", "房产", "不动产"]


def is_real_estate_industry(industry: str) -> bool:
    """
    Check if an industry string indicates real estate sector.

    Args:
        industry: Industry classification string

    Returns:
        True if the industry is real estate related
    """
    if not industry:
        return False
    return any(keyword in industry for keyword in REAL_ESTATE_KEYWORDS)


def apply_real_estate_cap(pb_value: float, industry_type: str) -> dict:
    """
    Apply P/B cap for real estate industry.

    Args:
        pb_value: Calculated P/B multiple
        industry_type: Industry classification

    Returns:
        dict with pb_capped value and optional warning
    """
    if not is_real_estate_industry(industry_type):
        return {"pb_capped": pb_value, "warning": None}

    cap = REAL_ESTATE_CONFIG["pb_multiple_cap"]
    if pb_value > cap:
        return {
            "pb_capped": cap,
            "original_pb": pb_value,
            "warning": REAL_ESTATE_CONFIG["warning_text"],
        }

    return {"pb_capped": pb_value, "warning": None}


def calculate_ev_ebitda_value(
    ebitda: float, multiple: float, shares: int, revenue: float
) -> tuple[float | None, str | None]:
    """
    Calculate per-share value using EV/EBITDA method with validation.

    Args:
        ebitda: Company's EBITDA (Earnings Before Interest, Taxes, Depreciation, Amortization)
        multiple: EV/EBITDA multiple to apply
        shares: Total number of shares outstanding
        revenue: Company's total revenue (for sanity check)

    Returns:
        Tuple of (per_share_value, error_message)
        - If valid: (calculated_value, None)
        - If invalid: (None, error_description)
    """
    # Validate EBITDA is positive
    if ebitda is None or ebitda <= 0:
        return None, "EBITDA无效：EBITDA必须为正数"

    # Calculate Enterprise Value
    ev = ebitda * multiple

    # Sanity check: EV should be at least 10% of revenue
    min_ev = revenue * 0.1
    if ev < min_ev:
        return None, f"EV异常：计算的EV({ev:,.0f})低于营收的10%({min_ev:,.0f})，数据可能有误"

    # Calculate per-share value
    per_share_value = ev / shares

    return per_share_value, None


# ── Task 3.1: Brand Moat P/E Anchor Valuation ──────────────────────────────


BRAND_MOAT_CRITERIA = {
    "gross_margin_min": 0.60,
    "roe_5yr_avg_min": 0.15,
    "fcf_positive_years_min": 4,
    "revenue_growth_stable": True,
}


def detect_brand_moat(metrics: dict) -> bool:
    """
    Detect if company has brand moat characteristics.
    Must meet 3+ conditions.
    """
    conditions_met = 0

    if metrics.get("gross_margin", 0) >= BRAND_MOAT_CRITERIA["gross_margin_min"] * 100:
        conditions_met += 1

    if metrics.get("roe_5yr_avg", 0) >= BRAND_MOAT_CRITERIA["roe_5yr_avg_min"] * 100:
        conditions_met += 1

    fcf_history = metrics.get("fcf_history", [])
    positive_years = sum(1 for fcf in fcf_history if fcf > 0)
    if positive_years >= BRAND_MOAT_CRITERIA["fcf_positive_years_min"]:
        conditions_met += 1

    revenue_growth = metrics.get("revenue_growth_5yr", [])
    if len(revenue_growth) >= 3 and all(g > 0 for g in revenue_growth[-3:]):
        conditions_met += 1

    return conditions_met >= 3


BRAND_MOAT_PE_ANCHORS = {
    "premium": {"pe_range": (30, 40)},
    "strong": {"pe_range": (25, 35)},
    "moderate": {"pe_range": (20, 28)},
}


def classify_moat_tier(metrics: dict) -> str:
    """Classify moat tier based on financials."""
    gross_margin = metrics.get("gross_margin", 0)
    roe_5yr = metrics.get("roe_5yr_avg", 0)

    if gross_margin >= 80 and roe_5yr >= 25:
        return "premium"
    elif gross_margin >= 60 and roe_5yr >= 18:
        return "strong"
    elif gross_margin >= 50 and roe_5yr >= 15:
        return "moderate"

    return None


def apply_brand_moat_valuation(metrics: dict, industry_config: dict) -> dict:
    """
    Apply brand moat valuation using P/E anchors.

    Why P/E not P/B:
    - P/B formula (ROE/Ke) fails for growth companies (g > 0)
    - Brand value isn't reflected in book value
    - P/E directly captures earnings power
    """
    if not detect_brand_moat(metrics):
        return None

    # Classify tier
    moat_tier = classify_moat_tier(metrics)
    if moat_tier is None:
        moat_tier = "moderate"

    # Get P/E range
    pe_range = BRAND_MOAT_PE_ANCHORS[moat_tier]["pe_range"]
    pe_low, pe_high = pe_range
    pe_mid = (pe_low + pe_high) / 2

    # Normalize EPS: use max(TTM, 3yr avg) to avoid one-time impact
    eps_ttm = metrics.get("eps")
    eps_3yr_avg = metrics.get("eps_3yr_avg")

    if eps_ttm is None or eps_ttm <= 0:
        if eps_3yr_avg is not None and eps_3yr_avg > 0:
            eps = eps_3yr_avg
            eps_source = "3年平均EPS（TTM为负）"
        else:
            return {
                "method": "pe_moat",
                "error": "EPS为负或无效，护城河P/E估值不适用",
                "moat_tier": moat_tier,
            }
    elif eps_3yr_avg is not None and eps_3yr_avg > eps_ttm * 1.2:
        eps = eps_3yr_avg
        eps_source = f"3年平均EPS（TTM={eps_ttm:.2f}显著偏低）"
    else:
        eps = eps_ttm
        eps_source = "TTM EPS"

    # Calculate target prices
    target_low = eps * pe_low
    target_mid = eps * pe_mid
    target_high = eps * pe_high

    return {
        "method": "pe_moat",
        "moat_tier": moat_tier,
        "pe_range": pe_range,
        "pe_applied": pe_mid,
        "target_price": target_mid,
        "target_range": (target_low, target_high),
        "eps_used": eps,
        "eps_source": eps_source,
        "eps_ttm": eps_ttm,
        "eps_3yr_avg": eps_3yr_avg,
        "note": f"护城河等级: {moat_tier}，P/E锚点: {pe_low}-{pe_high}x，使用{eps_source}",
    }


def _get_industry_position_safe(ticker: str) -> dict | None:
    """
    P2-1: Get industry valuation positioning with error handling.

    Returns positioning dict or None if unavailable.
    """
    try:
        from src.agents.industry_valuation import get_industry_position

        result = get_industry_position(ticker)
        if result.get("error"):
            logger.warning("[Valuation] Industry positioning failed: %s", result["error"])
            return None
        return result
    except Exception as e:
        logger.warning("[Valuation] Industry positioning error: %s", e)
        return None


# Default valuation assumptions (conservative value-investor settings)
WACC_DEFAULT = 0.10  # 10% discount rate
WACC_CYCLE_ADDON = 0.005  # +50bp for highly cyclical sectors (oil services)
TERMINAL_GROWTH = 0.025  # 2.5% perpetual growth — conservative (was 3%)
FCF_GROWTH_BULL = 0.12  # 12% — optimistic scenario
FCF_GROWTH_BASE = 0.07  # 7%  — base scenario
FCF_GROWTH_BEAR = 0.02  # 2%  — pessimistic scenario
PROJECTION_YEARS = 10
INDUSTRY_EV_EBITDA_OIL = 6.0  # oil services sector multiple (3rd-party benchmark)
INDUSTRY_EV_EBITDA = 8.0  # generic fallback multiple
# P/B midpoint targets by sector (industry research benchmarks)
PB_TARGET_OIL_SERVICES = 1.8  # oil services: 1.6-2.1x midpoint
PB_TARGET_DEFAULT = 2.0  # generic fallback
# WACC = 0.10 kept as alias for backward compatibility
WACC = WACC_DEFAULT

# BUG-03A: PS (Price-to-Sales) multiples for loss-making tech stocks
# Based on A-share tech sector medians (2023-2025 data)
PS_MULTIPLE_TECH_AI = 8.0  # AI/Voice tech: 6-10x median
PS_MULTIPLE_TECH_SOFTWARE = 6.0  # Software/SaaS: 4-8x median
PS_MULTIPLE_DEFAULT = 4.0  # Generic tech fallback

# EV/Sales multiples (for loss-making tech stocks)
EV_SALES_TECH = 6.0  # Tech sector EV/Sales median

# BUG-03B: PEG (Price/Earnings-to-Growth) parameters for growth stocks
# PEG = PE / EPS Growth Rate
# Fair PEG for A-share growth stocks is typically 1.0-1.5x
# Premium quality growth stocks can justify PEG up to 2.0x
PEG_FAIR_VALUE = 1.2  # A-share quality growth premium
PEG_MAX_REASONABLE = 2.0  # Above this, overvalued even for growth
PEG_BARGAIN = 0.8  # Below this, potentially undervalued

# Phase 2: Financial stock (bank/insurance) valuation parameters
# P/B valuation: Fair PB = ROE / Ke (cost of equity)
# Insurance embedded value typically trades at 0.6-1.2x EV
FINANCIAL_COST_OF_EQUITY = 0.08  # 8% cost of equity assumption
FINANCIAL_DDM_GROWTH = 0.03  # 3% long-term dividend growth
PB_MIN_FINANCIAL = 0.5  # Minimum reasonable P/B for banks
PB_MAX_FINANCIAL = 3.0  # Maximum reasonable P/B

# Task #15: Utility stock valuation parameters (DDM is primary for stable dividend payers)
# Utilities have lower cost of equity due to regulated, stable cash flows
UTILITY_COST_OF_EQUITY = 0.07  # 7% cost of equity (lower than financial due to stability)
UTILITY_DDM_GROWTH = 0.025  # 2.5% long-term dividend growth (inflation-linked)

# Utility tickers - these use DDM as primary valuation method
_UTILITY_TICKERS = {
    "600900.SH",  # 长江电力 - stable dividend payer
    "601985.SH",  # 中国核电
    "600023.SH",  # 浙能电力
    "600025.SH",  # 华能水电
    "000027.SZ",  # 深圳能源
    "600011.SH",  # 华能国际
    "600795.SH",  # 国电电力
    "601991.SH",  # 大唐发电
    "600886.SH",  # 国投电力
}

# Phase 2: Cyclical stock valuation parameters
# Use cycle-bottom multiples, not current period
EV_EBITDA_CYCLE_BOTTOM = 5.0  # Cycle trough EV/EBITDA for oil services
EV_EBITDA_CYCLE_NORMAL = 7.0  # Mid-cycle EV/EBITDA
EV_EBITDA_CYCLE_PEAK = 10.0  # Cycle peak EV/EBITDA
PB_CYCLE_BOTTOM = 0.7  # Cycle trough P/B for resources

# Phase 2: Healthcare stock valuation parameters
# R&D stage uses PS (like loss-making tech), mature uses PE
PS_MULTIPLE_HEALTHCARE_RD = 8.0  # R&D stage biotech/pharma PS multiple
PS_MULTIPLE_HEALTHCARE_MATURE = 4.0  # Mature pharma PS multiple
PE_MULTIPLE_HEALTHCARE = 30.0  # Mature healthcare PE multiple (higher than general)
EV_EBITDA_HEALTHCARE = 18.0  # Healthcare EV/EBITDA (higher than general)


# ── Task 3.4: Distressed Company Valuation Framework ──────────────────────────


DISTRESSED_CATEGORIES = {
    "asset_intensive": {
        "keywords": ["超市", "零售", "门店", "制造", "工厂", "餐饮"],
        "valuation_method": "asset_replacement",
        "ev_sales_multiple": 0.2,
        "note": "以门店/设备重置成本为底线",
    },
    "contract_based": {
        "keywords": ["工程", "环保", "PPP", "建筑", "施工", "BOT"],
        "valuation_method": "backlog_value",
        "ev_sales_multiple": None,
        "note": "以在手合同/订单折现价值为锚点",
    },
    "receivables_heavy": {
        "keywords": ["政府项目", "市政", "国企客户", "央企客户"],
        "valuation_method": "receivables_recovery",
        "ev_sales_multiple": None,
        "note": "以应收账款预期回收率为关键变量",
    },
    "generic_distressed": {
        "valuation_method": "ev_sales",
        "ev_sales_multiple": 0.3,
        "note": "通用困境折价",
    },
}


def detect_distressed_company(metrics: dict) -> bool:
    """
    Detect if company is distressed.
    Need 2+ signals to classify as distressed.
    """
    # Safely get values with proper None handling
    net_margin = metrics.get("net_margin")
    roe = metrics.get("roe")
    fcf = metrics.get("fcf")
    ocf = metrics.get("ocf")
    debt_equity = metrics.get("debt_equity")

    signals = [
        net_margin is not None and net_margin < -20,  # Deep loss
        roe is not None and roe < -15,  # Negative ROE
        (fcf is not None and fcf < 0) and (ocf is not None and ocf < 0),  # Double negative
        debt_equity is not None and debt_equity > 300,  # High leverage
    ]
    return sum(signals) >= 2


def classify_distressed_type(company_info: dict, metrics: dict) -> str:
    """Classify distressed company type."""
    business_desc = company_info.get("business_description", "")
    company_name = company_info.get("name", "")
    combined = business_desc + company_name

    # Check keyword matches
    for category, config in DISTRESSED_CATEGORIES.items():
        if category == "generic_distressed":
            continue
        keywords = config.get("keywords", [])
        if any(kw in combined for kw in keywords):
            return category

    # Check financial characteristics
    receivables = metrics.get("accounts_receivable", 0)
    revenue = metrics.get("revenue", 1)
    if receivables / revenue > 0.5:
        return "receivables_heavy"

    return "generic_distressed"


def is_delisting_risk(metrics: dict) -> dict:
    """
    Assess delisting risk based on A-share rules.

    Rules:
    - 2 consecutive loss years: *ST
    - 3 consecutive loss years: suspend trading
    - Negative net assets: delisting warning
    """
    risk_factors = []
    risk_level = "LOW"

    # Check consecutive losses
    net_income_history = metrics.get("net_income_history", [])
    consecutive_losses = 0
    for ni in reversed(net_income_history):
        if ni is None:
            continue  # Skip None values in history
        if ni < 0:
            consecutive_losses += 1
        else:
            break

    if consecutive_losses >= 3:
        risk_factors.append("连续三年亏损")
        risk_level = "HIGH"
    elif consecutive_losses >= 2:
        risk_factors.append("连续两年亏损")
        risk_level = "MEDIUM" if risk_level == "LOW" else risk_level

    # Check net assets (only if explicitly provided)
    net_assets = metrics.get("net_assets")
    if net_assets is not None:
        if net_assets <= 0:
            risk_factors.append("净资产为负")
            risk_level = "HIGH"
        elif net_assets < metrics.get("total_assets", 1) * 0.1:
            risk_factors.append("净资产占比过低")
            risk_level = "MEDIUM" if risk_level == "LOW" else risk_level

    # Check audit opinion
    audit_opinion = metrics.get("audit_opinion", "标准无保留")
    if audit_opinion in ["无法表示意见", "否定意见"]:
        risk_factors.append(f"审计意见: {audit_opinion}")
        risk_level = "HIGH"
    elif audit_opinion in ["保留意见", "带强调事项段"]:
        risk_factors.append(f"审计意见: {audit_opinion}")
        risk_level = "MEDIUM" if risk_level == "LOW" else risk_level

    return {"level": risk_level, "factors": risk_factors, "consecutive_losses": consecutive_losses}


def distressed_valuation(metrics: dict, company_info: dict) -> dict:
    """
    Distressed company valuation framework.
    Selects method based on distressed type.
    """
    results = {}

    # 1. Determine type
    distressed_type = classify_distressed_type(company_info, metrics)
    config = DISTRESSED_CATEGORIES[distressed_type]
    results["distressed_type"] = distressed_type
    results["valuation_note"] = config["note"]

    shares = metrics.get("shares", 1)

    # 2. Apply type-specific valuation
    if config["valuation_method"] == "asset_replacement":
        fixed_assets = metrics.get("fixed_assets", 0)
        inventory = metrics.get("inventory", 0)
        # 50% discount on fixed assets, 30% discount on inventory
        replacement_value = fixed_assets * 0.5 + inventory * 0.7
        results["asset_replacement"] = {
            "value": replacement_value / shares,
            "fixed_assets": fixed_assets,
            "inventory": inventory,
            "note": "资产重置价值（固定资产50%折价 + 存货70%折价）",
        }
        # EV/Sales as reference
        if metrics.get("revenue", 0) > 0:
            ev = metrics["revenue"] * config["ev_sales_multiple"]
            results["ev_sales_ref"] = {
                "value": ev / shares,
                "multiple": config["ev_sales_multiple"],
                "note": "仅供参考",
            }

    elif config["valuation_method"] == "backlog_value":
        backlog = metrics.get("order_backlog", 0)
        if backlog > 0:
            gross_margin = metrics.get("gross_margin", 15) / 100
            backlog_pv = backlog * gross_margin * 0.85
            results["backlog_value"] = {
                "value": backlog_pv / shares,
                "order_backlog": backlog,
                "assumed_margin": gross_margin,
                "note": f"在手订单{backlog/1e8:.1f}亿，假设毛利率{gross_margin:.0%}，3年折现",
            }
        else:
            results["backlog_value"] = {"error": "无法获取在手订单数据", "fallback": "ev_sales"}
            if metrics.get("revenue", 0) > 0:
                results["ev_sales"] = {
                    "value": metrics["revenue"] * 0.4 / shares,
                    "multiple": 0.4,
                    "note": "回退到EV/Sales（无订单数据）",
                }

    elif config["valuation_method"] == "receivables_recovery":
        receivables = metrics.get("accounts_receivable", 0)
        recovery_low = receivables * 0.5
        recovery_high = receivables * 0.7
        results["receivables_recovery"] = {
            "value_range": (recovery_low / shares, recovery_high / shares),
            "receivables": receivables,
            "recovery_rate": "50%-70%",
            "note": f"应收账款{receivables/1e8:.1f}亿，预期回收率50%-70%",
        }

    else:  # generic_distressed
        if metrics.get("revenue", 0) > 0:
            ev = metrics["revenue"] * config["ev_sales_multiple"]
            results["ev_sales"] = {
                "value": ev / shares,
                "multiple": config["ev_sales_multiple"],
                "note": config["note"],
            }

    # 3. Net-Net analysis (all types)
    current_assets = metrics.get("current_assets", 0)
    total_liabilities = metrics.get("total_liabilities", 0)
    if current_assets > total_liabilities:
        results["net_net"] = {
            "value": (current_assets - total_liabilities) / shares,
            "current_assets": current_assets,
            "total_liabilities": total_liabilities,
        }

    # 4. Delisting risk
    delisting_risk = is_delisting_risk(metrics)
    if delisting_risk["level"] in ["MEDIUM", "HIGH"]:
        results["delisting_risk"] = delisting_risk

    return results


# ── Task 3.5 & 3.6: Outlier Threshold Adjustment + DCF Exclusion ──────────────


def get_outlier_threshold(industry_type: str, method_name: str | None = None) -> float:
    """
    Get outlier threshold based on industry type AND method name.

    P0-3 FIX: Added method_name parameter to handle moat P/E specially.
    Moat P/E valuation is DESIGNED to give higher prices than other methods,
    so it should use a relaxed threshold to avoid being incorrectly excluded.

    Args:
        industry_type: Industry classification string
        method_name: Optional method name for method-specific thresholds

    Returns:
        Outlier threshold multiplier (e.g., 0.6 means 60% deviation from median)
    """
    # Method-specific thresholds (P0-3 fix)
    # P/E_Moat is designed to give higher valuations for moat companies
    # It should NOT be excluded just because it differs from other methods
    if method_name == "P/E_Moat":
        return 1.5  # 150% deviation allowed for moat P/E

    thresholds = {
        "auto_new_energy": 1.5,  # EV makers like BYD have wide valuation ranges
        "new_energy_mfg": 1.5,  # Battery/solar manufacturers
        "growth_tech": 2.0,  # High-growth tech (most volatile)
        "brand_moat": 1.0,  # Moat companies get relaxed threshold (100%)
        "consumer_premium": 1.0,  # Premium consumer brands
        "default": 0.6,  # Standard 60% threshold for others
    }
    return thresholds.get(industry_type, thresholds["default"])


def should_exclude_dcf(dcf_value: float, median_value: float, growth_rate: float) -> bool:
    """
    Determine if DCF should be excluded based on deviation from median.

    High-growth companies (>20% growth) get relaxed 1.5x threshold because
    DCF valuations for growth companies are inherently more sensitive to
    growth rate assumptions.

    Low-growth companies use stricter 0.6x threshold.

    Args:
        dcf_value: DCF valuation result
        median_value: Median of all valuation methods
        growth_rate: Company's growth rate (percentage, e.g., 25 for 25%)

    Returns:
        True if DCF should be excluded, False otherwise
    """
    if median_value == 0:
        return True

    deviation = abs(dcf_value - median_value) / median_value
    threshold = 1.5 if growth_rate > 20 else 0.6
    return deviation > threshold


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _dcf(
    base_fcf: float,
    growth_rate: float,
    wacc: float = WACC,
    terminal_growth: float = TERMINAL_GROWTH,
    years: int = PROJECTION_YEARS,
) -> float:
    """
    10-year DCF with terminal value.
    Returns total present value (NOT per share — divide by shares outstanding separately).
    """
    pv = 0.0
    fcf = base_fcf
    for yr in range(1, years + 1):
        fcf *= 1 + growth_rate
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
    all_results: list[float],
    industry_type: str = "default",
) -> dict:
    """
    Validate a valuation result against outlier detection rules.

    BUG-02 FIX: Changed from market price baseline to method median baseline.
    Old rules incorrectly excluded valid valuations for undervalued/overvalued stocks.

    Rules (any violation triggers exclusion):
    1. Negative or zero target price → exclude
    2. Deviation from method median > industry threshold → exclude (BUG-02 fix: uses median)
    3. EXCEPTION: If all methods agree on direction (all above or all below market),
       skip rule 2 (don't exclude consistent signals)
    4. P1-5 HARD CAP: Deviation > 100% from median → always exclude regardless of consensus
       (prevents wildly off methods from skewing weighted average)

    Args:
        method_name: Name of the valuation method (e.g., "DCF", "Graham")
        target_price: Target price from this method
        current_price: Current market price
        all_results: List of all target prices (for median calculation)
        industry_type: Industry type for threshold lookup (e.g., "auto_new_energy", "default")

    Returns:
        dict with:
            - method: str
            - target_price: float
            - valid: bool (True if passes all rules)
            - warnings: list[str] (reasons for exclusion)
            - exclude_from_weighted: bool (True if should be excluded)
    """
    # P1-5: Hard deviation cap - beyond this, always exclude regardless of consensus
    HARD_DEVIATION_CAP = 1.0  # 100% deviation = always exclude
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
            all_above_market = (
                all(p > current_price for p in valid_prices) if current_price > 0 else False
            )
            all_below_market = (
                all(p < current_price for p in valid_prices) if current_price > 0 else False
            )
            directional_consensus = all_above_market or all_below_market

            median_price = statistics.median(valid_prices)
            deviation_from_median = (
                abs(target_price - median_price) / median_price if median_price > 0 else 0
            )

            # Task 3.5 + P0-3 fix: Use industry-specific AND method-specific threshold
            threshold = get_outlier_threshold(industry_type, method_name=method_name)

            # P1-5: Hard cap - always exclude if deviation > 100%, regardless of consensus
            if deviation_from_median > HARD_DEVIATION_CAP:
                warnings.append(
                    f"{method_name}: deviation from median "
                    f"{deviation_from_median*100:.1f}% exceeds hard cap {HARD_DEVIATION_CAP*100:.0f}% "
                    f"(target=¥{target_price:.2f} vs median=¥{median_price:.2f}) - always excluded"
                )
                valid = False
            elif deviation_from_median > threshold and not directional_consensus:
                warnings.append(
                    f"{method_name}: deviation from median "
                    f"{deviation_from_median*100:.1f}% exceeds {threshold*100:.0f}% threshold "
                    f"(target=¥{target_price:.2f} vs median=¥{median_price:.2f})"
                )
                valid = False
            elif deviation_from_median > threshold and directional_consensus:
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
    results: list[dict], current_price: float, weights: dict[str, float] | None = None
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
        r["target_price"] * normalized_weights.get(r["method"], 0) for r in valid_results
    )

    # ── DEBUG: Log weighted calculation output ────────────────────────────────
    manual_calc = sum(
        r["target_price"] * normalized_weights.get(r["method"], 0) for r in valid_results
    )
    logger.info(f"[Weighted Calc] Final weighted price: ¥{weighted_target:.2f}")
    logger.info(f"[Weighted Calc] Manual verification: ¥{manual_calc:.2f}")
    logger.info(
        f"[Weighted Calc] Detailed calculation: "
        + " + ".join(
            [
                f"¥{r['target_price']:.2f}×{normalized_weights.get(r['method'], 0):.4f}"
                for r in valid_results
            ]
        )
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


def _get_dividend_from_akshare(ticker: str) -> float | None:
    """
    Task #15: Fetch dividend per share from AKShare for utility stocks.

    Uses stock_history_dividend_detail which returns:
    - 派息: Cash dividend per 10 shares (e.g., 2.10 means ¥0.21 per share)

    For annual DPS, sums up all dividends with "实施" status in the current/recent year.

    Returns:
        Estimated annual dividend per share in yuan, or None if unavailable.
    """
    try:
        import akshare as ak
        from datetime import datetime

        code = ticker.split(".")[0]

        # Use stock_history_dividend_detail - the working API
        df = ak.stock_history_dividend_detail(symbol=code)

        if df is None or df.empty:
            logger.debug("[Valuation] %s: No dividend detail data from AKShare", ticker)
            return None

        # Calculate annual DPS by summing implemented dividends
        # 派息 column contains dividend per 10 shares
        if "派息" not in df.columns:
            logger.debug("[Valuation] %s: No '派息' column in dividend data", ticker)
            return None

        # Filter for implemented dividends (进度 == "实施")
        if "进度" in df.columns:
            implemented = df[df["进度"] == "实施"]
        else:
            implemented = df

        if implemented.empty:
            # Fallback to first row if no implemented
            implemented = df.head(1)

        # Get current year
        current_year = datetime.now().year

        # Sum dividends from current year or most recent year with data
        annual_dividend_per_10 = 0.0
        years_checked = set()

        for _, row in implemented.iterrows():
            try:
                # Parse announcement date
                announce_date = row.get("公告日期")
                if announce_date:
                    if isinstance(announce_date, str):
                        year = int(announce_date[:4])
                    else:
                        year = announce_date.year

                    # Only sum dividends from current year or previous year
                    if year >= current_year - 1:
                        dividend_per_10 = float(row["派息"]) if row["派息"] else 0
                        if dividend_per_10 > 0:
                            annual_dividend_per_10 += dividend_per_10
                            years_checked.add(year)
            except (ValueError, TypeError):
                continue

        # If no current year data, use the latest single dividend
        if annual_dividend_per_10 == 0 and not implemented.empty:
            latest = implemented.iloc[0]
            dividend_per_10 = float(latest["派息"]) if latest["派息"] else 0
            if dividend_per_10 > 0:
                annual_dividend_per_10 = dividend_per_10

        if annual_dividend_per_10 > 0:
            dps = annual_dividend_per_10 / 10  # Convert from per-10-shares to per-share
            logger.info(
                "[Valuation] %s: fetched annual DPS=¥%.3f from AKShare " "(派息=%s/10股, years=%s)",
                ticker,
                dps,
                annual_dividend_per_10,
                years_checked or "latest",
            )
            return dps

    except Exception as e:
        logger.warning("[Valuation] AKShare dividend fetch failed for %s: %s", ticker, e)

    return None


def _is_utility_stock(ticker: str) -> bool:
    """Check if ticker is a utility stock that should use DDM valuation."""
    return ticker in _UTILITY_TICKERS


def _build_engine_metrics(ticker: str, market: str) -> dict:
    """Build metrics dict required by industry_engine.get_valuation_config().

    Uses existing database query functions from src.data.database module.
    """
    # Fetch latest balance sheet
    balance_sheets = get_balance_sheets(ticker, limit=1, period_type="annual")
    latest_bs = balance_sheets[0] if balance_sheets else {}

    # Fetch latest financial metrics
    fin_metrics = get_financial_metrics(ticker, limit=5)
    latest_fm = fin_metrics[0] if fin_metrics else {}

    # Calculate derived metrics
    roe_values = [m.get("roe") for m in fin_metrics[:5] if m.get("roe") is not None]
    fcf_positive = sum(1 for m in fin_metrics[:5] if (m.get("fcf_per_share") or 0) > 0)

    return {
        # From balance sheet
        "total_assets": latest_bs.get("total_assets"),
        "inventory": latest_bs.get("inventory"),
        "advance_receipts": latest_bs.get("advance_receipts"),
        "fixed_assets": latest_bs.get("fixed_assets"),
        "has_loan_loss_provision": bool(latest_bs.get("has_loan_loss_provision", 0)),
        "has_insurance_reserve": bool(latest_bs.get("has_insurance_reserve", 0)),

        # From financial metrics
        "de_ratio": latest_fm.get("debt_to_equity"),
        "gross_margin": latest_fm.get("gross_margin"),
        "net_margin": latest_fm.get("operating_margin"),  # Approximate
        "roe": latest_fm.get("roe"),
        "rd_expense_ratio": latest_fm.get("rd_expense_ratio"),
        "revenue_growth": latest_fm.get("revenue_growth"),
        "net_income_growth": latest_fm.get("net_income_growth"),

        # Derived
        "roe_5yr_avg": sum(roe_values) / len(roe_values) if roe_values else None,
        "fcf_positive_years": fcf_positive,
        "report_period": latest_bs.get("period_end_date", "unknown"),
    }


def _get_company_info(ticker: str, market: str) -> dict:
    """Get basic company info for LLM routing.

    Returns minimal info needed for industry engine. The industry label is optional
    since V3 focuses on financial characteristics rather than labels.
    """
    # For now, return minimal info. In Phase 2+, can integrate with company profile API
    return {
        "name": ticker,  # Will be populated by LLM context if available
        "industry": "",
        "business_description": "",
    }


def _get_legacy_valuation_config(ticker: str, market: str, company_info: dict) -> dict:
    """Get valuation config from legacy V2 industry classifier.

    Used only in parallel comparison mode to compare V3 vs V2 results.
    """
    # Use actual legacy classifier from src.agents.industry_classifier
    return classify_industry(ticker, market, company_info)


def run(ticker: str, market: str, use_llm: bool = True) -> AgentSignal:
    """
    Run the Valuation Agent for a given ticker.
    Returns an AgentSignal and persists it to the database.
    """
    income_rows = get_income_statements(ticker, limit=5, period_type="annual")
    balance_rows = get_balance_sheets(ticker, limit=3, period_type="annual")
    cashflow_rows = get_cash_flows(ticker, limit=3, period_type="annual")
    metric_rows = get_financial_metrics(ticker, limit=3)

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

    # BUG-03B: Use AKShare company basics for industry if still default
    if industry == "default":
        try:
            from src.data.fetcher import Fetcher

            fetcher = Fetcher()
            company_info = fetcher.fetch_company_basics(ticker, market)
            if company_info and company_info.get("industry"):
                akshare_industry = company_info["industry"]
                logger.info(f"[Valuation] {ticker}: Using AKShare industry: {akshare_industry}")
                industry = akshare_industry
        except Exception as e:
            logger.warning(f"[Valuation] {ticker}: AKShare industry fetch failed: {e}")

    # Calculate industry-adapted WACC (P2-⑦)
    wacc_result = calculate_wacc(ticker, market, industry, current_price)
    wacc = wacc_result["wacc"]
    wacc_fallback = wacc_result.get("fallback_used", False)

    results: dict = {
        "wacc": wacc * 100,
        "wacc_components": {
            "re": wacc_result.get("re") * 100 if wacc_result.get("re") else None,
            "rd": wacc_result.get("rd") * 100 if wacc_result.get("rd") else None,
            "tc": wacc_result.get("tc") * 100 if wacc_result.get("tc") else None,
            "beta": wacc_result.get("beta"),
            "equity_weight": wacc_result.get("equity_weight") * 100
            if wacc_result.get("equity_weight")
            else None,
            "debt_weight": wacc_result.get("debt_weight") * 100
            if wacc_result.get("debt_weight")
            else None,
        },
        "terminal_growth": TERMINAL_GROWTH * 100,
        "current_price": current_price,
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
    _is_cyclical = industry and any(
        k in (industry or "").lower() for k in ["oil", "energy", "mining", "steel"]
    )
    # Flag for oil services P/B target
    _is_oil = industry and any(
        k in (industry or "").lower() for k in ["oil", "energy", "油", "石油", "油服", "海油"]
    )
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
        detail_lines.append(
            f"⚠ WACC: {wacc*100:.2f}% (使用行业默认值: {wacc_result.get('note', '')})"
        )

    # ── QVeris supplement: enrich shares + balance sheet ────────────────────
    # AKShare often lacks shares_outstanding and current_assets for A-shares.
    if market == "a_share":
        try:
            from src.data.qveris_source import QVerisSource

            qsrc = QVerisSource()
            if qsrc.health_check():
                # Income supplement (for shares derivation)
                if not income_rows or not _safe(income_rows[0].get("eps")):
                    qi = qsrc.get_income_statements(ticker, market, limit=1)
                    if qi and not income_rows:
                        income_rows = [
                            {
                                "revenue": qi[0].revenue,
                                "net_income": qi[0].net_income,
                                "eps": qi[0].eps,
                            }
                        ]
                # Balance supplement
                qb = qsrc.get_balance_sheets(ticker, market, limit=1)
                if qb:
                    if not balance_rows:
                        balance_rows = [{}]
                    _b = balance_rows[0]
                    for fld in [
                        "total_equity",
                        "current_assets",
                        "current_liabilities",
                        "total_assets",
                        "total_liabilities",
                        "cash_and_equivalents",
                    ]:
                        if not _safe(_b.get(fld)) and getattr(qb[0], fld, None):
                            _b[fld] = getattr(qb[0], fld)
                    logger.info("[Valuation] %s: enriched from QVeris", ticker)
        except Exception as _e:
            logger.warning("[Valuation] QVeris enrichment failed: %s", _e)

    latest_ni = None
    if income_rows:
        latest_ni = _safe(income_rows[0].get("net_income"))
        shares_raw = _safe(income_rows[0].get("shares_outstanding"))
        eps_raw = _safe(income_rows[0].get("eps"))
        shares = shares_raw
        # Derive shares from net_income / EPS when not stored explicitly
        if (shares is None or shares == 0) and latest_ni and eps_raw and eps_raw != 0:
            shares = latest_ni / eps_raw
            logger.debug("[Valuation] %s: derived shares=%.0f from NI/EPS", ticker, shares)

    owner_earnings = None
    if cashflow_rows:
        ocf = _safe(cashflow_rows[0].get("operating_cash_flow"))
        fcf = _safe(cashflow_rows[0].get("free_cash_flow"))
        capex = _safe(cashflow_rows[0].get("capital_expenditure"))
        dep = _safe(cashflow_rows[0].get("depreciation"))

        # Owner Earnings = Net Income + D&A − CapEx  (Buffett's formula)
        if latest_ni and capex is not None:
            da = dep or 0
            cx = abs(capex) if capex < 0 else capex
            owner_earnings = latest_ni + da - cx
            results["owner_earnings"] = owner_earnings
            detail_lines.append(f"Owner Earnings: {owner_earnings/1e8:.2f}亿元")

    # ── BUG-03A: Detect loss-making tech stocks ───────────────────────────────
    # These need PS/EV-Sales valuation instead of Graham Number/EV-EBITDA
    is_loss_making_tech = False
    net_margin = None
    revenue_growth = None
    net_income = None  # Initialize to None for healthcare detection fallback
    revenue = None  # Initialize to None for fallback

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

    # P0-3 FIX: Calculate industry_class early for probe filtering
    # This is needed before brand_moat detection to exclude tech industries
    v3_metrics_early = None
    if metric_rows:
        latest_metric = metric_rows[0]
        v3_metrics_early = {
            "net_margin": _safe(latest_metric.get("operating_margin")),
            "gross_margin": _safe(latest_metric.get("gross_margin")),
            "roe": _safe(latest_metric.get("roe")),
            "dividend_payout": _safe(latest_metric.get("dividend_payout")),
            "revenue": _safe(income_rows[0].get("revenue")) if income_rows else None,
        }
    industry_class = classify_industry_v3(industry, metrics=v3_metrics_early) if industry else "default"

    # Get ROE early for loss-making tech detection (BUG-03A fix)
    # This prevents misclassifying profitable low-margin manufacturers
    roe_for_detection = None
    if metric_rows:
        roe_raw = _safe(metric_rows[0].get("roe"))
        if roe_raw is not None:
            # AKShare returns ROE as percentage (e.g., 21.6 for 21.6%)
            # Convert to decimal (0.216)
            roe_for_detection = roe_raw / 100 if roe_raw > 1 else roe_raw

    # Detect loss-making tech (with ROE check to avoid false positives)
    is_loss_making_tech = detect_loss_making_tech_stock(
        net_income=latest_ni,
        net_margin=net_margin,
        revenue_growth=revenue_growth,
        rd_ratio=rd_ratio,
        industry=industry,
        roe=roe_for_detection,  # NEW: prevents misclassifying profitable companies
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
            revenue_cagr_3y = (revenue_current / revenue_3y_ago) ** (1 / 3) - 1
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
        roe_raw = _safe(metric_rows[0].get("roe"))
        dividend_yield_raw = _safe(metric_rows[0].get("dividend_yield"))

        # BUG-FIX: Normalize ROE/dividend_yield to decimal form
        # AKShare returns ROE as percentage (e.g., 10.47 for 10.47%)
        # We need decimal form (0.1047) for P/B-ROE calculation: fair_pb = roe / Ke
        if roe_raw is not None:
            roe = roe_raw / 100 if roe_raw > 1 else roe_raw  # 10.47 → 0.1047
        if dividend_yield_raw is not None:
            dividend_yield = (
                dividend_yield_raw / 100 if dividend_yield_raw > 1 else dividend_yield_raw
            )

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
        # Store as percentage for display (roe is now decimal, so *100 is correct)
        results["roe"] = round(roe * 100, 2) if roe else None
        results["dividend_yield"] = round(dividend_yield * 100, 2) if dividend_yield else None
        detail_lines.append(
            f"🏦 金融股：使用P/B-ROE模型+DDM估值方法，禁用EV/EBITDA " f"(ROE={roe*100:.1f}%)"
            if roe
            else "🏦 金融股：使用P/B-ROE模型+DDM估值方法"
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

    if (
        not is_loss_making_tech
        and not is_growth_stock
        and not is_financial_stock
        and not is_cyclical_stock
    ):
        is_healthcare_stock = detect_healthcare_stock(industry=industry)

    if is_healthcare_stock:
        # Determine development stage
        is_healthcare_rd = detect_healthcare_rd_stage(
            net_income=net_income,
            net_margin=net_margin,
            rd_ratio=None,  # TODO: Get R&D ratio from income statement if available
            revenue_growth=revenue_growth,
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

    # ── Task #15: Detect utility stocks ─────────────────────────────────────────
    # Utility stocks (power, gas, water) use DDM as primary valuation method
    # due to stable, regulated cash flows and predictable dividends
    is_utility_stock = False

    if (
        not is_loss_making_tech
        and not is_growth_stock
        and not is_financial_stock
        and not is_cyclical_stock
        and not is_healthcare_stock
    ):
        is_utility_stock = _is_utility_stock(ticker)

    if is_utility_stock:
        results["is_utility_stock"] = True
        results["valuation_mode"] = "utility"
        detail_lines.append(
            "⚡ 公用事业股：使用DDM股息折现模型为主要估值方法（稳定股息，"
            "监管收益可预测），禁用高增长DCF假设"
        )

    # ── Task 3.1: Detect brand moat stocks ─────────────────────────────────────
    # Premium brands with high gross margin + high ROE use P/E anchor valuation
    # Graham Number is disabled for brand moat stocks (designed for defensive undervalued stocks)
    is_brand_moat = False
    brand_moat_tier = None

    # Build metrics dict for brand moat detection
    gross_margin = None
    if metric_rows:
        gross_margin_raw = _safe(metric_rows[0].get("gross_margin"))
        if gross_margin_raw is not None:
            gross_margin = gross_margin_raw if gross_margin_raw > 1 else gross_margin_raw * 100

    # Get 5-year average ROE from metrics history
    roe_5yr_avg = None
    if metric_rows and len(metric_rows) >= 3:
        roe_values = [_safe(m.get("roe")) for m in metric_rows if _safe(m.get("roe")) is not None]
        if roe_values:
            roe_5yr_avg = sum(roe_values) / len(roe_values)
            # Normalize to percentage
            if roe_5yr_avg < 1:
                roe_5yr_avg = roe_5yr_avg * 100

    # Get FCF history for moat detection
    fcf_history = []
    if cashflow_rows:
        for cf in cashflow_rows:
            fcf_val = _safe(cf.get("free_cash_flow"))
            if fcf_val is not None:
                fcf_history.append(fcf_val)

    # Get EPS early for brand moat detection
    eps_for_moat = _safe(income_rows[0].get("eps")) if income_rows else None

    # Build metrics dict
    moat_metrics = {
        "gross_margin": gross_margin,
        "roe_5yr_avg": roe_5yr_avg,
        "fcf_history": fcf_history,
        "revenue_growth_5yr": [],  # TODO: Calculate from income history
        "eps": eps_for_moat,
        "eps_3yr_avg": None,  # TODO: Calculate from income history
    }

    # Check brand moat if not already classified as special type
    # Brand moat detection criteria:
    # - Premium: gross_margin >= 80% AND (current ROE >= 20% OR 5yr avg ROE >= 20%)
    # - Strong: gross_margin >= 60% AND (current ROE >= 15% OR 5yr avg ROE >= 15%)
    # - Moderate: gross_margin >= 50% AND (current ROE >= 12% OR 5yr avg ROE >= 12%)
    if (
        not is_loss_making_tech
        and not is_growth_stock
        and not is_financial_stock
        and not is_cyclical_stock
        and not is_healthcare_stock
        and not is_utility_stock
    ):
        # Get current ROE from metrics (decimal form, need to convert to percentage)
        current_roe_pct = roe * 100 if roe is not None else None

        # Use the higher of current ROE or 5yr avg ROE
        best_roe = None
        if current_roe_pct is not None and roe_5yr_avg is not None:
            best_roe = max(current_roe_pct, roe_5yr_avg)
        elif current_roe_pct is not None:
            best_roe = current_roe_pct
        elif roe_5yr_avg is not None:
            best_roe = roe_5yr_avg

        # Brand moat detection: high gross margin + reasonable ROE
        # P0-3 FIX: Exclude tech/software industries - they have high gross margins
        # but should use tech valuation frameworks, not consumer brand moat P/E anchors
        tech_industries = ["tech_saas", "tech_traditional", "pharma_innovative", "pharma_cxo", "defense_equipment"]
        is_tech_industry = industry_class in tech_industries

        if gross_margin is not None and best_roe is not None and not is_tech_industry:
            if gross_margin >= 50 and best_roe >= 12:  # Moderate tier baseline
                is_brand_moat = True
                # Determine tier based on gross margin and ROE
                if gross_margin >= 80 and best_roe >= 20:
                    brand_moat_tier = "premium"
                elif gross_margin >= 60 and best_roe >= 15:
                    brand_moat_tier = "strong"
                else:
                    brand_moat_tier = "moderate"

                results["is_brand_moat"] = True
                results["brand_moat_tier"] = brand_moat_tier
                results["valuation_mode"] = "brand_moat"
                results["brand_moat_gross_margin"] = round(gross_margin, 2)
                results["brand_moat_roe"] = round(best_roe, 2)

                pe_range = BRAND_MOAT_PE_ANCHORS.get(
                    brand_moat_tier, BRAND_MOAT_PE_ANCHORS["moderate"]
                )["pe_range"]
                detail_lines.append(
                    f"🏆 护城河品牌股：毛利率{gross_margin:.1f}% + ROE{best_roe:.1f}% → {brand_moat_tier}档，"
                    f"使用P/E锚点估值（{pe_range[0]}-{pe_range[1]}x），禁用Graham Number"
                )

    # ── Task 3.4: Detect distressed companies ──────────────────────────────────
    # Distressed companies need special valuation: asset replacement, net-net, etc.
    is_distressed = False
    distressed_type = None

    # Build distressed detection metrics
    distressed_metrics = {
        "net_margin": net_margin * 100 if net_margin else None,
        "roe": roe * 100 if roe else None,
        "fcf": _safe(cashflow_rows[0].get("free_cash_flow")) if cashflow_rows else None,
        "ocf": _safe(cashflow_rows[0].get("operating_cash_flow")) if cashflow_rows else None,
        "debt_equity": _safe(metric_rows[0].get("debt_to_equity")) * 100
        if metric_rows and _safe(metric_rows[0].get("debt_to_equity"))
        else None,
    }

    # Check if distressed (not if already classified as special type)
    if (
        not is_loss_making_tech
        and not is_growth_stock
        and not is_financial_stock
        and not is_cyclical_stock
        and not is_healthcare_stock
        and not is_utility_stock
        and not is_brand_moat
    ):
        if detect_distressed_company(distressed_metrics):
            is_distressed = True
            results["is_distressed"] = True
            results["valuation_mode"] = "distressed"

            # Get company info for distressed type classification
            company_info = {"name": ticker, "business_description": industry or ""}
            distressed_type = classify_distressed_type(company_info, distressed_metrics)
            results["distressed_type"] = distressed_type

            distressed_config = DISTRESSED_CATEGORIES.get(
                distressed_type, DISTRESSED_CATEGORIES["generic_distressed"]
            )
            detail_lines.append(
                f"⚠️ 困境企业：{distressed_config['note']}，使用{distressed_config['valuation_method']}估值方法"
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
            results.update(
                {
                    "base_fcf": base_fcf,
                    "dcf_bull": dcf_bull,
                    "dcf_base": dcf_base,
                    "dcf_bear": dcf_bear,
                    "fcf_growth_bull": FCF_GROWTH_BULL * 100,
                    "fcf_growth_base": FCF_GROWTH_BASE * 100,
                    "fcf_growth_bear": FCF_GROWTH_BEAR * 100,
                }
            )
            # DCF display: clarify that these are enterprise values (OUTPUT), not FCF (INPUT)
            detail_lines.append(
                f"DCF企业价值 (乐观/基准/悲观): {dcf_bull/1e8:.0f}亿 / {dcf_base/1e8:.0f}亿 / {dcf_bear/1e8:.0f}亿元 "
                f"(输入: {fcf_source}={base_fcf/1e8:.2f}亿)"
            )

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

                # Generate sensitivity heatmap with implied market assumptions (P2 enhancement)
                if current_price and current_price > 0:
                    heatmap = generate_sensitivity_heatmap(
                        base_fcf=base_fcf,
                        shares=shares,
                        current_price=current_price,
                        wacc_range=(max(0.05, wacc * 0.6), min(0.18, wacc * 1.5)),  # Wider range
                        growth_range=(0.00, 0.08),  # Perpetual growth: 0-8%
                        terminal_growth=TERMINAL_GROWTH,
                        years=PROJECTION_YEARS,
                        grid_size=7,
                    )
                    results["sensitivity_heatmap"] = heatmap
                    results["implied_wacc"] = heatmap["implied_wacc"]
                    results["implied_growth"] = heatmap["implied_growth"]

                    # Add formatted heatmap to detail_lines
                    heatmap_md = format_sensitivity_heatmap(heatmap)
                    detail_lines.append("")
                    detail_lines.append(heatmap_md)
                    logger.info(
                        f"[Valuation] {ticker}: implied WACC={heatmap['implied_wacc']*100:.1f}%, "
                        f"implied growth={heatmap['implied_growth']*100:.1f}%"
                    )
        else:
            fcf_str = f"FCF={fcf/1e8:.1f}亿" if fcf is not None else "FCF缺失"
            ocf_str = f"OCF={ocf/1e8:.1f}亿" if ocf is not None else "OCF缺失"
            detail_lines.append(f"⚠ {fcf_str}, {ocf_str} — 均为负或缺失，无法进行 DCF 估值")

    # ── 2. Graham Number ──────────────────────────────────────────────────────
    graham_number_per_share = None
    eps = _safe(income_rows[0].get("eps")) if income_rows else None
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
        detail_lines.append(
            f"Graham Number: ¥{graham_number_per_share:.2f}/股 (EPS={eps:.2f}, BVPS={bvps:.2f})"
        )
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
                logger.debug(
                    "[Valuation] %s: estimated EBITDA=%.0f亿 from NI×1.5", ticker, ebitda / 1e8
                )

    # Phase 3: Use industry-specific EV/EBITDA multiple from industry_profiles.yaml
    # NOTE: industry_class is already calculated early (P0-3 fix for brand_moat filtering)
    _ev_multiple = get_ev_ebitda_multiple(industry_class, cycle_phase="normal")

    # P1-4 FIX: Load disable_methods from industry_profiles.yaml
    # This allows industry-specific method disabling (e.g., telecom_operator disables graham_number)
    disabled_methods_from_profile = []
    try:
        from src.agents.industry_classifier import get_industry_profile

        industry_profile = get_industry_profile(industry_class)
        disabled_methods_from_profile = industry_profile.get("disable_methods", [])
        if disabled_methods_from_profile:
            logger.info(
                f"[Valuation] {ticker}: Industry {industry_class} disables methods: "
                f"{disabled_methods_from_profile}"
            )
    except Exception as e:
        logger.debug(f"[Valuation] Failed to load industry profile disable_methods: {e}")

    # Task #18: Check if utility stock - skip generic EV/EBITDA detail lines
    # Utility stocks will add their own 12x detail lines later
    _skip_generic_ev_detail = _is_utility_stock(ticker)

    if ebitda and ebitda > 0:
        # Get revenue for validation (extracted earlier at line 684)
        revenue = _safe(income_rows[0].get("revenue")) if income_rows else None

        # Use the new calculate_ev_ebitda_value() function with validation
        ev_ebitda_per_share, ev_error = calculate_ev_ebitda_value(
            ebitda=ebitda,
            multiple=_ev_multiple,
            shares=shares,
            revenue=revenue or 0,  # Default to 0 if revenue unavailable
        )

        if ev_error:
            # Validation failed - add warning to detail_lines
            detail_lines.append(f"⚠ EV/EBITDA 估值失败: {ev_error}")
            results["ev_ebitda_per_share"] = None
        else:
            # BUG-18: Additional validation - EV/EBITDA per share should be reasonable
            # If < 10% of current price, the EBITDA estimate is likely unreliable
            if current_price and ev_ebitda_per_share < current_price * 0.1:
                detail_lines.append(
                    f"⚠ EV/EBITDA 不适用: 估值(¥{ev_ebitda_per_share:.2f})远低于市价的10%，EBITDA数据可能无效"
                )
                results["ev_ebitda_per_share"] = None
                ev_ebitda_per_share = None
            else:
                # Validation succeeded - calculate and store total EV
                ev_ebitda_total = ebitda * _ev_multiple
                results["ev_ebitda_value"] = ev_ebitda_total
                results["ev_ebitda_multiple"] = _ev_multiple  # Store for reporting
                ev_ebitda_value = ev_ebitda_total

                # Only add generic detail lines for non-utility stocks
                if not _skip_generic_ev_detail:
                    detail_lines.append(
                        f"EV/EBITDA ({_ev_multiple:.1f}x行业倍数): 总企业价值≈{ev_ebitda_total/1e8:.0f}亿元"
                    )

                # Per-share estimate
                if ev_ebitda_per_share is not None:
                    results["ev_ebitda_per_share"] = round(ev_ebitda_per_share, 2)
                    if not _skip_generic_ev_detail:
                        detail_lines.append(f"EV/EBITDA 每股隐含价值: ¥{ev_ebitda_per_share:.2f}")

        # P/B per share target - also skip for utility stocks (they have their own)
        if bvps and not _skip_generic_ev_detail:
            _pb = PB_TARGET_OIL_SERVICES if _is_oil else PB_TARGET_DEFAULT
            pb_target_per_share = bvps * _pb

            # Apply real estate P/B cap (Task 1.2)
            if is_real_estate_industry(industry):
                cap_result = apply_real_estate_cap(_pb, industry)
                _pb = cap_result["pb_capped"]
                pb_target_per_share = bvps * _pb
                if cap_result.get("warning"):
                    detail_lines.append(cap_result["warning"])

            results["pb_target"] = round(pb_target_per_share, 2)
            detail_lines.append(f"P/B目标价 ({_pb}x BVPS={bvps:.2f}): ¥{pb_target_per_share:.2f}")
    else:
        results["ev_ebitda_value"] = None
        if not _skip_generic_ev_detail:
            detail_lines.append("- EBITDA 数据缺失（注：已尝试用净利润×1.5估算，仍失败）")

    # ── 3b. PS (Price-to-Sales) valuation (BUG-03A: for loss-making tech stocks) ──
    ps_per_share = None
    revenue = _safe(income_rows[0].get("revenue")) if income_rows else None

    if revenue and revenue > 0 and shares and shares > 0:
        # Determine PS multiple based on industry
        _is_ai = industry and any(k in (industry or "").lower() for k in ["ai", "人工智能", "语音"])
        _is_software = industry and any(
            k in (industry or "").lower() for k in ["软件", "software", "saas"]
        )

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
            detail_lines.append(
                f"EV/Sales估值 ({EV_SALES_TECH}x 营收): ¥{ev_sales_per_share:.2f}/股"
            )

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
        # BUG-C FIX: Add note explaining PEG is reference-only
        results["peg_note"] = (
            "PEG估值仅供交叉验证参考，未纳入加权目标价计算。"
            f"原因：EPS增速({eps_growth_pct:.1f}%)对短期波动敏感，"
            "PEG方法对增速假设高度依赖，不适合作为核心定价依据。"
        )

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
        # Try multiple sources for dividend per share:
        # 1. Database metrics
        dps = _safe(metric_rows[0].get("dividend_per_share")) if metric_rows else None

        # 2. Fallback: estimate DPS from dividend yield and current price
        if dps is None and dividend_yield and current_price:
            dps = dividend_yield * current_price

        # 3. Task #17 Fix: Fetch from AKShare directly
        if dps is None:
            dps = _get_dividend_from_akshare(ticker)

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
            detail_lines.append("⚠ DDM估值无法计算（缺少股息数据，已尝试AKShare获取）")

    # ── Task #15: DDM valuation for utility stocks ─────────────────────────────
    # Utilities have stable, predictable dividends - DDM is primary method
    ddm_utility_per_share = None

    if is_utility_stock:
        # Try multiple sources for dividend per share:
        # 1. Database metrics
        dps = _safe(metric_rows[0].get("dividend_per_share")) if metric_rows else None

        # 2. Fallback: estimate from dividend yield
        if dps is None and dividend_yield and current_price:
            dps = dividend_yield * current_price

        # 3. Task #15: Fetch from AKShare directly for utilities
        if dps is None:
            dps = _get_dividend_from_akshare(ticker)

        if dps and dps > 0:
            # DDM formula: P = D1 / (Ke - g)
            # D1 = DPS × (1 + g)
            d1 = dps * (1 + UTILITY_DDM_GROWTH)
            if UTILITY_COST_OF_EQUITY > UTILITY_DDM_GROWTH:
                ddm_utility_per_share = d1 / (UTILITY_COST_OF_EQUITY - UTILITY_DDM_GROWTH)
                results["ddm_per_share"] = round(ddm_utility_per_share, 2)
                results["dps"] = round(dps, 2)
                detail_lines.append(
                    f"DDM股息折现 (DPS=¥{dps:.2f}, g={UTILITY_DDM_GROWTH*100:.1f}%, "
                    f"Ke={UTILITY_COST_OF_EQUITY*100:.0f}%): ¥{ddm_utility_per_share:.2f}/股"
                )
        else:
            detail_lines.append("⚠ DDM估值无法计算（缺少股息数据，已尝试AKShare获取）")

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
        ca = _safe(balance_rows[0].get("current_assets"))
        tl = _safe(balance_rows[0].get("total_liabilities"))
        if ca and tl and shares and shares > 0:
            net_net_per_share = (ca - tl) / shares
            net_net_ratio = net_net_per_share / current_price if current_price else None
            results["net_net_per_share"] = net_net_per_share
            results["net_net_ratio"] = net_net_ratio
            detail_lines.append(
                f"Net-Net: (CA-TL)/股={net_net_per_share:.2f}, 价格比={net_net_ratio:.2f}"
                if net_net_ratio
                else f"Net-Net: {net_net_per_share:.2f}/股"
            )

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

            # Apply real estate P/B cap (Task 1.2)
            if is_real_estate_industry(industry):
                cap_result = apply_real_estate_cap(pb_target_per_share / bvps, industry)
                pb_target_per_share = cap_result["pb_capped"] * bvps
                if cap_result.get("warning"):
                    detail_lines.append(cap_result["warning"])

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

            # Apply real estate P/B cap (Task 1.2)
            if is_real_estate_industry(industry):
                cap_result = apply_real_estate_cap(pb_target_per_share / bvps, industry)
                pb_target_per_share = cap_result["pb_capped"] * bvps
                if cap_result.get("warning"):
                    detail_lines.append(cap_result["warning"])

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
                _pb = 1.5  # Conservative multiple for R&D stage
                pb_target_per_share = bvps * _pb

                # Apply real estate P/B cap (Task 1.2)
                if is_real_estate_industry(industry):
                    cap_result = apply_real_estate_cap(_pb, industry)
                    pb_target_per_share = cap_result["pb_capped"] * bvps
                    if cap_result.get("warning"):
                        detail_lines.append(cap_result["warning"])

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

    elif is_utility_stock:
        # Task #15: Utility stock valuation methods
        # Uses DDM as primary (stable dividends), DCF, and P/B
        # Disables high-growth assumptions (utilities have regulated, slow growth)

        # DDM valuation - primary method for utilities
        if ddm_utility_per_share:
            valuation_methods.append(("DDM", ddm_utility_per_share))

        # DCF with conservative growth (use bear case as more realistic for utilities)
        if dcf_bear and shares and shares > 0:
            dcf_per_share = dcf_bear / shares  # Use conservative DCF
            results["dcf_per_share"] = dcf_per_share
            valuation_methods.append(("DCF_Conservative", dcf_per_share))
            detail_lines.append(f"公用事业DCF（保守）: ¥{dcf_per_share:.2f}/股")

        # EV/EBITDA with utility multiple (12-15x) - OVERRIDE the generic calculation
        if ebitda and ebitda > 0 and shares and shares > 0:
            utility_ev_multiple = 12.0  # Utility sector median
            utility_ev = ebitda * utility_ev_multiple
            utility_ev_per_share = utility_ev / shares
            results["ev_ebitda_utility_per_share"] = round(utility_ev_per_share, 2)
            results["ev_ebitda_per_share"] = round(utility_ev_per_share, 2)  # Override generic
            results["ev_ebitda_multiple"] = utility_ev_multiple  # Override for report
            valuation_methods.append(("EV/EBITDA", utility_ev_per_share))
            # Fix #16: Add detail line with correct 12x multiple
            detail_lines.append(
                f"EV/EBITDA ({utility_ev_multiple:.0f}x 公用事业倍数): ¥{utility_ev_per_share:.2f}/股"
            )

        # P/B as floor value (utilities typically trade at 1.5-2.5x book)
        if bvps:
            utility_pb_multiple = 2.0  # Utility P/B multiple
            pb_target_per_share = bvps * utility_pb_multiple

            # Apply real estate P/B cap (Task 1.2)
            if is_real_estate_industry(industry):
                cap_result = apply_real_estate_cap(pb_target_per_share / bvps, industry)
                pb_target_per_share = cap_result["pb_capped"] * bvps
                if cap_result.get("warning"):
                    detail_lines.append(cap_result["warning"])

            results["pb_target"] = round(pb_target_per_share, 2)
            valuation_methods.append(("P/B", pb_target_per_share))
            detail_lines.append(
                f"P/B目标价 ({utility_pb_multiple}x BVPS=¥{bvps:.2f}): ¥{pb_target_per_share:.2f}/股"
            )

        # NOTE: Graham Number is DISABLED for utilities
        # (Graham Number is designed for undervalued stocks, not regulated utilities)

    elif is_brand_moat:
        # Task 3.1: Brand moat stock valuation methods
        # Uses P/E anchor (tier-based), DCF, disables Graham Number
        pe_anchor_per_share = None

        # P/E anchor valuation - primary method for brand moat stocks
        pe_range = BRAND_MOAT_PE_ANCHORS.get(brand_moat_tier, BRAND_MOAT_PE_ANCHORS["moderate"])[
            "pe_range"
        ]
        pe_mid = (pe_range[0] + pe_range[1]) / 2

        # Use the early-defined EPS for brand moat calculation (defined at moat detection)
        if eps_for_moat and eps_for_moat > 0:
            pe_anchor_per_share = eps_for_moat * pe_mid
            results["pe_anchor_per_share"] = round(pe_anchor_per_share, 2)
            results["pe_anchor_range"] = pe_range
            valuation_methods.append(("P/E_Moat", pe_anchor_per_share))
            detail_lines.append(
                f"护城河P/E锚点 ({pe_range[0]}-{pe_range[1]}x, 使用{pe_mid:.0f}x): ¥{pe_anchor_per_share:.2f}/股"
            )

        # DCF base case (per share) - secondary method
        if dcf_base and shares and shares > 0:
            dcf_per_share = dcf_base / shares
            results["dcf_per_share"] = dcf_per_share
            valuation_methods.append(("DCF", dcf_per_share))

        # EV/EBITDA per share - tertiary method
        if ev_ebitda_per_share:
            valuation_methods.append(("EV/EBITDA", ev_ebitda_per_share))

        # P/B as floor value only
        if bvps:
            _pb = 3.0  # Higher P/B for brand moat stocks
            pb_target_per_share = bvps * _pb
            results["pb_target"] = round(pb_target_per_share, 2)
            valuation_methods.append(("P/B", pb_target_per_share))
            detail_lines.append(
                f"P/B目标价 ({_pb}x BVPS=¥{bvps:.2f}): ¥{pb_target_per_share:.2f}/股"
            )

        # NOTE: Graham Number is DISABLED for brand moat stocks
        # (Graham designed for undervalued stocks, not premium brands)

    elif is_distressed:
        # Task 3.4: Distressed company valuation methods
        # Uses asset replacement value, net-net, EV/Sales based on distressed type

        # Get company info for distressed valuation
        company_info = {"name": ticker, "business_description": industry or ""}

        # Build full metrics for distressed valuation
        full_metrics = {
            "shares": shares,
            "revenue": revenue,
            "fixed_assets": _safe(balance_rows[0].get("fixed_assets")) if balance_rows else None,
            "inventory": _safe(balance_rows[0].get("inventory")) if balance_rows else None,
            "current_assets": _safe(balance_rows[0].get("current_assets"))
            if balance_rows
            else None,
            "total_liabilities": _safe(balance_rows[0].get("total_liabilities"))
            if balance_rows
            else None,
            "accounts_receivable": _safe(balance_rows[0].get("accounts_receivable"))
            if balance_rows
            else None,
            "order_backlog": None,  # Usually not available
            "gross_margin": gross_margin,
        }

        distressed_results = distressed_valuation(full_metrics, company_info)
        results["distressed_valuation"] = distressed_results

        # Asset replacement value (for asset_intensive type)
        if "asset_replacement" in distressed_results:
            asset_val = distressed_results["asset_replacement"]["value"]
            valuation_methods.append(("Asset_Replacement", asset_val))
            detail_lines.append(f"资产重置估值: ¥{asset_val:.2f}/股")

        # EV/Sales (generic distressed or reference)
        if "ev_sales" in distressed_results or "ev_sales_ref" in distressed_results:
            ev_sales_data = distressed_results.get("ev_sales") or distressed_results.get(
                "ev_sales_ref"
            )
            if ev_sales_data and "value" in ev_sales_data:
                ev_sales_val = ev_sales_data["value"]
                valuation_methods.append(("EV/Sales_Distressed", ev_sales_val))
                detail_lines.append(
                    f"困境EV/Sales ({ev_sales_data.get('multiple', 0.3)}x): ¥{ev_sales_val:.2f}/股"
                )

        # Net-Net value (Graham's net-net for deep value)
        if "net_net" in distressed_results:
            net_net_val = distressed_results["net_net"]["value"]
            valuation_methods.append(("Net_Net", net_net_val))
            detail_lines.append(f"Net-Net价值: ¥{net_net_val:.2f}/股")

        # P/B at distressed multiple (0.5-0.7x)
        if bvps and bvps > 0:
            distressed_pb = 0.5
            pb_distressed = bvps * distressed_pb
            results["pb_target"] = round(pb_distressed, 2)
            valuation_methods.append(("P/B_Distressed", pb_distressed))
            detail_lines.append(f"困境P/B目标价 ({distressed_pb}x): ¥{pb_distressed:.2f}/股")

        # NOTE: Graham Number and standard DCF are NOT applicable for distressed
        # (Earnings-based methods fail when earnings are negative)

    else:
        # Standard valuation methods for traditional value stocks
        # P1-4 FIX: Check disabled_methods_from_profile before adding each method

        # DCF base case (per share)
        if dcf_base and shares and shares > 0:
            dcf_per_share = dcf_base / shares
            results["dcf_per_share"] = dcf_per_share
            # Check if DCF is disabled for this industry
            if "dcf" not in [m.lower() for m in disabled_methods_from_profile]:
                valuation_methods.append(("DCF", dcf_per_share))
            else:
                detail_lines.append(f"⚠ DCF: 已禁用 (行业配置: {industry_class})")

        # Graham Number - only for traditional value stocks
        if graham_number_per_share:
            # Check if Graham Number is disabled for this industry
            if "graham_number" not in [m.lower() for m in disabled_methods_from_profile]:
                valuation_methods.append(("Graham", graham_number_per_share))
            else:
                detail_lines.append(f"⚠ Graham Number: 已禁用 (行业配置: {industry_class})")

        # EV/EBITDA per share - only for profitable companies
        if ev_ebitda_per_share:
            # Check if EV/EBITDA is disabled for this industry
            if "ev_ebitda" not in [m.lower() for m in disabled_methods_from_profile]:
                valuation_methods.append(("EV/EBITDA", ev_ebitda_per_share))
            else:
                detail_lines.append(f"⚠ EV/EBITDA: 已禁用 (行业配置: {industry_class})")

        # P/B target per share
        if bvps:
            _pb = PB_TARGET_OIL_SERVICES if _is_oil else PB_TARGET_DEFAULT
            pb_target_per_share = bvps * _pb

            # Apply real estate P/B cap (Task 1.2)
            if is_real_estate_industry(industry):
                cap_result = apply_real_estate_cap(pb_target_per_share / bvps, industry)
                pb_target_per_share = cap_result["pb_capped"] * bvps
                if cap_result.get("warning"):
                    detail_lines.append(cap_result["warning"])

            # Only add to results if not already added in EV/EBITDA section
            if "pb_target" not in results:
                results["pb_target"] = round(pb_target_per_share, 2)

            # Check if P/B is disabled for this industry
            if "pb" not in [m.lower() for m in disabled_methods_from_profile]:
                valuation_methods.append(("P/B", pb_target_per_share))
            else:
                detail_lines.append(f"⚠ P/B: 已禁用 (行业配置: {industry_class})")

    # Validate each method and calculate weighted target
    validated_results = []
    all_target_prices = [price for _, price in valuation_methods if price and price > 0]

    # Task 3.5: Get industry type for outlier threshold lookup
    industry_type_for_threshold = get_industry_type("", industry or "") if industry else "default"

    for method_name, target_price in valuation_methods:
        validation = _validate_valuation_result(
            method_name=method_name,
            target_price=target_price,
            current_price=current_price or 0,
            all_results=all_target_prices,
            industry_type=industry_type_for_threshold,
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
            logger.info(
                "[Valuation] %s: Using healthcare R&D stage weights: %s", ticker, default_weights
            )
        else:
            valuation_config = get_healthcare_mature_valuation_config()
            default_weights = valuation_config["weights"]
            logger.info(
                "[Valuation] %s: Using healthcare mature stage weights: %s", ticker, default_weights
            )
    elif is_utility_stock:
        # Task #15: Use utility stock weights (DDM-focused for stable dividend payers)
        default_weights = {
            "DDM": 0.50,  # Primary - utilities have stable, predictable dividends
            "DCF_Conservative": 0.20,  # Secondary - conservative growth assumptions
            "EV/EBITDA": 0.20,  # Tertiary - regulated asset base
            "P/B": 0.10,  # Floor value
        }
        logger.info("[Valuation] %s: Using utility stock weights: %s", ticker, default_weights)
    elif is_brand_moat:
        # Task 3.1: Use brand moat stock weights (P/E anchor focused, no Graham)
        default_weights = {
            "P/E_Moat": 0.50,  # Primary - P/E anchor based on moat tier
            "DCF": 0.30,  # Secondary - supports high valuation
            "EV/EBITDA": 0.15,  # Tertiary - cross-check
            "P/B": 0.05,  # Floor value only
        }
        logger.info("[Valuation] %s: Using brand moat stock weights: %s", ticker, default_weights)
    elif is_distressed:
        # Task 3.4: Use distressed company weights based on type
        if distressed_type == "asset_intensive":
            default_weights = {
                "Asset_Replacement": 0.50,  # Primary - replacement value
                "Net_Net": 0.30,  # Secondary - Graham deep value
                "P/B_Distressed": 0.20,  # Tertiary - floor value
            }
        elif distressed_type in ["contract_based", "receivables_heavy"]:
            default_weights = {
                "EV/Sales_Distressed": 0.40,  # Primary - revenue-based
                "Net_Net": 0.35,  # Secondary - deep value
                "P/B_Distressed": 0.25,  # Tertiary - floor value
            }
        else:  # generic_distressed
            default_weights = {
                "EV/Sales_Distressed": 0.40,  # Primary - generic EV/Sales
                "Net_Net": 0.30,  # Secondary - deep value
                "P/B_Distressed": 0.30,  # Tertiary - book value
            }
        logger.info(
            "[Valuation] %s: Using distressed stock weights (%s): %s",
            ticker,
            distressed_type,
            default_weights,
        )
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
        results=validated_results, current_price=current_price or 0, weights=default_weights
    )

    # Store validation results in metrics
    results["validation"] = {
        "validated_methods": [
            {
                "method": v["method"],
                "target_price": v["target_price"],
                "valid": v["valid"],
                "excluded": v["exclude_from_weighted"],
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
            detail_lines.append(
                f"安全边际 (单一方法): {mos_pct:.1f}% (目标¥{weighted_target:.2f} vs 市价¥{current_price:.2f})"
            )
        else:
            valid_method_list = ", ".join(weighted_result["valid_methods"])
            detail_lines.append(
                f"\n✓ 加权目标价: ¥{weighted_target:.2f} (基于 {len(weighted_result['valid_methods'])} 个有效方法: {valid_method_list})"
            )
            if weighted_result["excluded_methods"]:
                excluded_list = ", ".join(weighted_result["excluded_methods"])
                detail_lines.append(f"  已排除异常值: {excluded_list}")
            detail_lines.append(
                f"安全边际 (加权): {mos_pct:.1f}% (目标¥{weighted_target:.2f} vs 市价¥{current_price:.2f})"
            )
    elif not weighted_target and current_price:
        # Degraded mode with 0 valid methods
        if weighted_result.get("warning"):
            detail_lines.append(f"\n{weighted_result['warning']}")
        detail_lines.append("⚠ 所有估值方法均被排除，无法计算目标价")

    # P2-1: Get industry valuation positioning
    industry_position = _get_industry_position_safe(ticker)
    if industry_position:
        results["industry_position"] = industry_position
        detail_lines.append(f"\n【行业估值定位】")
        detail_lines.append(f"  行业: {industry_position.get('industry', '未知')}")
        detail_lines.append(f"  PE分位: {industry_position.get('pe_percentile', 'N/A')}%")
        detail_lines.append(f"  PB分位: {industry_position.get('pb_percentile', 'N/A')}%")
        detail_lines.append(f"  行业PE中位数: {industry_position.get('industry_pe_median', 'N/A')}")
        detail_lines.append(f"  同业比较样本: {industry_position.get('peer_count', 0)}家")

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
            confidence = min(
                0.90, (0.55 + abs(margin_of_safety) * 0.5) * base_confidence_multiplier
            )

        # Override with degraded confidence if applicable
        if weighted_result["degraded"]:
            confidence = min(confidence, weighted_result["confidence"])
    else:
        signal, confidence = "neutral", 0.30
        if not weighted_target:
            confidence = 0.25  # Very low confidence when no valid methods
        detail_lines.append("⚠ 估值数据不足，保持中性")

    reasoning = f"估值分析结果（{primary_method}为主要依据）：\n" + "\n".join(detail_lines)

    # ── 6. Optional LLM interpretation ────────────────────────────────────────
    if use_llm:
        try:
            from src.llm.router import call_llm, LLMError
            from src.llm.prompts import (
                VALUATION_INTERPRET_SYSTEM_PROMPT,
                VALUATION_INTERPRET_USER_TEMPLATE,
            )

            # Format validation context for LLM
            valid_methods_str = (
                ", ".join(weighted_result["valid_methods"])
                if weighted_result["valid_methods"]
                else "无"
            )
            excluded_methods_str = (
                ", ".join(weighted_result["excluded_methods"])
                if weighted_result["excluded_methods"]
                else "无"
            )
            weighted_target_str = f"¥{weighted_target:.2f}" if weighted_target else "N/A"
            validation_mode = (
                "降级模式（≤1个有效方法）" if weighted_result["degraded"] else "正常模式"
            )

            # Task #19: Build detailed method lists for LLM to reference correctly
            valuation_mode = results.get("valuation_mode", "standard")
            valuation_mode_display = {
                "financial": "金融股（P/B-ROE + DDM + P/E）",
                "utility": "公用事业股（DDM为主）",
                "growth_stock": "成长股（PEG + DCF）",
                "loss_making_tech": "亏损期科技股（PS + EV/Sales）",
                "cyclical": "周期股（正常化DCF + 周期底部倍数）",
                "healthcare_rd": "研发期医药股（PS + EV/Sales）",
                "healthcare_mature": "成熟期医药股（PE + DCF）",
                "standard": "传统价值股（DCF + Graham + EV/EBITDA）",
            }.get(valuation_mode, valuation_mode)

            # Build valid methods details
            valid_methods_details_list = []
            for v in validated_results:
                if not v.get("exclude_from_weighted", True):
                    valid_methods_details_list.append(
                        f"- {v['method']}: ¥{v['target_price']:.2f}/股"
                    )
            valid_methods_details = (
                "\n".join(valid_methods_details_list)
                if valid_methods_details_list
                else "无有效方法"
            )

            # Build excluded/not-applicable methods
            excluded_details_list = []
            for v in validated_results:
                if v.get("exclude_from_weighted", True):
                    excluded_details_list.append(f"- {v['method']}: 已排除")
            # Add methods that were never calculated for this stock type
            if valuation_mode == "financial":
                excluded_details_list.append("- DCF: 不适用于金融股")
                excluded_details_list.append("- Graham Number: 不适用于金融股")
            elif valuation_mode == "utility":
                excluded_details_list.append("- Graham Number: 不适用于公用事业股")
            excluded_methods_details = (
                "\n".join(excluded_details_list) if excluded_details_list else "无排除方法"
            )

            user_msg = VALUATION_INTERPRET_USER_TEMPLATE.format(
                ticker=ticker,
                current_price=f"¥{current_price:.2f}" if current_price else "未知",
                valuation_mode=valuation_mode_display,
                valid_methods_details=valid_methods_details,
                excluded_methods_details=excluded_methods_details,
                wacc=wacc * 100,
                terminal_growth=TERMINAL_GROWTH * 100,
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
                iv_low = parsed.get("intrinsic_value_range_low", "")
                iv_high = parsed.get("intrinsic_value_range_high", "")
                method = parsed.get("most_relevant_method", "")
                prose = parsed.get("reasoning", "")
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
    logger.info(
        "[Valuation] %s: signal=%s confidence=%.2f mos=%s",
        ticker,
        signal,
        confidence,
        f"{margin_of_safety*100:.1f}%" if margin_of_safety else "N/A",
    )
    return agent_signal
