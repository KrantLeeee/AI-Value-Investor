"""V3.0 Industry Engine — three-layer funnel for valuation config routing.

Architecture:
1. Hard Rules (zero-cost): Bank, Insurance, Real Estate, Distressed, Brand Moat, Pharma
2. LLM Dynamic Routing (cached): DeepSeek-Reasoner with method_importance scoring
3. Safe Fallback (never-fail): Generic regime with balanced weights

Usage:
    from src.agents.industry_engine import get_valuation_config

    config = get_valuation_config(ticker, company_info, metrics)
"""

import hashlib
import json
import re
from dataclasses import dataclass

from src.agents.valuation_config import ValuationConfig
from src.llm.prompts import (
    INDUSTRY_ROUTING_SYSTEM_PROMPT,
    INDUSTRY_ROUTING_USER_PROMPT_TEMPLATE,
)
from src.llm.router import LLMError, call_llm
from src.utils.config import get_output_dir
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


# ── Layer 2: LLM Dynamic Routing ─────────────────────────────────────────────


def extract_json_from_llm_output(raw_output: str) -> dict:
    """
    Extract JSON from LLM output, handling DeepSeek's <think> blocks.

    Strategies:
    1. Remove <think>...</think> blocks
    2. Try to extract from ```json...``` code block
    3. Fallback to extracting bare JSON object
    4. Raise ValueError if all fail
    """
    # Step 1: Remove <think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", raw_output, flags=re.DOTALL)

    # Step 2: Try markdown code block
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Step 3: Try bare JSON object
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from LLM output: {raw_output[:200]}")


def _get_cache_key(stock_code: str, report_period: str) -> str:
    """Generate cache key from stock code and report period."""
    raw = f"{stock_code}:{report_period}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached_config(cache_key: str) -> ValuationConfig | None:
    """Try to load cached ValuationConfig."""
    cache_dir = get_output_dir("industry_cache")
    cache_file = cache_dir / f"{cache_key}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return ValuationConfig(**data)
        except Exception as e:
            logger.warning("[IndustryEngine] Cache read failed: %s", e)
    return None


def _save_to_cache(cache_key: str, config: ValuationConfig) -> None:
    """Save ValuationConfig to cache."""
    cache_dir = get_output_dir("industry_cache")
    cache_file = cache_dir / f"{cache_key}.json"
    try:
        cache_file.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[IndustryEngine] Cache write failed: %s", e)


def _call_llm_for_routing(company_info: dict, metrics: dict) -> ValuationConfig | None:
    """
    Layer 2: LLM dynamic routing.

    Calls DeepSeek-Reasoner to determine the best valuation framework.
    Returns ValuationConfig on success, None on failure (falls through to Layer 3).
    """
    # Build user prompt
    prompt_vars = {
        "name": company_info.get("name", "Unknown"),
        "industry": company_info.get("industry", "Unknown"),
        "business_description": company_info.get("business_description", ""),
        "gross_margin": metrics.get("gross_margin") or 0,
        "net_margin": metrics.get("net_margin") or 0,
        "roe": metrics.get("roe") or 0,
        "rd_expense_ratio": metrics.get("rd_expense_ratio") or 0,
        "de_ratio": metrics.get("de_ratio") or 0,
        "revenue_growth": metrics.get("revenue_growth") or 0,
        "net_income_growth": metrics.get("net_income_growth") or 0,
        "fcf_positive_years": metrics.get("fcf_positive_years") or 0,
    }

    try:
        user_prompt = INDUSTRY_ROUTING_USER_PROMPT_TEMPLATE.format(**prompt_vars)
        raw_output = call_llm(
            task="industry_routing",
            system_prompt=INDUSTRY_ROUTING_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        # Extract and parse JSON
        parsed = extract_json_from_llm_output(raw_output)

        # Build ValuationConfig
        config = ValuationConfig(
            regime=parsed.get("regime", "llm_generic"),
            primary_methods=parsed.get("primary_methods", ["ev_ebitda", "pe"]),
            method_importance=parsed.get("method_importance", {}),
            disabled_methods=parsed.get("disabled_methods", []),
            scoring_mode=parsed.get("scoring_mode", "standard"),
            ev_ebitda_multiple_range=tuple(
                parsed.get("ev_ebitda_multiple_range", [8.0, 12.0])
            ),
            confidence=0.75,
            source="llm",
            rationale=parsed.get("rationale", ""),
        )

        logger.info(
            "[IndustryEngine] LLM routing: regime=%s, methods=%s",
            config.regime,
            config.primary_methods,
        )
        return config

    except LLMError as e:
        logger.warning("[IndustryEngine] LLM call failed: %s", e)
        return None
    except ValueError as e:
        logger.warning("[IndustryEngine] JSON extraction failed: %s", e)
        return None
    except Exception as e:
        logger.warning("[IndustryEngine] LLM routing error: %s", e)
        return None


# ── Fallback Configuration ───────────────────────────────────────────────────


def get_fallback_config() -> ValuationConfig:
    """
    Layer 3: Safe fallback — always returns valid config.

    Used when:
    - No hard rule matches
    - LLM call fails or returns invalid data
    - Cache miss and LLM disabled
    """
    return ValuationConfig(
        regime="generic",
        primary_methods=["ev_ebitda", "pe", "pb"],
        weights={"ev_ebitda": 0.4, "pe": 0.35, "pb": 0.25},
        confidence=0.40,
        source="fallback",
        rationale="No special regime detected, using balanced generic valuation",
    )


# ── Unified Entry Point ──────────────────────────────────────────────────────


def get_valuation_config(
    ticker: str,
    company_info: dict,
    metrics: dict,
    *,
    skip_llm: bool = False,
) -> ValuationConfig:
    """
    Unified entry point for the three-layer industry engine.

    Args:
        ticker: Stock ticker (e.g., "601398.SH")
        company_info: Dict with name, industry, business_description
        metrics: Dict with financial metrics
        skip_llm: If True, skip LLM layer and fall through to fallback

    Returns:
        ValuationConfig with regime, methods, weights, confidence, source
    """
    # Layer 1: Hard Rules
    hard_result = detect_special_regime(metrics, company_info)
    if hard_result:
        config = _build_valuation_config_from_regime(
            regime=hard_result.regime,
            confidence=hard_result.confidence,
            triggered_rules=hard_result.triggered_rules,
            rationale=hard_result.rationale,
        )
        logger.info(
            "[IndustryEngine] %s: regime=%s, source=hard_rule, confidence=%.2f",
            ticker,
            config.regime,
            config.confidence,
        )
        return config

    # Layer 2: LLM Dynamic Routing (with cache)
    if not skip_llm:
        report_period = metrics.get("report_period", "unknown")
        cache_key = _get_cache_key(ticker, report_period)

        # Check cache first
        cached = _get_cached_config(cache_key)
        if cached:
            logger.info(
                "[IndustryEngine] %s: regime=%s, source=cache",
                ticker,
                cached.regime,
            )
            return cached

        # Call LLM
        llm_config = _call_llm_for_routing(company_info, metrics)
        if llm_config:
            _save_to_cache(cache_key, llm_config)
            logger.info(
                "[IndustryEngine] %s: regime=%s, source=llm, confidence=%.2f",
                ticker,
                llm_config.regime,
                llm_config.confidence,
            )
            return llm_config

    # Layer 3: Fallback
    fallback = get_fallback_config()
    logger.info(
        "[IndustryEngine] %s: regime=%s, source=fallback",
        ticker,
        fallback.regime,
    )
    return fallback
