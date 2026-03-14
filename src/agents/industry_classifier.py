"""Industry Classification and Profile Management.

Classifies stocks by industry and retrieves industry-specific:
- Agent weights
- Fundamentals scoring thresholds
- Rationale for weight distribution

Based on PROJECT_ROADMAP.md P1-⑤
"""

import yaml
from pathlib import Path
from typing import TypedDict
from dataclasses import dataclass
from typing import Dict, List

from src.utils.config import get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class IndustryClassificationResult:
    """Result of industry classification with confidence scoring."""

    industry_type: str  # Industry type code
    display_name: str  # Display name
    confidence: float  # 0.0-1.0
    confidence_factors: Dict  # Confidence breakdown
    conservative_mode: bool  # Whether conservative mode triggered
    classification_path: List  # Decision path for debugging


class IndustryWeights(TypedDict):
    """Agent weight distribution for an industry."""

    fundamentals: float
    valuation: float
    warren_buffett: float
    ben_graham: float
    sentiment: float


class IndustryScoring(TypedDict):
    """Fundamentals scoring thresholds for an industry."""

    roe_thresholds: list[float]
    net_margin_thresholds: list[float]
    de_thresholds: list[float]
    growth_weight: float
    cash_quality_weight: float


class IndustryProfile(TypedDict):
    """Complete industry profile."""

    weights: IndustryWeights
    rationale: str
    validated: bool
    scoring: IndustryScoring


_PROFILES_CACHE: dict[str, IndustryProfile] | None = None
_KEYWORDS_CACHE: dict[str, list[str]] | None = None

# BUG-03: Priority keywords checked first to avoid misclassification
# (e.g., 宁德时代 should be new_energy_mfg, not pharma)
PRIORITY_KEYWORDS = {
    # Order matters: more specific keywords should be checked first
    "auto_new_energy": ["新能源汽车", "电动汽车", "纯电动", "整车制造"],
    "new_energy_mfg": ["锂电", "动力电池", "储能", "光伏", "风电", "电芯"],
    # Note: "新能源" alone is too generic, removed to avoid misclassification
}

# Task 2.3: Industry classification confidence mechanism
CONFIDENCE_THRESHOLD = 0.5  # Conservative mode threshold

INDUSTRY_KEYWORDS = {
    "bank": {
        "primary": ["银行", "商业银行"],
        "secondary": ["信贷", "存贷"],
        "negative": ["投资银行"],
    },
    "insurance": {
        "primary": ["保险", "人寿", "财险", "再保险"],
        "secondary": ["保费", "承保"],
        "negative": ["保险经纪"],
    },
    "new_energy_mfg": {
        "primary": ["锂电池", "动力电池", "储能电池", "新能源"],
        "secondary": ["锂电", "电芯", "正极", "负极", "隔膜"],
        "negative": ["新能源汽车"],
    },
    "auto_new_energy": {
        "primary": ["新能源汽车", "电动汽车", "纯电动"],
        "secondary": ["整车", "造车"],
        "negative": [],
    },
    "cyclical_materials": {
        "primary": ["钢铁", "水泥", "铝业", "铜业", "化工"],
        "secondary": ["冶炼", "矿业", "有色"],
        "negative": [],
    },
    "defense_equipment": {
        "primary": ["航空发动机", "军工", "国防", "航天"],
        "secondary": ["导弹", "舰船", "雷达"],
        "negative": [],
    },
    "telecom_operator": {
        "primary": ["电信", "移动通信", "运营商"],
        "secondary": ["基站", "5G网络"],
        "negative": ["设备"],
    },
    "telecom_equipment": {
        "primary": ["通信设备", "网络设备", "基站设备"],
        "secondary": ["交换机", "路由器"],
        "negative": [],
    },
    "low_margin_mfg": {
        "primary": ["代工", "ODM", "OEM", "电子制造"],
        "secondary": ["组装", "精密制造"],
        "negative": [],
    },
    "real_estate": {
        "primary": ["房地产", "地产", "房产开发"],
        "secondary": ["商业地产", "住宅开发"],
        "negative": [],
    },
}


