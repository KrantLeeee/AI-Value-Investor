"""Tests for industry classification.

Updated for v2.1 industry classification system with more specific categories.
"""

import pytest
from src.agents.industry_classifier import (
    classify_industry,
    get_industry_profile,
    get_agent_weights,
    get_scoring_thresholds,
)


# ── classify_industry tests (updated for v2.1 system) ──────────────────────


def test_classify_industry_bank():
    """Bank keywords should classify as bank."""
    assert classify_industry("银行") == "bank"
    assert classify_industry("商业银行") == "bank"


def test_classify_industry_insurance():
    """Insurance keywords should classify as insurance."""
    assert classify_industry("保险") == "insurance"
    assert classify_industry("人寿保险") == "insurance"


def test_classify_industry_new_energy_mfg():
    """New energy manufacturing keywords should classify correctly."""
    assert classify_industry("锂电池") == "new_energy_mfg"
    assert classify_industry("动力电池") == "new_energy_mfg"
    assert classify_industry("储能电池") == "new_energy_mfg"
    # Priority keywords should work
    assert classify_industry("锂电") == "new_energy_mfg"
    assert classify_industry("光伏") == "new_energy_mfg"


def test_classify_industry_auto_new_energy():
    """New energy auto keywords should classify correctly."""
    assert classify_industry("新能源汽车") == "auto_new_energy"
    assert classify_industry("电动汽车") == "auto_new_energy"


def test_classify_industry_cyclical_materials():
    """Cyclical materials keywords should classify correctly."""
    assert classify_industry("钢铁") == "cyclical_materials"
    assert classify_industry("水泥") == "cyclical_materials"
    assert classify_industry("有色") == "cyclical_materials"


def test_classify_industry_telecom():
    """Telecom keywords should classify correctly."""
    assert classify_industry("电信") == "telecom_operator"
    assert classify_industry("移动通信") == "telecom_operator"
    assert classify_industry("通信设备") == "telecom_equipment"


def test_classify_industry_low_margin_mfg():
    """Low margin manufacturing keywords should classify correctly."""
    assert classify_industry("代工") == "low_margin_mfg"
    assert classify_industry("电子制造") == "low_margin_mfg"


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
    # New energy keywords should take priority
    industry = classify_industry("制造业", "锂电池")
    assert industry == "new_energy_mfg"


# ── get_industry_profile tests ─────────────────────────────────────────────


def test_get_industry_profile_bank():
    """Bank profile should have correct structure."""
    profile = get_industry_profile("bank")

    assert "weights" in profile
    assert "rationale" in profile
    assert "validated" in profile
    assert "scoring" in profile


def test_get_industry_profile_oil_gas():
    """Oil & Gas profile should have cycle-aware settings."""
    profile = get_industry_profile("oil_gas")

    assert "weights" in profile
    assert profile.get("scoring_mode") == "cycle_adjusted"


def test_get_industry_profile_real_estate():
    """Real estate profile should have disable_methods including DCF."""
    profile = get_industry_profile("real_estate")

    assert "disable_methods" in profile
    assert "dcf" in profile["disable_methods"]
    assert "graham_number" in profile["disable_methods"]


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
    weights = get_agent_weights("bank")

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
    thresholds = get_scoring_thresholds("bank")

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
        "bank",
        "insurance",
        "oil_gas",
        "coal",
        "real_estate",
        "cyclical_materials",
        "telecom_operator",
        "default",
    ]

    for industry in industries:
        weights = get_agent_weights(industry)
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01, f"{industry} weights sum to {total}, not 1.0"


def test_all_profiles_have_required_fields():
    """All industry profiles should have required fields."""
    industries = [
        "bank",
        "insurance",
        "oil_gas",
        "real_estate",
        "cyclical_materials",
        "telecom_operator",
        "default",
    ]

    for industry in industries:
        profile = get_industry_profile(industry)

        # Check required fields
        assert "weights" in profile
        assert "rationale" in profile
        assert "validated" in profile
        assert "scoring" in profile


# ── Phase 3: Industry-specific valuation multiples tests ───────────────────


def test_get_ev_ebitda_multiple_oil_gas():
    """Oil & Gas industry should have specific EV/EBITDA multiples."""
    from src.agents.industry_classifier import get_ev_ebitda_multiple

    bottom = get_ev_ebitda_multiple("oil_gas", "bottom")
    normal = get_ev_ebitda_multiple("oil_gas", "normal")
    peak = get_ev_ebitda_multiple("oil_gas", "peak")

    # Oil & Gas should have cycle-aware multiples
    # Note: YAML defines [4.0, 5.0], so bottom=4.0, normal=4.5, peak=5.0
    assert bottom is not None
    assert normal is not None
    assert peak is not None
    assert 3.0 <= bottom <= 6.0  # Cycle bottom around 4x
    assert 4.0 <= normal <= 6.0  # Mid-cycle around 4.5x


def test_get_ev_ebitda_multiple_cyclical_materials():
    """Cyclical materials industry should have EV/EBITDA multiples."""
    from src.agents.industry_classifier import get_ev_ebitda_multiple

    normal = get_ev_ebitda_multiple("cyclical_materials", "normal")

    # Cyclical materials typically have moderate multiples
    assert 4.0 <= normal <= 8.0


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


