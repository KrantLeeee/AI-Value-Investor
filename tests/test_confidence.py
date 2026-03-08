"""Tests for confidence calculation engine."""

import pytest
from src.agents.confidence import (
    calculate_confidence,
    calculate_fundamentals_confidence,
    calculate_valuation_confidence,
    calculate_buffett_confidence,
    calculate_graham_confidence,
    calculate_sentiment_confidence,
    calculate_contrarian_confidence,
)
from src.data.models import QualityReport


@pytest.fixture
def high_quality_report():
    """High quality data."""
    return QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.95,
        data_completeness=0.98,
        stale_fields=[],
        records_checked={},
    )


@pytest.fixture
def low_quality_report():
    """Low quality data."""
    from src.data.models import QualityFlag
    return QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[
            QualityFlag(
                flag="stale_data",
                field="revenue",
                detail="Data is stale",
                severity="critical"
            )
        ],
        overall_quality_score=0.40,
        data_completeness=0.60,
        stale_fields=["revenue"],
        records_checked={},
    )


def test_calculate_confidence_bounds():
    """Confidence should be bounded between 0.10 and 0.85."""
    # Very low strength and agreement
    conf = calculate_confidence("test", 0.0, 0.0)
    assert conf == 0.10

    # Very high strength and agreement with perfect quality
    quality = QualityReport(
        ticker="TEST", market="a_share", flags=[],
        overall_quality_score=1.0, data_completeness=1.0,
        stale_fields=[], records_checked={}
    )
    conf = calculate_confidence("test", 1.0, 1.0, quality)
    assert conf == 0.85  # Capped at 0.85


def test_calculate_confidence_quality_penalty(low_quality_report):
    """Low quality data should reduce confidence."""
    # Same signal with different quality
    conf_high = calculate_confidence("test", 0.8, 0.8, None)  # Default 0.5 quality
    conf_low = calculate_confidence("test", 0.8, 0.8, low_quality_report)

    assert conf_low < conf_high
    # Low quality (0.40) should significantly reduce confidence
    assert conf_low < 0.6


def test_fundamentals_confidence_strong_signal(high_quality_report):
    """Strong fundamentals should give high confidence."""
    # Very high score (90/100), all dimensions strong
    conf = calculate_fundamentals_confidence(
        score=90,
        revenue_score=23,
        profitability_score=22,
        leverage_score=23,
        cash_flow_score=22,
        quality_report=high_quality_report,
    )

    assert conf > 0.60  # Strong signal
    assert conf <= 0.85  # Capped


def test_fundamentals_confidence_weak_signal(high_quality_report):
    """Weak fundamentals near neutral should give low confidence."""
    # Score near neutral (57.5)
    conf = calculate_fundamentals_confidence(
        score=58,
        revenue_score=14,
        profitability_score=15,
        leverage_score=14,
        cash_flow_score=15,
        quality_report=high_quality_report,
    )

    assert conf < 0.40  # Weak signal


def test_fundamentals_confidence_disagreement(high_quality_report):
    """Disagreement among dimensions should reduce confidence."""
    # High total score but dimensions disagree
    conf = calculate_fundamentals_confidence(
        score=70,
        revenue_score=23,  # High
        profitability_score=22,  # High
        leverage_score=8,  # Low
        cash_flow_score=17,  # Medium
        quality_report=high_quality_report,
    )

    # Should be lower than if all dimensions agreed
    conf_agree = calculate_fundamentals_confidence(
        score=70,
        revenue_score=18,
        profitability_score=17,
        leverage_score=18,
        cash_flow_score=17,
        quality_report=high_quality_report,
    )

    assert conf < conf_agree


def test_valuation_confidence_strong_undervalued(high_quality_report):
    """Strong undervaluation should give high confidence."""
    conf = calculate_valuation_confidence(
        margin_of_safety=0.40,  # 40% undervalued
        dcf_per_share=20.0,
        graham_number=18.0,
        current_price=12.0,  # Both DCF and Graham agree: bullish
        quality_report=high_quality_report,
    )

    assert conf > 0.50