def _load_profiles() -> tuple[dict[str, IndustryProfile], dict[str, list[str]]]:
    """Load industry profiles from YAML config.

    Supports both v2.1 format (industries key) and legacy format (industry_profiles key).
    """
    global _PROFILES_CACHE, _KEYWORDS_CACHE

    if _PROFILES_CACHE is not None and _KEYWORDS_CACHE is not None:
        return _PROFILES_CACHE, _KEYWORDS_CACHE

    config_path = get_project_root() / "config" / "industry_profiles.yaml"

    # Default profile template for v2.1 format conversion
    default_weights = {
        "fundamentals": 0.25,
        "valuation": 0.25,
        "warren_buffett": 0.20,
        "ben_graham": 0.15,
        "sentiment": 0.15,
    }
    default_scoring = {
        "roe_thresholds": [20, 15, 10],
        "net_margin_thresholds": [15, 10, 5],
        "de_thresholds": [0.5, 1.0, 1.5],
        "growth_weight": 0.25,
        "cash_quality_weight": 0.25,
    }

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Support v2.1 format (industries key)
        if "industries" in config:
            _PROFILES_CACHE = {}
            _KEYWORDS_CACHE = {}

            for industry_key, industry_data in config["industries"].items():
                # Convert v2.1 format to IndustryProfile format
                profile: IndustryProfile = {
                    "weights": industry_data.get("method_weights", default_weights),
                    "rationale": industry_data.get("display_name", industry_key),
                    "validated": industry_data.get("confidence", 0.5) >= 0.8,
                    "scoring": default_scoring.copy(),
                    # Store v2.1 specific fields
                    "display_name": industry_data.get("display_name", industry_key),
                    "confidence": industry_data.get("confidence", 0.5),
                    "methods": industry_data.get("methods", []),
                    "ev_ebitda_multiple": industry_data.get("ev_ebitda_multiple"),
                    "exempt_metrics": industry_data.get("exempt_metrics", []),
                    "disable_methods": industry_data.get("disable_methods", []),
                    "scoring_mode": industry_data.get("scoring_mode"),
                    "comparables": industry_data.get("example_companies", []),
                }
                _PROFILES_CACHE[industry_key] = profile

                # Build keywords cache from applies_to if present
                applies_to = industry_data.get("applies_to", [])
                if applies_to:
                    _KEYWORDS_CACHE[industry_key] = applies_to

            logger.info(f"[Industry] Loaded {len(_PROFILES_CACHE)} industry profiles (v2.1 format)")
            return _PROFILES_CACHE, _KEYWORDS_CACHE

        # Legacy format support
        _PROFILES_CACHE = config["industry_profiles"]
        _KEYWORDS_CACHE = config["industry_keywords"]

        logger.info(f"[Industry] Loaded {len(_PROFILES_CACHE)} industry profiles")
        return _PROFILES_CACHE, _KEYWORDS_CACHE

    except Exception as e:
        logger.error(f"[Industry] Failed to load profiles: {e}")
        # Return minimal default
        default_profile: IndustryProfile = {
            "weights": default_weights,
            "rationale": "Default fallback",
            "validated": False,
            "scoring": default_scoring,
        }
        _PROFILES_CACHE = {"default": default_profile}
        _KEYWORDS_CACHE = {}
        return _PROFILES_CACHE, _KEYWORDS_CACHE


def match_keywords(company_name: str, business_desc: str, akshare_industry: str) -> tuple:
    """
    Match keywords to determine industry type.

    Returns:
        Tuple of (industry_type, confidence_score)

    Confidence scoring (v1.2 revised):
    - Primary keyword in company name: +0.45
    - Primary keyword in business desc: +0.40
    - Secondary keyword in company name: +0.30
    - Secondary keyword in business desc: +0.25
    - AKShare industry match: +0.20
    """
    combined_text = company_name + business_desc

    best_match = None
    best_score = 0.0

    for industry_type, keywords in INDUSTRY_KEYWORDS.items():
        # Check negative keywords first
        if any(neg in combined_text for neg in keywords.get("negative", [])):
            continue

        score = 0.0

        # Primary keyword matching (raised weights)
        if any(kw in company_name for kw in keywords["primary"]):
            score = 0.45  # Company name primary → 0.45
        elif any(kw in business_desc for kw in keywords["primary"]):
            score = 0.40  # Business desc primary → 0.40
        elif any(kw in company_name for kw in keywords.get("secondary", [])):
            score = 0.30  # Company name secondary → 0.30
        elif any(kw in business_desc for kw in keywords.get("secondary", [])):
            score = 0.25  # Business desc secondary → 0.25

        # Check AKShare industry match (additional confidence boost)
        if akshare_industry and any(
            kw in akshare_industry for kw in keywords["primary"] + keywords.get("secondary", [])
        ):
            score += 0.20  # AKShare industry confirmation

        if score > best_score:
            best_score = score
            best_match = industry_type

    return best_match, best_score


def get_display_name(industry_type: str) -> str:
    """Get display name for industry type."""
    try:
        config_path = get_project_root() / "config" / "industry_profiles.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return (
            config.get("industries", {}).get(industry_type, {}).get("display_name", industry_type)
        )
    except Exception:
        return industry_type


def classify_industry_with_confidence(
    stock_code: str, company_info: dict, metrics: dict
) -> IndustryClassificationResult:
    """
    Multi-signal industry classification with confidence scoring.

    Args:
        stock_code: Stock code
        company_info: Company information dict
        metrics: Financial metrics dict

    Returns:
        IndustryClassificationResult with confidence scoring
    """
    confidence = 0.0
    confidence_factors = {}
    classification_path = []

    # Extract company info
    company_name = company_info.get("name", "")
    business_desc = company_info.get("business_description", "")
    akshare_industry = company_info.get("akshare_industry", "")

    # Factor 1: Keyword matching
    keyword_industry, keyword_score = match_keywords(company_name, business_desc, akshare_industry)
    if keyword_industry:
        confidence += keyword_score
        confidence_factors["keyword"] = keyword_score
        classification_path.append(f"关键词匹配: {keyword_industry} (+{keyword_score:.2f})")

    # Determine final industry type
    final_industry = keyword_industry if keyword_industry else "generic"

    # Check if confidence below threshold
    if confidence < CONFIDENCE_THRESHOLD:
        classification_path.append(f"置信度{confidence:.2f}<{CONFIDENCE_THRESHOLD}，降级到generic")
        return IndustryClassificationResult(
            industry_type="generic",
            display_name="综合行业",
            confidence=confidence,
            confidence_factors=confidence_factors,
            conservative_mode=True,
            classification_path=classification_path,
        )

    return IndustryClassificationResult(
        industry_type=final_industry,
        display_name=get_display_name(final_industry),
        confidence=min(confidence, 1.0),
        confidence_factors=confidence_factors,
        conservative_mode=False,
        classification_path=classification_path,
    )