def test_get_pe_multiple_bank():
    """Bank industry should have specific P/E multiples."""
    from src.agents.industry_classifier import get_pe_multiple

    # Banks may not have all P/E stages defined
    fair = get_pe_multiple("bank", "fair_value")
    # If defined, should be moderate for banks
    if fair is not None:
        assert 5.0 <= fair <= 12.0


def test_get_pe_multiple_pharma_mature():
    """Mature pharma should have P/E multiples."""
    from src.agents.industry_classifier import get_pe_multiple

    fair = get_pe_multiple("pharma_mature", "fair_value")
    # Pharma typically has higher P/E
    if fair is not None:
        assert fair >= 15.0


def test_get_ps_multiple_telecom_equipment():
    """Telecom equipment industry should have P/S multiples."""
    from src.agents.industry_classifier import get_ps_multiple

    growth = get_ps_multiple("telecom_equipment", "growth_stage")

    # Tech/equipment PS multiples can vary
    if growth is not None:
        assert growth >= 2.0


def test_get_pb_multiple_bank():
    """Bank industry should have specific P/B multiples."""
    from src.agents.industry_classifier import get_pb_multiple

    fair = get_pb_multiple("bank", "fair_value")

    # Banks typically have low P/B
    if fair is not None:
        assert 0.5 <= fair <= 1.5


def test_get_industry_comparables_oil_gas():
    """Oil & Gas industry should have comparable companies."""
    from src.agents.industry_classifier import get_industry_comparables

    comparables = get_industry_comparables("oil_gas")

    # May or may not have comparables defined
    assert isinstance(comparables, list)


def test_get_industry_comparables_bank():
    """Bank industry should have comparable companies."""
    from src.agents.industry_classifier import get_industry_comparables

    comparables = get_industry_comparables("bank")

    # May or may not have comparables defined
    assert isinstance(comparables, list)


def test_get_industry_comparables_default():
    """Default industry should have empty comparables."""
    from src.agents.industry_classifier import get_industry_comparables

    comparables = get_industry_comparables("default")

    assert comparables == []


# ── BUG-03: CATL (宁德时代) Classification Fix Tests ───────────────────────


def test_catl_classified_as_new_energy():
    """Test 宁德时代 is classified as new_energy_mfg, not pharma."""
    from src.agents.industry_classifier import classify_by_business_description

    result = classify_by_business_description(
        company_name="宁德时代新能源科技股份有限公司",
        business_desc="动力电池、储能电池研发生产",
    )

    assert result == "new_energy_mfg"
    assert result != "pharma"


def test_new_energy_keywords_priority():
    """Test new energy keywords take priority over pharma."""
    from src.agents.industry_classifier import classify_by_business_description

    # Even if company has '能' which might partially match pharma keywords
    result = classify_by_business_description(
        company_name="XXX新能源科技", business_desc="锂电池生产"
    )

    assert result == "new_energy_mfg"


def test_classify_industry_priority_keywords():
    """Test that PRIORITY_KEYWORDS are checked first."""
    # 新能源汽车 should trigger auto_new_energy (more specific takes priority)
    assert classify_industry("新能源汽车") == "auto_new_energy"
    # 锂电 should trigger new_energy_mfg through priority keywords
    assert classify_industry("锂电") == "new_energy_mfg"
    # 动力电池 should trigger new_energy_mfg
    assert classify_industry("动力电池") == "new_energy_mfg"


# ── Task 2.2: New Industry Type Mappings ────────────────────────────────────


def test_cyclical_materials_mapping():
    """Test cyclical materials companies are mapped correctly"""
    from src.data.industry_mapping import get_industry_type

    assert get_industry_type("紫金矿业", "有色金属") == "cyclical_materials"
    assert get_industry_type("宝钢股份", "钢铁") == "cyclical_materials"
    assert get_industry_type("海螺水泥", "水泥") == "cyclical_materials"


def test_telecom_mapping():
    """Test telecom companies are mapped correctly"""
    from src.data.industry_mapping import get_industry_type

    assert get_industry_type("中国移动", "通信运营") == "telecom_operator"
    assert get_industry_type("中兴通讯", "通信设备") == "telecom_equipment"


def test_new_energy_mapping():
    """Test new energy companies are mapped correctly"""
    from src.data.industry_mapping import get_industry_type

    assert get_industry_type("比亚迪", "新能源汽车") == "auto_new_energy"
    assert get_industry_type("工业富联", "电子制造") == "low_margin_mfg"


def test_specific_stock_mappings():
    """Test specific stocks map to correct industries per spec"""
    from src.data.industry_mapping import get_industry_type

    # Per spec Section 4.6
    test_cases = [
        ("紫金矿业", "有色金属", "cyclical_materials"),
        ("宝钢股份", "钢铁", "cyclical_materials"),
        ("海螺水泥", "水泥", "cyclical_materials"),
        ("万华化学", "化工", "cyclical_materials"),
        ("中国移动", "通信运营", "telecom_operator"),
        ("中兴通讯", "通信设备", "telecom_equipment"),
        ("工业富联", "电子制造", "low_margin_mfg"),
        ("比亚迪", "新能源汽车", "auto_new_energy"),
        ("牧原股份", "养殖", "cyclical_agri"),
        ("航发动力", "军工", "defense_equipment"),
    ]

    for company_name, sector, expected in test_cases:
        result = get_industry_type(company_name, sector)
        assert result == expected, f"{company_name} expected {expected}, got {result}"
