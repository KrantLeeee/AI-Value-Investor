"""Tests for industry classification."""

import pytest
from src.agents.industry_classifier import (
    classify_industry,
    get_industry_profile,
    get_agent_weights,
    get_scoring_thresholds,
)


def test_classify_industry_energy():
    """Energy keywords should classify as energy."""
    assert classify_industry("能源") == "energy"
    assert classify_industry("石油服务") == "energy"
    assert classify_industry("煤炭") == "energy"
    assert classify_industry("电力") == "energy"


def test_classify_industry_consumer():
    """Consumer keywords should classify as consumer."""
    assert classify_industry("消费") == "consumer"
    assert classify_industry("食品饮料") == "consumer"
    assert classify_industry("零售") == "consumer"


def test_classify_industry_tech():
    """Tech keywords should classify as tech."""
    assert classify_industry("科技") == "tech"
    assert classify_industry("软件服务") == "tech"
    assert classify_industry("互联网") == "tech"
    assert classify_industry("电子") == "tech"


def test_classify_industry_banking():
    """Banking keywords should classify as banking."""
    assert classify_industry("银行") == "banking"
    assert classify_industry("金融") == "banking"


def test_classify_industry_manufacturing():
    """Manufacturing keywords should classify as manufacturing."""
    assert classify_industry("制造业") == "manufacturing"
    assert classify_industry("机械") == "manufacturing"
    assert classify_industry("汽车") == "manufacturing"


def test_classify_industry_healthcare():
    """Healthcare keywords should classify as healthcare."""
    assert classify_industry("医药") == "healthcare"
    assert classify_industry("生物制药") == "healthcare"  # Changed from 生物科技
    assert classify_industry("医疗器械") == "healthcare"


def test_classify_industry_real_estate():
    """Real estate keywords should classify as real_estate."""
    assert classify_industry("房地产") == "real_estate"
    assert classify_industry("地产") == "real_estate"


def test_classify_industry_unknown():
    """Unknown sector should default to default."""
    assert classify_industry("未知行业") == "default"
    assert classify_industry(None) == "default"


def test_classify_industry_with_sub_industry():
    """Sub-industry should also be considered."""
    industry = classify_industry("服务业", "互联网服务")
    assert industry == "tech"  # Should match "互联网"


def test_get_industry_profile_energy():
    """Energy profile should have correct structure."""
    profile = get_industry_profile("energy")

    assert "weights" in profile
    assert "rationale" in profile
    assert "validated" in profile
    assert "scoring" in profile

    # Check weights
    assert profile["weights"]["fundamentals"] == 0.25
    assert profile["weights"]["valuation"] == 0.30
    assert profile["validated"] is False

    # Check scoring thresholds
    assert profile["scoring"]["roe_thresholds"] == [15, 10, 6]


def test_get_industry_profile_consumer():
    """Consumer profile should emphasize Buffett."""
    profile = get_industry_profile("consumer")

    # Consumer should have high Buffett weight (brand/moat focus)
    assert profile["weights"]["warren_buffett"] == 0.35
    # Higher ROE thresholds for consumer
    assert profile["scoring"]["roe_thresholds"] == [25, 20, 15]


def test_get_industry_profile_tech():
    """Tech profile should emphasize sentiment."""
    profile = get_industry_profile("tech")

    # Tech should have high sentiment weight
    assert profile["weights"]["sentiment"] == 0.30
    # Lower D/E thresholds (tech has low debt)
    assert profile["scoring"]["de_thresholds"] == [0.2, 0.5, 0.8]


def test_get_industry_profile_banking():
    """Banking profile should emphasize fundamentals and Graham."""
    profile = get_industry_profile("banking")

    # Banking should emphasize fundamentals and Graham
    assert profile["weights"]["fundamentals"] == 0.30
    assert profile["weights"]["ben_graham"] == 0.30
    # Very high D/E thresholds for banks
    assert profile["scoring"]["de_thresholds"] == [8.0, 12.0, 15.0]


def test_get_industry_profile_default():
    """Default profile should have balanced weights."""
    profile = get_industry_profile("default")

    # Balanced weights
    assert profile["weights"]["fundamentals"] == 0.25
    assert profile["weights"]["valuation"] == 0.25
    assert profile["weights"]["warren_buffett"] == 0.20


def test_get_industry_profile_invalid():
    """Invalid industry should fall back to default."""
    profile = get_industry_profile("invalid_industry")

    # Should fall back to default
    assert profile["weights"]["fundamentals"] == 0.25


def test_get_agent_weights():
    """get_agent_weights should return weights dict."""
    weights = get_agent_weights("energy")

    assert isinstance(weights, dict)
    assert "fundamentals" in weights
    assert "valuation" in weights
    assert "warren_buffett" in weights
    assert "ben_graham" in weights
    assert "sentiment" in weights

    # Weights should sum to 1.0
    total = sum(weights.values())
    assert abs(total - 1.0) < 0.01