def classify_by_business_description(company_name: str, business_desc: str) -> str | None:
    """
    Classify industry based on company name and business description.

    BUG-03: Priority keywords are checked first to avoid misclassification.
    For example, 宁德时代 (CATL) should be classified as new_energy_mfg
    even if some characters might partially match pharma keywords.

    Args:
        company_name: Company name
        business_desc: Business description

    Returns:
        Industry type string or None if no match
    """
    combined_text = company_name + business_desc

    # Check priority keywords first (new energy, etc.)
    for industry_type, keywords in PRIORITY_KEYWORDS.items():
        if any(kw in combined_text for kw in keywords):
            logger.info(
                f"[Industry] Classified '{company_name}' as '{industry_type}' "
                f"(matched priority keyword)"
            )
            return industry_type

    # Check regular keywords from YAML config
    _, keywords = _load_profiles()
    for industry_type, keyword_list in keywords.items():
        if any(kw in combined_text for kw in keyword_list):
            logger.info(
                f"[Industry] Classified '{company_name}' as '{industry_type}' "
                f"(matched regular keyword)"
            )
            return industry_type

    logger.debug(f"[Industry] No match for '{company_name}', returning None")
    return None


def classify_industry(sector: str | None, sub_industry: str | None = None) -> str:
    """
    Classify stock into industry category.

    BUG-03 FIX: Priority keywords are now checked FIRST to avoid misclassification.
    E.g., CATL (宁德时代) should be new_energy_mfg, not pharma.

    Args:
        sector: Sector from watchlist or data source
        sub_industry: Optional sub-industry detail

    Returns:
        Industry key (energy/consumer/tech/banking/manufacturing/healthcare/real_estate/default)
    """
    if not sector:
        logger.debug("[Industry] No sector provided, using default")
        return "default"

    # Combine sector and sub_industry for matching
    search_text = sector + " " + (sub_industry or "")
    search_text_lower = search_text.lower()

    # Step 1: Check PRIORITY_KEYWORDS first (new energy, EV, etc.)
    # These are high-value categories that should never be misclassified
    for industry_type, keywords in PRIORITY_KEYWORDS.items():
        if any(kw in search_text for kw in keywords):
            logger.info(
                f"[Industry] Priority matched '{sector}' as '{industry_type}' "
                f"(matched priority keyword)"
            )
            return industry_type

    # Step 2: Check INDUSTRY_KEYWORDS (structured with primary/secondary/negative)
    for industry_type, keywords_dict in INDUSTRY_KEYWORDS.items():
        primary = keywords_dict.get("primary", [])
        secondary = keywords_dict.get("secondary", [])
        negative = keywords_dict.get("negative", [])

        # Skip if negative keywords match
        if any(neg in search_text for neg in negative):
            continue

        # Check primary and secondary keywords
        if any(kw in search_text for kw in primary + secondary):
            logger.info(f"[Industry] Keyword matched '{sector}' as '{industry_type}'")
            return industry_type

    # Step 3: Check YAML applies_to keywords (fallback)
    _, yaml_keywords = _load_profiles()
    for industry, keyword_list in yaml_keywords.items():
        for keyword in keyword_list:
            if keyword in search_text_lower:
                logger.info(
                    f"[Industry] YAML matched '{sector}' as '{industry}' " f"(matched: {keyword})"
                )
                return industry

    # No match found
    logger.warning(f"[Industry] No match for sector '{sector}', using default")
    return "default"


def get_industry_profile(industry: str) -> IndustryProfile:
    """
    Get complete industry profile.

    Args:
        industry: Industry key from classify_industry()

    Returns:
        IndustryProfile with weights, rationale, and scoring thresholds
    """
    profiles, _ = _load_profiles()

    if industry not in profiles:
        logger.warning(f"[Industry] Profile for '{industry}' not found, using generic")
        industry = "generic"

    profile = profiles[industry]

    # Validate weights sum to 1.0
    weights_sum = sum(profile["weights"].values())
    if abs(weights_sum - 1.0) > 0.01:
        logger.warning(f"[Industry] Weights for '{industry}' sum to {weights_sum:.2f}, not 1.0!")

    return profile


def get_agent_weights(industry: str) -> IndustryWeights:
    """
    Get agent weights for an industry.

    Args:
        industry: Industry key

    Returns:
        Dictionary of agent weights
    """
    profile = get_industry_profile(industry)
    return profile["weights"]


def get_scoring_thresholds(industry: str) -> IndustryScoring:
    """
    Get fundamentals scoring thresholds for an industry.

    Args:
        industry: Industry key

    Returns:
        Scoring thresholds (ROE, margin, D/E, growth weight, cash weight)
    """
    profile = get_industry_profile(industry)
    return profile["scoring"]


