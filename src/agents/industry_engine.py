"""V3.0 Industry Engine — three-layer funnel for valuation config routing.

Architecture:
1. Hard Rules (zero-cost): Bank, Insurance, Real Estate, Distressed, Brand Moat, Pharma
2. LLM Dynamic Routing (cached): DeepSeek-Reasoner with method_importance scoring
3. Safe Fallback (never-fail): Generic regime with balanced weights

Usage:
    from src.agents.industry_engine import get_valuation_config

    config = get_valuation_config(ticker, company_info, metrics)
"""

from dataclasses import dataclass

from src.agents.valuation_config import ValuationConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Special Regime Configurations ────────────────────────────────────────────

SPECIAL_REGIME_CONFIGS = {
    "bank": {
        "primary_methods": ["pb_roe", "ddm"],
        "weights": {"pb_roe": 0.6, "ddm": 0.4},
        "disabled_methods": ["ev_ebitda", "graham_number", "dcf"],
        "exempt_scoring_metrics": ["debt_equity", "current_ratio", "fcf_ni"],
        "scoring_mode": "financial",
    },
    "insurance": {
        "primary_methods": ["pb_roe", "ddm", "pe"],
        "weights": {"pb_roe": 0.4, "ddm": 0.35, "pe": 0.25},
        "disabled_methods": ["ev_ebitda", "graham_number"],
        "exempt_scoring_metrics": ["debt_equity", "current_ratio", "fcf_ni"],
        "scoring_mode": "financial",
    },
    "real_estate": {
        "primary_methods": ["pb"],
        "weights": {"pb": 1.0},
        "disabled_methods": ["graham_number", "ev_ebitda", "dcf"],
        "scoring_mode": "standard",
        "pb_multiple_cap": 0.5,  # Cap P/B at 0.5 for distressed real estate
    },
    "distressed": {
        "primary_methods": ["ev_sales", "net_net", "asset_replacement"],
        "weights": {"ev_sales": 0.5, "net_net": 0.3, "asset_replacement": 0.2},
        "disabled_methods": ["pe", "graham_number", "dcf", "pb_roe"],
        "scoring_mode": "distressed",
    },
    "brand_moat": {
        "primary_methods": ["pe_moat", "dcf", "ev_ebitda"],
        "weights": {"pe_moat": 0.5, "dcf": 0.3, "ev_ebitda": 0.2},
        "disabled_methods": ["graham_number"],
        "ev_ebitda_multiple_range": (15.0, 25.0),
    },
    "pharma_innovative": {
        "primary_methods": ["ps", "ev_sales"],
        "weights": {"ps": 0.6, "ev_sales": 0.4},
        "disabled_methods": ["pe", "graham_number", "ev_ebitda"],
        "exempt_scoring_metrics": ["fcf_ni", "net_margin"],
    },
}

PIPELINE_KEYWORDS = [
    "临床",
    "管线",
    "适应症",
    "pipeline",
    "IND",
    "NDA",
    "FDA",
    "NMPA",
    "BLA",
    "一期",
    "二期",
    "三期",
    "Phase",
    "创新药",
    "生物药",
    "单抗",
    "双抗",
]


@dataclass
class SpecialRegimeResult:
    """Result from hard rule detection."""

    regime: str
    confidence: float
    triggered_rules: list[str]
    rationale: str


