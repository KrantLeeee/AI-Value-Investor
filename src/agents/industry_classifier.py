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