def get_ev_ebitda_multiple(industry: str, cycle_phase: str = "normal") -> float:
    """
    Get industry-specific EV/EBITDA multiple.

    Phase 3: Replace hardcoded 6x/8x multiples with industry-specific values
    from industry_profiles.yaml.

    Args:
        industry: Industry key from classify_industry()
        cycle_phase: "bottom", "normal", or "peak" (default: "normal")

    Returns:
        EV/EBITDA multiple for the industry and cycle phase
    """
    profiles, _ = _load_profiles()

    if industry not in profiles:
        industry = "generic"  # v2.1 uses 'generic' instead of 'default'

    profile = profiles.get(industry, profiles.get("generic", {}))

    # v2.1 format: ev_ebitda_multiple can be a list [low, high] or single value
    ev_multiple = profile.get("ev_ebitda_multiple")

    if ev_multiple is not None:
        if isinstance(ev_multiple, list):
            # Return middle of range for normal, low for peak, high for bottom
            if cycle_phase == "bottom":
                return float(ev_multiple[1])  # Higher multiple at bottom
            elif cycle_phase == "peak":
                return float(ev_multiple[0])  # Lower multiple at peak
            else:
                return float(sum(ev_multiple) / len(ev_multiple))  # Average for normal
        else:
            return float(ev_multiple)

    # Legacy format support
    multiples = profile.get("valuation_multiples", {}).get("ev_ebitda", {})
    key_mapping = {
        "bottom": "cycle_bottom",
        "normal": "cycle_normal",
        "peak": "cycle_peak",
    }
    key = key_mapping.get(cycle_phase, "cycle_normal")
    multiple = multiples.get(key)

    if multiple is None:
        # Fallback to generic
        generic_profile = profiles.get("generic", {})
        generic_multiple = generic_profile.get("ev_ebitda_multiple", 8.0)
        if isinstance(generic_multiple, list):
            multiple = sum(generic_multiple) / len(generic_multiple)
        else:
            multiple = generic_multiple

    logger.debug(f"[Industry] EV/EBITDA multiple for {industry} ({cycle_phase}): {multiple}x")
    return float(multiple)


def get_pe_multiple(industry: str, stage: str = "normal") -> float | None:
    """
    Get industry-specific P/E multiple.

    Args:
        industry: Industry key
        stage: "rd_stage", "growth_stage", "mature_stage", "cycle_bottom", "cycle_normal", "cycle_peak"

    Returns:
        P/E multiple or None if PE not applicable (e.g., R&D stage)
    """
    profiles, _ = _load_profiles()

    if industry not in profiles:
        industry = "default"

    profile = profiles[industry]
    multiples = profile.get("valuation_multiples", {}).get("pe", {})

    # Try exact key match
    multiple = multiples.get(stage)

    # Fallback mapping
    if multiple is None:
        key_mapping = {
            "bottom": "cycle_bottom",
            "normal": "cycle_normal",
            "peak": "cycle_peak",
        }
        multiple = multiples.get(key_mapping.get(stage, stage))

    return float(multiple) if multiple is not None else None


def get_ps_multiple(industry: str, stage: str = "normal") -> float:
    """
    Get industry-specific P/S (Price-to-Sales) multiple.

    Args:
        industry: Industry key
        stage: "loss_making", "growth_stage", "mature_stage", "rd_stage", "cycle_normal"

    Returns:
        P/S multiple for the industry
    """
    profiles, _ = _load_profiles()

    if industry not in profiles:
        industry = "default"

    profile = profiles[industry]
    multiples = profile.get("valuation_multiples", {}).get("ps", {})

    # Try exact key match
    multiple = multiples.get(stage)

    # Fallback to normal/growth
    if multiple is None:
        multiple = multiples.get("cycle_normal") or multiples.get("growth_stage") or 4.0

    return float(multiple)


def get_pb_multiple(industry: str, cycle_phase: str = "normal") -> float:
    """
    Get industry-specific P/B (Price-to-Book) multiple.

    Args:
        industry: Industry key
        cycle_phase: "bottom", "normal", "peak", "undervalued", "fair_value", "overvalued"

    Returns:
        P/B multiple for the industry
    """
    profiles, _ = _load_profiles()

    if industry not in profiles:
        industry = "default"

    profile = profiles[industry]
    multiples = profile.get("valuation_multiples", {}).get("pb", {})

    # Map cycle_phase to YAML key format
    key_mapping = {
        "bottom": "cycle_bottom",
        "normal": "cycle_normal",
        "peak": "cycle_peak",
        "fair": "fair_value",
    }
    key = key_mapping.get(cycle_phase, cycle_phase)

    multiple = multiples.get(key)

    if multiple is None:
        multiple = multiples.get("cycle_normal") or multiples.get("fair_value") or 1.5

    return float(multiple)


def get_industry_comparables(industry: str) -> list[dict]:
    """
    Get industry-specific comparable companies from industry_profiles.yaml.

    Args:
        industry: Industry key from classify_industry()

    Returns:
        List of comparable company dicts with ticker, name, and note
    """
    profiles, _ = _load_profiles()

    if industry not in profiles:
        industry = "generic"  # v2.1 uses 'generic' instead of 'default'

    profile = profiles.get(industry, profiles.get("generic", {}))

    # v2.1 format: comparables stored in 'comparables' field (list of tickers)
    comparables = profile.get("comparables", [])
    if comparables:
        # Convert ticker list to dict format
        return [{"ticker": t, "name": "", "note": ""} for t in comparables]

    # Also check example_companies (v2.1 format)
    example_companies = profile.get("example_companies", [])
    if example_companies:
        return [{"ticker": t, "name": "", "note": ""} for t in example_companies]

    # Legacy format support
    comparables = profile.get("comparable_companies", [])

    logger.debug(f"[Industry] Found {len(comparables)} comparables for {industry}")
    return comparables