def test_valuation_confidence_methods_disagree(high_quality_report):
    """DCF and Graham disagreeing should reduce confidence."""
    conf = calculate_valuation_confidence(
        margin_of_safety=0.20,
        dcf_per_share=20.0,  # Suggests bullish
        graham_number=10.0,  # Suggests bearish
        current_price=15.0,
        quality_report=high_quality_report,
    )

    # Disagreement should result in lower confidence
    assert conf < 0.50


def test_buffett_confidence_high_roe_consistency(high_quality_report):
    """Consistent high ROE should give high confidence."""
    conf = calculate_buffett_confidence(
        roe_values=[22.0, 23.0, 24.0, 23.5, 22.5],
        net_income_values=[1e9, 1.1e9, 1.2e9, 1.25e9, 1.3e9],
        has_pricing_power=True,
        moat_type="品牌护城河",
        quality_report=high_quality_report,
    )

    assert conf > 0.60


def test_buffett_confidence_code_llm_disagree(high_quality_report):
    """Code and LLM disagreement should reduce confidence."""
    conf = calculate_buffett_confidence(
        roe_values=[8.0, 7.0, 6.0],  # Low ROE
        net_income_values=[1e8, 0.8e8, 0.6e8],  # Declining
        has_pricing_power=True,  # LLM says has power
        moat_type="强大护城河",  # LLM says strong moat
        quality_report=high_quality_report,
    )

    # Disagreement should reduce confidence
    assert conf < 0.50


def test_graham_confidence_many_standards_passed(high_quality_report):
    """Passing many standards should give high confidence."""
    conf = calculate_graham_confidence(
        standards_passed=6,
        standards_details={
            "pe_ratio": True,
            "pb_ratio": True,
            "dividend_yield": True,
            "debt_ratio": True,
            "current_ratio": True,
            "earning_stability": True,
            "earnings_growth": False,
        },
        quality_report=high_quality_report,
    )

    assert conf > 0.60


def test_graham_confidence_few_standards_passed(high_quality_report):
    """Passing few standards should give low confidence."""
    conf = calculate_graham_confidence(
        standards_passed=2,
        standards_details={
            "pe_ratio": True,
            "pb_ratio": False,
            "dividend_yield": False,
            "debt_ratio": True,
            "current_ratio": False,
            "earning_stability": False,
            "earnings_growth": False,
        },
        quality_report=high_quality_report,
    )

    assert conf < 0.60  # 2/7 standards is weak


def test_sentiment_confidence_extreme_positive(high_quality_report):
    """Extremely positive sentiment should give high confidence."""
    conf = calculate_sentiment_confidence(
        sentiment_score=0.85,
        positive_count=15,
        negative_count=1,
        neutral_count=2,
        quality_report=high_quality_report,
    )

    assert conf > 0.60


def test_sentiment_confidence_mixed_sentiment(high_quality_report):
    """Mixed sentiment should give low confidence."""
    conf = calculate_sentiment_confidence(
        sentiment_score=0.15,
        positive_count=6,
        negative_count=5,
        neutral_count=7,
        quality_report=high_quality_report,
    )

    assert conf < 0.40


def test_contrarian_confidence_strong_consensus(high_quality_report):
    """Strong consensus with many challenges should give high confidence."""
    conf = calculate_contrarian_confidence(
        consensus_strength=0.80,
        mode="bear_case",
        num_challenges=4,
        quality_report=high_quality_report,
    )

    assert conf > 0.50


def test_contrarian_confidence_weak_consensus(high_quality_report):
    """Weak consensus should give lower confidence."""
    conf = calculate_contrarian_confidence(
        consensus_strength=0.50,
        mode="critical_questions",
        num_challenges=2,
        quality_report=high_quality_report,
    )

    assert conf < 0.65  # Moderate confidence for moderate consensus


def test_confidence_none_quality_defaults_to_half():
    """Missing quality report should default to 0.5."""
    conf = calculate_confidence("test", 0.8, 0.8, None)

    # Base = 0.8, quality = 0.5, result should be ~0.40
    assert 0.35 < conf < 0.45