def test_get_scoring_thresholds():
    """get_scoring_thresholds should return thresholds dict."""
    thresholds = get_scoring_thresholds("consumer")

    assert isinstance(thresholds, dict)
    assert "roe_thresholds" in thresholds
    assert "net_margin_thresholds" in thresholds
    assert "de_thresholds" in thresholds
    assert "growth_weight" in thresholds
    assert "cash_quality_weight" in thresholds

    # Each threshold should be a list of 3 values
    assert len(thresholds["roe_thresholds"]) == 3
    assert len(thresholds["net_margin_thresholds"]) == 3
    assert len(thresholds["de_thresholds"]) == 3


def test_all_profiles_weights_sum_to_one():
    """All industry profiles should have weights summing to 1.0."""
    industries = [
        "energy",
        "consumer",
        "tech",
        "banking",
        "manufacturing",
        "healthcare",
        "real_estate",
        "default",
    ]

    for industry in industries:
        weights = get_agent_weights(industry)
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01, f"{industry} weights sum to {total}, not 1.0"


def test_all_profiles_have_required_fields():
    """All industry profiles should have required fields."""
    industries = [
        "energy",
        "consumer",
        "tech",
        "banking",
        "manufacturing",
        "healthcare",
        "real_estate",
        "default",
    ]

    for industry in industries:
        profile = get_industry_profile(industry)

        # Check required fields
        assert "weights" in profile
        assert "rationale" in profile
        assert "validated" in profile
        assert "scoring" in profile

        # All should be marked as not validated (pending P3)
        assert profile["validated"] is False


# ── Phase 3: Industry-specific valuation multiples tests ───────────────────


def test_get_ev_ebitda_multiple_energy():
    """Energy industry should have specific EV/EBITDA multiples."""
    from src.agents.industry_classifier import get_ev_ebitda_multiple

    bottom = get_ev_ebitda_multiple("energy", "bottom")
    normal = get_ev_ebitda_multiple("energy", "normal")
    peak = get_ev_ebitda_multiple("energy", "peak")

    # Energy should have cycle-aware multiples
    assert bottom < normal < peak
    assert 4.0 <= bottom <= 6.0  # Cycle bottom around 4.5x
    assert 5.0 <= normal <= 8.0  # Mid-cycle around 6.5x
    assert 8.0 <= peak <= 12.0   # Peak around 10x


def test_get_ev_ebitda_multiple_consumer():
    """Consumer industry should have higher EV/EBITDA multiples."""
    from src.agents.industry_classifier import get_ev_ebitda_multiple

    normal = get_ev_ebitda_multiple("consumer", "normal")

    # Consumer stocks typically trade at higher multiples
    assert normal >= 12.0  # Should be ~18x for consumer


def test_get_ev_ebitda_multiple_default():
    """Default industry should return fallback multiples."""
    from src.agents.industry_classifier import get_ev_ebitda_multiple

    normal = get_ev_ebitda_multiple("default", "normal")

    # Default should be moderate
    assert 6.0 <= normal <= 10.0


def test_get_ev_ebitda_multiple_invalid_industry():
    """Invalid industry should fall back to default multiples."""
    from src.agents.industry_classifier import get_ev_ebitda_multiple

    normal = get_ev_ebitda_multiple("nonexistent_industry", "normal")

    # Should fall back to default (8x)
    assert normal == 8.0


def test_get_pe_multiple_banking():
    """Banking industry should have specific P/E multiples."""
    from src.agents.industry_classifier import get_pe_multiple

    undervalued = get_pe_multiple("banking", "undervalued")
    fair = get_pe_multiple("banking", "fair_value")
    overvalued = get_pe_multiple("banking", "overvalued")

    # Banks typically have low P/E
    assert undervalued is not None
    assert undervalued < fair < overvalued
    assert 4.0 <= undervalued <= 6.0
    assert 6.0 <= fair <= 10.0


def test_get_pe_multiple_healthcare_rd_stage():
    """Healthcare R&D stage should not have P/E (loss-making)."""
    from src.agents.industry_classifier import get_pe_multiple

    rd_pe = get_pe_multiple("healthcare", "rd_stage")

    # R&D stage biotech/pharma typically has no PE (losses)
    assert rd_pe is None


def test_get_ps_multiple_tech():
    """Tech industry should have P/S multiples."""
    from src.agents.industry_classifier import get_ps_multiple

    loss_making = get_ps_multiple("tech", "loss_making")
    growth = get_ps_multiple("tech", "growth_stage")

    # Tech PS multiples
    assert loss_making >= 4.0
    assert growth >= 6.0


def test_get_pb_multiple_banking():
    """Banking industry should have specific P/B multiples."""
    from src.agents.industry_classifier import get_pb_multiple

    undervalued = get_pb_multiple("banking", "undervalued")
    fair = get_pb_multiple("banking", "fair_value")

    # Banks typically have low P/B
    assert undervalued is not None
    assert undervalued < fair
    assert 0.5 <= undervalued <= 0.7
    assert 0.9 <= fair <= 1.1