def get_industry_from_watchlist(ticker: str, watchlist_path: Path | None = None) -> str:
    """
    Get industry classification from watchlist.yaml.

    Args:
        ticker: Stock ticker
        watchlist_path: Optional path to watchlist (defaults to config/watchlist.yaml)

    Returns:
        Industry key
    """
    if watchlist_path is None:
        watchlist_path = get_project_root() / "config" / "watchlist.yaml"

    try:
        with open(watchlist_path, "r", encoding="utf-8") as f:
            watchlist = yaml.safe_load(f)

        # Search for ticker in all markets
        for market, stocks in watchlist.get("watchlist", {}).items():
            for stock in stocks:
                if stock.get("ticker") == ticker:
                    sector = stock.get("sector")
                    sub_industry = stock.get("sub_industry")
                    return classify_industry(sector, sub_industry)

        logger.warning(f"[Industry] Ticker {ticker} not found in watchlist")
        return "default"

    except Exception as e:
        logger.error(f"[Industry] Failed to read watchlist: {e}")
        return "default"


class ValuationMethodConfig(TypedDict):
    """Valuation method configuration for a stock type."""

    enabled_methods: list[str]  # DCF, Graham, EV/EBITDA, P/B, PS, EV/Sales, PEG
    weights: dict[str, float]  # method -> weight
    rationale: str


def detect_loss_making_tech_stock(
    net_income: float | None,
    net_margin: float | None,
    revenue_growth: float | None,
    rd_ratio: float | None = None,
    industry: str | None = None,
    roe: float | None = None,
) -> bool:
    """
    BUG-03A FIX: Detect loss-making tech stocks that need PS/EV-Sales valuation.

    CRITICAL FIX: Added ROE check to prevent misclassifying profitable companies.
    Industrial Foxconn (工业富联) has 3.9% net margin but 21.6% ROE and ¥353B profit,
    which is clearly a profitable company, not a loss-making tech stock.

    Criteria (revised):
    - Net income ≤ 0 OR (net margin < 2% AND ROE < 5%)
    - Revenue growth ≥ 15% (growth potential)
    - R&D expense / revenue ≥ 10% (optional, indicates tech investment)
    - Industry is tech/software/AI related

    Args:
        net_income: Latest net income (absolute value)
        net_margin: Net profit margin as decimal (e.g., -0.49 for -49%)
        revenue_growth: Revenue growth rate as decimal (e.g., 0.25 for 25%)
        rd_ratio: R&D expense ratio as decimal (e.g., 0.15 for 15%)
        industry: Industry classification
        roe: Return on equity as decimal (e.g., 0.21 for 21%) - NEW parameter

    Returns:
        True if stock should use loss-making tech valuation methods
    """
    # Step 1: Check for TRUE loss-making condition
    # A company is only "loss-making" if:
    # - Net income is negative, OR
    # - Net margin < 2% AND ROE is also low (< 5%)
    # This prevents misclassifying low-margin-but-profitable manufacturers
    is_truly_loss_making = False

    if net_income is not None and net_income <= 0:
        is_truly_loss_making = True
    elif net_margin is not None and net_margin < 0.02:  # < 2% margin
        # Only consider as "struggling" if ROE is also low
        # High ROE (>5%) with low margin means efficient asset-light business, not loss-making
        if roe is None or roe < 0.05:
            is_truly_loss_making = True
        else:
            logger.debug(
                f"[Industry] Low margin ({net_margin:.1%}) but ROE={roe:.1%} > 5%, "
                f"not a loss-making tech stock (profitable manufacturing)"
            )
            return False

    if not is_truly_loss_making:
        return False

    # Step 2: Check ROE as a safety guard
    # Companies with ROE > 10% are clearly profitable and should NOT be classified as loss-making
    if roe is not None and roe > 0.10:
        logger.debug(
            f"[Industry] ROE={roe:.1%} > 10% indicates profitable company, "
            f"not a loss-making tech stock"
        )
        return False

    # Step 3: Check for growth potential (revenue growth >= 15%)
    has_growth = revenue_growth is not None and revenue_growth >= 0.15
    if not has_growth:
        logger.debug(
            f"[Industry] Loss-making but low growth ({revenue_growth}), " f"not a growth tech stock"
        )
        return False

    # Step 4: Check for tech industry indicators
    tech_keywords = ["tech", "科技", "软件", "AI", "人工智能", "互联网", "电子", "半导体"]
    is_tech_industry = False
    if industry:
        is_tech_industry = any(kw in industry.lower() for kw in tech_keywords)

    # Check for R&D investment (strong indicator of tech company)
    has_rd_investment = rd_ratio is not None and rd_ratio >= 0.10  # >= 10%

    # Classify as loss-making tech if:
    # 1. Is truly loss-making + has growth + (is tech industry OR has high R&D)
    if is_tech_industry or has_rd_investment:
        logger.info(
            f"[Industry] Detected loss-making tech stock: "
            f"net_margin={net_margin}, roe={roe}, growth={revenue_growth}, "
            f"rd_ratio={rd_ratio}, industry={industry}"
        )
        return True

    return False