def detect_special_regime(
    metrics: dict,
    company_info: dict,
) -> SpecialRegimeResult | None:
    """
    Layer 1: Hard rule detection for special regimes.

    Returns SpecialRegimeResult if a hard rule matches, None otherwise.
    """
    # Extract common metrics with safe defaults
    de_ratio = metrics.get("de_ratio") or 0
    has_loan_loss = metrics.get("has_loan_loss_provision", False)
    has_insurance = metrics.get("has_insurance_reserve", False)
    is_financial = has_loan_loss or has_insurance

    total_assets = metrics.get("total_assets") or 1
    inventory = metrics.get("inventory") or 0
    advance = metrics.get("advance_receipts") or 0
    fixed_assets = metrics.get("fixed_assets") or 0

    inventory_ratio = inventory / total_assets if total_assets > 0 else 0
    advance_ratio = advance / total_assets if total_assets > 0 else 0
    fixed_assets_ratio = fixed_assets / total_assets if total_assets > 0 else 0

    gross_margin = metrics.get("gross_margin") or 0
    roe_5yr = metrics.get("roe_5yr_avg") or 0
    fcf_years = metrics.get("fcf_positive_years") or 0
    rd_ratio = metrics.get("rd_expense_ratio") or 0
    net_margin = metrics.get("net_margin") or 0

    # Rule priority: Bank > Insurance > Real Estate > Distressed > Brand Moat > Pharma

    # Rule 1: Bank (DE > 8 AND has_loan_loss_provision)
    if de_ratio > 8 and has_loan_loss:
        return SpecialRegimeResult(
            regime="bank",
            confidence=0.95,
            triggered_rules=["de_ratio > 8", "has_loan_loss_provision"],
            rationale=f"DE={de_ratio:.1f}x with loan loss provisions",
        )

    # Rule 2: Insurance (DE > 4 AND has_insurance_reserve)
    if de_ratio > 4 and has_insurance:
        return SpecialRegimeResult(
            regime="insurance",
            confidence=0.92,
            triggered_rules=["de_ratio > 4", "has_insurance_reserve"],
            rationale=f"DE={de_ratio:.1f}x with insurance reserves",
        )

    # Rule 3: Real Estate (inventory > 40% AND advance > 10% AND fixed_assets < 10% AND NOT financial)
    if (
        inventory_ratio > 0.40
        and advance_ratio > 0.10
        and fixed_assets_ratio < 0.10
        and not is_financial
    ):
        return SpecialRegimeResult(
            regime="real_estate",
            confidence=0.90,
            triggered_rules=[
                f"inventory_ratio={inventory_ratio:.1%}",
                f"advance_ratio={advance_ratio:.1%}",
                f"fixed_assets_ratio={fixed_assets_ratio:.1%} (asset-light)",
            ],
            rationale="High inventory + advance receipts with light fixed assets (developer pattern)",
        )

    # Rule 4: Distressed (negative margins or ROE for 2+ years)
    margin_3yr = metrics.get("net_margin_3yr_avg")
    roe_3yr = metrics.get("roe_3yr_avg")
    loss_years = metrics.get("consecutive_loss_years") or 0
    if not is_financial and loss_years >= 2:
        if (margin_3yr is not None and margin_3yr < -10) or (
            roe_3yr is not None and roe_3yr < -10
        ):
            return SpecialRegimeResult(
                regime="distressed",
                confidence=0.85,
                triggered_rules=[
                    f"loss_years={loss_years}",
                    f"margin_3yr={margin_3yr}" if margin_3yr else "",
                ],
                rationale="Consecutive losses with negative profitability trend",
            )

    # Rule 5: Brand Moat (gross_margin > 70% AND roe_5yr > 18% AND fcf_positive >= 4)
    if gross_margin > 70 and roe_5yr > 18 and fcf_years >= 4 and not is_financial:
        return SpecialRegimeResult(
            regime="brand_moat",
            confidence=0.88,
            triggered_rules=[
                f"gross_margin={gross_margin:.1f}%",
                f"roe_5yr={roe_5yr:.1f}%",
                f"fcf_years={fcf_years}",
            ],
            rationale="Consistently high margins and returns indicate durable brand moat",
        )

    # Rule 6: Pharma Innovative (rd_ratio > 30% AND net_margin < 5% AND pipeline keywords)
    business_desc = company_info.get("business_description", "")
    has_pipeline = any(kw in business_desc for kw in PIPELINE_KEYWORDS)
    if rd_ratio > 30 and net_margin < 5 and has_pipeline and not is_financial:
        return SpecialRegimeResult(
            regime="pharma_innovative",
            confidence=0.82,
            triggered_rules=[
                f"rd_ratio={rd_ratio:.1f}%",
                f"net_margin={net_margin:.1f}%",
                "has_pipeline_keywords",
            ],
            rationale="High R&D with low profits and pipeline keywords suggests innovative pharma",
        )

    return None


def _build_valuation_config_from_regime(
    regime: str,
    confidence: float,
    triggered_rules: list[str],
    rationale: str,
) -> ValuationConfig:
    """Build ValuationConfig from a detected special regime."""
    config_data = SPECIAL_REGIME_CONFIGS.get(regime, {})
    return ValuationConfig(
        regime=regime,
        primary_methods=config_data.get("primary_methods", ["ev_ebitda", "pe", "pb"]),
        weights=config_data.get("weights", {}),
        disabled_methods=config_data.get("disabled_methods", []),
        exempt_scoring_metrics=config_data.get("exempt_scoring_metrics", []),
        scoring_mode=config_data.get("scoring_mode", "standard"),
        ev_ebitda_multiple_range=config_data.get("ev_ebitda_multiple_range", (8.0, 12.0)),
        pb_multiple_cap=config_data.get("pb_multiple_cap"),  # For real_estate
        confidence=confidence,
        source="hard_rule",
        rationale=rationale,
        triggered_rules=triggered_rules,
    )
