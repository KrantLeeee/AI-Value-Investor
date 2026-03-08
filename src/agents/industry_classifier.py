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

from src.utils.config import get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)


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


def _load_profiles() -> tuple[dict[str, IndustryProfile], dict[str, list[str]]]:
    """Load industry profiles from YAML config."""
    global _PROFILES_CACHE, _KEYWORDS_CACHE

    if _PROFILES_CACHE is not None and _KEYWORDS_CACHE is not None:
        return _PROFILES_CACHE, _KEYWORDS_CACHE

    config_path = get_project_root() / "config" / "industry_profiles.yaml"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        _PROFILES_CACHE = config["industry_profiles"]
        _KEYWORDS_CACHE = config["industry_keywords"]

        logger.info(f"[Industry] Loaded {len(_PROFILES_CACHE)} industry profiles")
        return _PROFILES_CACHE, _KEYWORDS_CACHE

    except Exception as e:
        logger.error(f"[Industry] Failed to load profiles: {e}")
        # Return minimal default
        default_profile: IndustryProfile = {
            "weights": {
                "fundamentals": 0.25,
                "valuation": 0.25,
                "warren_buffett": 0.20,
                "ben_graham": 0.15,
                "sentiment": 0.15,
            },
            "rationale": "Default fallback",
            "validated": False,
            "scoring": {
                "roe_thresholds": [20, 15, 10],
                "net_margin_thresholds": [15, 10, 5],
                "de_thresholds": [0.5, 1.0, 1.5],
                "growth_weight": 0.25,
                "cash_quality_weight": 0.25,
            },
        }
        _PROFILES_CACHE = {"default": default_profile}
        _KEYWORDS_CACHE = {}
        return _PROFILES_CACHE, _KEYWORDS_CACHE


def classify_industry(sector: str | None, sub_industry: str | None = None) -> str:
    """
    Classify stock into industry category.

    Args:
        sector: Sector from watchlist or data source
        sub_industry: Optional sub-industry detail

    Returns:
        Industry key (energy/consumer/tech/banking/manufacturing/healthcare/real_estate/default)
    """
    if not sector:
        logger.debug("[Industry] No sector provided, using default")
        return "default"

    _, keywords = _load_profiles()

    # Combine sector and sub_industry for matching
    search_text = (sector + " " + (sub_industry or "")).lower()

    # Try to match keywords
    for industry, keyword_list in keywords.items():
        for keyword in keyword_list:
            if keyword in search_text:
                logger.info(f"[Industry] Classified '{sector}' as '{industry}' (matched: {keyword})")
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
        logger.warning(f"[Industry] Profile for '{industry}' not found, using default")
        industry = "default"

    profile = profiles[industry]

    # Validate weights sum to 1.0
    weights_sum = sum(profile["weights"].values())
    if abs(weights_sum - 1.0) > 0.01:
        logger.warning(
            f"[Industry] Weights for '{industry}' sum to {weights_sum:.2f}, not 1.0!"
        )

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
) -> bool:
    """
    BUG-03A: Detect loss-making tech stocks that need PS/EV-Sales valuation.

    Criteria (from 多行业估值能力进化方案改造 2.0):
    - Net income ≤ 0 OR net margin < 2%
    - Revenue growth ≥ 20% (growth potential - without this, stock is just failing)
    - R&D expense / revenue ≥ 10% (optional, indicates tech investment)
    - Industry is tech/software/AI related

    Args:
        net_income: Latest net income (absolute value)
        net_margin: Net profit margin as decimal (e.g., -0.49 for -49%)
        revenue_growth: Revenue growth rate as decimal (e.g., 0.25 for 25%)
        rd_ratio: R&D expense ratio as decimal (e.g., 0.15 for 15%)
        industry: Industry classification

    Returns:
        True if stock should use loss-making tech valuation methods
    """
    # Check for loss-making OR marginal profitability condition
    # BUG-03A: Include borderline cases (net margin < 5% counts as "struggling")
    is_loss_making = False
    if net_income is not None and net_income <= 0:
        is_loss_making = True
    elif net_margin is not None and net_margin < 0.05:  # < 5% (borderline profitability)
        is_loss_making = True

    if not is_loss_making:
        return False

    # Check for growth potential (revenue growth >= 15% for borderline cases)
    # Original threshold was 20%, relaxed to 15% to catch borderline cases
    has_growth = revenue_growth is not None and revenue_growth >= 0.15  # >= 15%
    if not has_growth:
        logger.debug(f"[Industry] Loss-making/marginal but low growth ({revenue_growth}), not a growth tech stock")
        return False

    # Check for tech industry (optional but helps)
    tech_keywords = ["tech", "科技", "软件", "AI", "人工智能", "互联网", "电子", "半导体"]
    is_tech_industry = False
    if industry:
        is_tech_industry = any(kw in industry.lower() for kw in tech_keywords)

    # Check for R&D investment (strong indicator of tech company)
    has_rd_investment = rd_ratio is not None and rd_ratio >= 0.10  # >= 10%

    # Classify as loss-making tech if:
    # 1. Is loss-making + has growth + (is tech industry OR has high R&D)
    # 2. R&D alone can qualify even without explicit tech label
    if is_tech_industry or has_rd_investment:
        logger.info(
            f"[Industry] Detected loss-making tech stock: "
            f"net_margin={net_margin}, growth={revenue_growth}, "
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
            "PS": 0.40,        # Primary method - revenue-based
            "EV/Sales": 0.30,  # Enterprise value / sales
            "DCF": 0.20,       # DCF with turnaround assumptions
            "P/B": 0.10,       # Floor value only
        },
        "rationale": (
            "亏损期科技股估值方法: PS和EV/Sales为主力方法（营收不受亏损影响），"
            "禁用Graham Number/EV-EBITDA（负EPS/EBITDA无意义）"
        ),
    }


def get_growth_tech_valuation_config() -> ValuationMethodConfig:
    """
    BUG-03B: Get valuation method configuration for profitable growth tech stocks.

    Returns PEG-focused weights, disables Graham Number.
    """
    return {
        "enabled_methods": ["PEG", "DCF", "EV/Sales", "P/B"],
        "weights": {
            "DCF": 0.35,       # DCF with growth assumptions
            "PEG": 0.30,       # Price/Earnings-to-Growth
            "EV/Sales": 0.20,  # Industry comparison
            "P/B": 0.15,       # ROE-adjusted P/B
        },
        "rationale": (
            "盈利期成长股估值方法: DCF和PEG为主力（反映成长溢价），"
            "禁用Graham Number（专为防御型低估股设计）"
        ),
    }