def get_loss_making_tech_valuation_config() -> ValuationMethodConfig:
    """
    BUG-03A: Get valuation method configuration for loss-making tech stocks.

    Returns PS/EV-Sales focused weights, disables Graham Number.
    """
    return {
        "enabled_methods": ["PS", "EV/Sales", "DCF", "P/B"],
        "weights": {
            "PS": 0.40,  # Primary method - revenue-based
            "EV/Sales": 0.30,  # Enterprise value / sales
            "DCF": 0.20,  # DCF with turnaround assumptions
            "P/B": 0.10,  # Floor value only
        },
        "rationale": (
            "亏损期科技股估值方法: PS和EV/Sales为主力方法（营收不受亏损影响），"
            "禁用Graham Number/EV-EBITDA（负EPS/EBITDA无意义）"
        ),
    }


def detect_growth_stock(
    pe_ratio: float | None,
    revenue_cagr_3y: float | None,
    net_income: float | None,
    eps: float | None,
    industry: str | None = None,
) -> bool:
    """
    BUG-03B: Detect profitable growth stocks that need PEG valuation.

    Criteria (from 多行业估值能力进化方案改造 2.0):
    - Net income > 0 AND EPS > 0 (must be profitable)
    - Revenue 3-year CAGR >= 15%
    - PE > 25x (market is paying for growth)
    - Industry is growth-related (automation/semiconductor/new energy/internet/pharma R&D)

    Args:
        pe_ratio: Current P/E ratio
        revenue_cagr_3y: Revenue 3-year CAGR as decimal (e.g., 0.20 for 20%)
        net_income: Latest net income (absolute value)
        eps: Latest earnings per share
        industry: Industry classification

    Returns:
        True if stock should use growth stock PEG valuation methods
    """
    # Must be profitable (positive net income and EPS)
    if net_income is None or net_income <= 0:
        return False
    if eps is None or eps <= 0:
        return False

    # Check for growth potential (revenue CAGR >= 15%)
    if revenue_cagr_3y is None or revenue_cagr_3y < 0.15:
        logger.debug(
            f"[Industry] Profitable but low growth (CAGR={revenue_cagr_3y}), not a growth stock"
        )
        return False

    # Check for growth premium (PE > 25x means market is paying for growth)
    if pe_ratio is None or pe_ratio <= 25:
        logger.debug(
            f"[Industry] Profitable and growing but PE={pe_ratio} <= 25x, "
            f"market not pricing as growth stock"
        )
        return False

    # Check for growth-related industry (optional but helps confirm)
    growth_keywords = [
        "自动化",
        "automation",
        "半导体",
        "semiconductor",
        "新能源",
        "new energy",
        "互联网",
        "internet",
        "医药",
        "pharma",
        "研发",
        "r&d",
        "科技",
        "tech",
        "软件",
        "software",
        "人工智能",
        "ai",
        "电子",
        "electronic",
        "机器人",
        "robot",
        "智能",
        "smart",
    ]
    is_growth_industry = False
    if industry:
        is_growth_industry = any(kw in industry.lower() for kw in growth_keywords)

    # Classify as growth stock if:
    # 1. Is profitable + has high growth + high PE
    # 2. Industry is growth-related (strongly preferred but not mandatory)
    if is_growth_industry:
        logger.info(
            f"[Industry] Detected growth stock: "
            f"PE={pe_ratio:.1f}, CAGR={revenue_cagr_3y*100:.1f}%, "
            f"industry={industry}"
        )
        return True

    # Even without growth industry tag, if PE > 30 and CAGR > 20%, classify as growth
    # (strong financial signals override industry classification)
    if pe_ratio > 30 and revenue_cagr_3y > 0.20:
        logger.info(
            f"[Industry] Detected growth stock (strong financials override): "
            f"PE={pe_ratio:.1f}, CAGR={revenue_cagr_3y*100:.1f}%"
        )
        return True

    return False


def get_growth_tech_valuation_config() -> ValuationMethodConfig:
    """
    BUG-03B: Get valuation method configuration for profitable growth tech stocks.

    Returns PEG-focused weights, disables Graham Number.
    """
    return {
        "enabled_methods": ["PEG", "DCF", "EV/Sales", "P/B"],
        "weights": {
            "DCF": 0.35,  # DCF with growth assumptions
            "PEG": 0.30,  # Price/Earnings-to-Growth
            "EV/Sales": 0.20,  # Industry comparison
            "P/B": 0.15,  # ROE-adjusted P/B
        },
        "rationale": (
            "盈利期成长股估值方法: DCF和PEG为主力（反映成长溢价），"
            "禁用Graham Number（专为防御型低估股设计）"
        ),
    }


def detect_financial_stock(
    industry: str | None,
    roe: float | None = None,
    dividend_yield: float | None = None,
) -> bool:
    """
    Phase 2: Detect financial stocks (banks/insurance) that need P/B-ROE valuation.

    Criteria:
    - Industry is banking/insurance/financial services related
    - Typically high dividend yield (>2%)
    - ROE is the key profitability metric

    Args:
        industry: Industry classification
        roe: Return on equity as decimal (e.g., 0.14 for 14%)
        dividend_yield: Dividend yield as decimal (e.g., 0.05 for 5%)

    Returns:
        True if stock should use financial stock valuation methods
    """
    if not industry:
        return False

    financial_keywords = [
        "银行",
        "bank",
        "banking",
        "保险",
        "insurance",
        "寿险",
        "财险",
        "金融",
        "financial",
        "finance",
        "证券",
        "securities",
        "券商",
    ]

    is_financial = any(kw in industry.lower() for kw in financial_keywords)

    if is_financial:
        logger.info(
            f"[Industry] Detected financial stock: "
            f"industry={industry}, ROE={roe}, div_yield={dividend_yield}"
        )
        return True

    return False


def get_financial_stock_valuation_config() -> ValuationMethodConfig:
    """
    Phase 2: Get valuation method configuration for financial stocks.

    Returns P/B-ROE focused weights, disables EV/EBITDA and standard DCF.
    """
    return {
        "enabled_methods": ["P/B_ROE", "DDM", "P/E"],
        "weights": {
            "P/B_ROE": 0.40,  # P/B driven by ROE/Ke
            "DDM": 0.35,  # Dividend discount model
            "P/E": 0.25,  # Operational profit PE
        },
        "rationale": (
            "金融股估值方法: P/B-ROE模型为主力（ROE是核心指标），"
            "DDM股息折现（金融股分红稳定），"
            "禁用EV/EBITDA（金融公司债务是业务本身）和标准DCF（FCF定义不同）"
        ),
    }


def detect_cyclical_stock(
    industry: str | None,
    revenue_volatility: float | None = None,
    operating_margin_volatility: float | None = None,
) -> bool:
    """
    Phase 2: Detect cyclical stocks (resources/commodities) that need normalized DCF.

    Criteria:
    - Industry is oil/gas/mining/steel/chemical related
    - High revenue or margin volatility across business cycles
    - Earnings tied to commodity prices

    Args:
        industry: Industry classification
        revenue_volatility: Standard deviation of revenue growth
        operating_margin_volatility: Standard deviation of operating margin

    Returns:
        True if stock should use cyclical stock valuation methods
    """
    if not industry:
        return False

    cyclical_keywords = [
        "石油",
        "oil",
        "petroleum",
        "天然气",
        "gas",
        "lng",
        "矿业",
        "mining",
        "矿产",
        "钢铁",
        "steel",
        "铝",
        "aluminum",
        "化工",
        "chemical",
        "petrochemical",
        "有色金属",
        "metal",
        "煤炭",
        "coal",
        "航运",
        "shipping",
        "能源",
        "energy",  # Added to catch energy sector
        "油田",
        "oilfield",
        "油服",
    ]

    is_cyclical = any(kw in industry.lower() for kw in cyclical_keywords)

    if is_cyclical:
        logger.info(
            f"[Industry] Detected cyclical stock: "
            f"industry={industry}, rev_vol={revenue_volatility}"
        )
        return True

    return False


def get_cyclical_stock_valuation_config() -> ValuationMethodConfig:
    """
    Phase 2: Get valuation method configuration for cyclical stocks.

    Returns normalized DCF + NAV focused weights.
    """
    return {
        "enabled_methods": ["DCF_Normalized", "EV/EBITDA_Cycle", "NAV", "P/B_Cycle"],
        "weights": {
            "DCF_Normalized": 0.35,  # DCF with cycle-bottom FCF
            "EV/EBITDA_Cycle": 0.30,  # Cycle-adjusted EV/EBITDA
            "NAV": 0.20,  # Asset replacement value
            "P/B_Cycle": 0.15,  # Cycle-bottom P/B
        },
        "rationale": (
            "周期股估值方法: 正常化DCF（周期底部FCF为基准），"
            "EV/EBITDA使用周期底部倍数，"
            "资产重置价值（NAV）作为安全边际参考，"
            "禁用成长性DCF（会高估周期顶部增长）"
        ),
    }


def detect_healthcare_stock(
    industry: str | None,
) -> bool:
    """
    Phase 2: Detect healthcare/pharma stocks.

    Criteria:
    - Industry is pharma/biotech/medical/healthcare related

    Args:
        industry: Industry classification

    Returns:
        True if stock is in healthcare sector
    """
    if not industry:
        return False

    healthcare_keywords = [
        "医药",
        "pharma",
        "pharmaceutical",
        "生物",
        "biotech",
        "bio",
        "医疗",
        "medical",
        "healthcare",
        "制药",
        "drug",
        "保健",
        "health",
        "疫苗",
        "vaccine",
        "诊断",
        "diagnostic",
        "器械",
        "device",
        "cro",
        "cxo",
        "cmo",
        "cdmo",  # lowercase for case-insensitive matching
    ]

    is_healthcare = any(kw in industry.lower() for kw in healthcare_keywords)

    if is_healthcare:
        logger.info(f"[Industry] Detected healthcare stock: industry={industry}")
        return True

    return False


def detect_healthcare_rd_stage(
    net_income: float | None,
    net_margin: float | None,
    rd_ratio: float | None = None,
    revenue_growth: float | None = None,
) -> bool:
    """
    Phase 2: Detect if healthcare stock is in R&D stage (vs mature stage).

    R&D stage criteria (from 多行业估值能力进化方案改造 2.0):
    - Net income <= 0 OR net margin < 5% (loss-making or marginal)
    - High R&D expense ratio (>= 15%, typical for biotech)
    - Often high revenue growth despite losses

    Mature stage:
    - Stable profitability (net margin >= 10%)
    - Lower R&D ratio as % of revenue

    Args:
        net_income: Latest net income (absolute value)
        net_margin: Net profit margin as decimal (e.g., -0.10 for -10%)
        rd_ratio: R&D expense ratio as decimal (e.g., 0.20 for 20%)
        revenue_growth: Revenue growth rate as decimal

    Returns:
        True if stock is in R&D stage, False if mature stage
    """
    # Check for loss-making OR marginal profitability
    is_loss_making = False
    if net_income is not None and net_income <= 0:
        is_loss_making = True
    elif net_margin is not None and net_margin < 0.05:
        is_loss_making = True

    # High R&D ratio is a strong indicator of R&D stage
    has_high_rd = rd_ratio is not None and rd_ratio >= 0.15

    # R&D stage if loss-making OR high R&D ratio
    if is_loss_making:
        logger.info(
            f"[Industry] Healthcare R&D stage detected: "
            f"net_margin={net_margin}, rd_ratio={rd_ratio}"
        )
        return True

    if has_high_rd:
        logger.info(f"[Industry] Healthcare R&D stage detected (high R&D): " f"rd_ratio={rd_ratio}")
        return True

    logger.debug(
        f"[Industry] Healthcare mature stage: " f"net_margin={net_margin}, rd_ratio={rd_ratio}"
    )
    return False


def get_healthcare_rd_valuation_config() -> ValuationMethodConfig:
    """
    Phase 2: Get valuation method configuration for R&D stage healthcare stocks.

    Returns PS-focused weights, similar to loss-making tech.
    R&D stage pharma/biotech should use revenue-based metrics as profits
    don't reflect value of pipeline.
    """
    return {
        "enabled_methods": ["PS", "EV/Sales", "Pipeline_DCF", "P/B"],
        "weights": {
            "PS": 0.40,  # Primary - revenue-based
            "EV/Sales": 0.30,  # Enterprise value approach
            "Pipeline_DCF": 0.20,  # DCF with pipeline probability adjustments
            "P/B": 0.10,  # Floor value only
        },
        "rationale": (
            "研发期医药股估值方法: PS和EV/Sales为主力（营收反映管线商业化进展），"
            "管线折现DCF（考虑研发成功概率），"
            "禁用PE类方法（亏损期PE无意义）"
        ),
    }


def get_healthcare_mature_valuation_config() -> ValuationMethodConfig:
    """
    Phase 2: Get valuation method configuration for mature healthcare stocks.

    Returns PE-focused weights for profitable pharma companies with established products.
    """
    return {
        "enabled_methods": ["P/E", "DCF", "EV/EBITDA", "PS"],
        "weights": {
            "P/E": 0.35,  # Primary - stable earnings
            "DCF": 0.30,  # Cash flow based
            "EV/EBITDA": 0.20,  # Industry comparison
            "PS": 0.15,  # Revenue multiple as secondary
        },
        "rationale": (
            "成熟期医药股估值方法: PE为主力（盈利稳定可比较），"
            "DCF折现现金流（现金流可预测），"
            "EV/EBITDA行业对标，"
            "PS作为辅助参考"
        ),
    }


def is_innovative_pharma(metrics: dict, business_desc: str) -> bool:
    """
    Detect if company is an innovative pharma company.

    Conditions (need 2+):
    1. R&D expense ratio > 30%
    2. Net margin < 5% (loss or slim profit)
    3. Business description contains innovative drug keywords
    4. Pipeline/clinical trial mentions

    Args:
        metrics: Financial metrics dict
        business_desc: Business description text

    Returns:
        True if company is an innovative pharma company
    """
    conditions = 0

    # R&D expense ratio high
    rd_ratio = metrics.get("rd_expense_ratio", 0)
    if rd_ratio > 30:
        conditions += 1

    # Loss or slim profit
    net_margin = metrics.get("net_margin", 0)
    if net_margin < 5:
        conditions += 1

    # Keywords check
    innovative_keywords = [
        "创新药",
        "First-in-class",
        "Best-in-class",
        "临床试验",
        "IND",
        "NDA",
        "管线",
        "Pipeline",
    ]
    if any(kw.lower() in business_desc.lower() for kw in innovative_keywords):
        conditions += 1

    return conditions >= 2


def classify_sub_industry(industry_type: str, company_info: dict, metrics: dict) -> str:
    """
    Further classify within a main industry to sub-industry.

    Args:
        industry_type: Main industry type (e.g., 'pharma')
        company_info: Company information dict
        metrics: Financial metrics dict

    Returns:
        Sub-industry type or original industry_type
    """
    business_desc = company_info.get("business_description", "")
    company_name = company_info.get("name", "")

    # === Pharma sub-classification ===
    if industry_type in ["pharma", "pharma_mature", "medical_device"]:
        # Check innovative pharma
        if is_innovative_pharma(metrics, business_desc):
            return "pharma_innovative"

        # Check CXO
        cxo_keywords = ["CRO", "CMO", "CDMO", "医药外包", "药物研发服务"]
        if any(kw in business_desc for kw in cxo_keywords):
            return "pharma_cxo"

        # Check TCM
        tcm_keywords = ["中药", "中成药", "中医药", "传统医药"]
        if any(kw in business_desc + company_name for kw in tcm_keywords):
            return "pharma_tcm"

    # === Tech sub-classification ===
    if industry_type in ["tech", "software"]:
        saas_keywords = ["SaaS", "云服务", "订阅", "ARR", "云计算"]
        gross_margin = metrics.get("gross_margin", 0)
        if any(kw in business_desc for kw in saas_keywords) and gross_margin > 70:
            return "tech_saas"
        else:
            return "tech_traditional"

    # === Consumer sub-classification ===
    if industry_type in ["consumer", "brand_moat"]:
        gross_margin = metrics.get("gross_margin", 0)
        if gross_margin > 70:
            return "consumer_premium"
        elif gross_margin > 30:
            return "consumer_mass"

    return industry_type  # No further classification
