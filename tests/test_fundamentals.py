"""Tests for the Fundamentals Agent"""

import pytest


def test_cycle_adjusted_scoring_uses_5yr_avg():
    """Test cyclical industries use 5-year average for scoring"""
    from src.agents.fundamentals import calculate_fundamentals_score

    metrics = {
        'roe': 5.0,  # Current year (low in cycle)
        'roe_5yr_avg': 15.0,  # 5-year average
        'net_margin': 3.0,
        'net_margin_5yr_avg': 12.0
    }
    industry_config = {'scoring_mode': 'cycle_adjusted'}

    result = calculate_fundamentals_score(metrics, industry_config)

    # Should use 5-year averages, not current values
    assert result['roe_for_scoring'] == 15.0
    assert result['net_margin_for_scoring'] == 12.0
    assert 'adjustments' in result


def test_non_cyclical_uses_current():
    """Test non-cyclical industries use current values"""
    from src.agents.fundamentals import calculate_fundamentals_score

    metrics = {'roe': 20.0, 'roe_5yr_avg': 18.0}
    industry_config = {}  # No scoring_mode

    result = calculate_fundamentals_score(metrics, industry_config)

    assert result['roe_for_scoring'] == 20.0


def test_data_freshness_thresholds():
    """Test data freshness thresholds are set correctly"""
    from src.agents.fundamentals import DATA_FRESHNESS_CONFIG

    # Per spec: 180 days warning, 270 days critical
    assert DATA_FRESHNESS_CONFIG['warning_threshold_days'] == 180
    assert DATA_FRESHNESS_CONFIG['critical_threshold_days'] == 270


def test_evaluate_fcf_growth_investment():
    """Test growth investment FCF is not penalized"""
    from src.agents.fundamentals import evaluate_fcf

    result = evaluate_fcf(
        fcf=-500_000_000,      # Negative FCF
        net_income=1_000_000_000,  # Profitable
        capex=800_000_000,     # High capex > 50% of NI
        revenue_growth=25,     # High growth > 20%
        industry_type='tech'
    )

    assert result['score_impact'] == 0  # No penalty
    assert result['fcf_type'] == 'growth_investment'


def test_evaluate_fcf_operational_issue():
    """Test low-growth negative FCF is penalized"""
    from src.agents.fundamentals import evaluate_fcf

    result = evaluate_fcf(
        fcf=-200_000_000,
        net_income=300_000_000,
        capex=100_000_000,
        revenue_growth=5,  # Low growth
        industry_type='retail'
    )

    assert result['score_impact'] < 0  # Penalty
    assert result['fcf_type'] == 'operational_issue'


def test_evaluate_fcf_loss_company():
    """Test loss company FCF handled separately"""
    from src.agents.fundamentals import evaluate_fcf

    result = evaluate_fcf(
        fcf=-100_000_000,
        net_income=-200_000_000,  # Loss-making
        capex=50_000_000,
        revenue_growth=30,
        industry_type='biotech'
    )

    assert result['fcf_type'] == 'loss_company'
    assert result['score_impact'] == -5  # Light penalty


def test_detect_data_contradictions_ni_ocf():
    """Test detection of NI vs OCF divergence"""
    from src.agents.fundamentals import detect_data_contradictions

    metrics = {
        'net_income': 1_000_000_000,  # Positive NI
        'ocf': -500_000_000,          # Negative OCF
        'ocf_prev_year': -300_000_000  # Also negative last year
    }

    contradictions = detect_data_contradictions(metrics)

    assert len(contradictions) > 0
    assert any(c['type'] == 'ni_ocf_divergence_persistent' for c in contradictions)


def test_detect_data_contradictions_roe_jump():
    """Test detection of ROE historical jump"""
    from src.agents.fundamentals import detect_data_contradictions

    metrics = {
        'roe': 5.0,
        'roe_5yr_avg': 30.0  # 25 point difference
    }

    contradictions = detect_data_contradictions(metrics)

    assert any(c['type'] == 'roe_historical_jump' for c in contradictions)


def test_get_data_confidence_score():
    """Test data confidence scoring"""
    from src.agents.fundamentals import get_data_confidence_score

    # High severity issues
    contradictions = [
        {'severity': 'high', 'type': 'test', 'detail': 'test'}
    ]
    score = get_data_confidence_score(contradictions)
    assert score == 0.7  # 1.0 - 0.3

    # Multiple issues
    contradictions = [
        {'severity': 'high', 'type': 'test', 'detail': 'test'},
        {'severity': 'medium', 'type': 'test', 'detail': 'test'}
    ]
    score = get_data_confidence_score(contradictions)
    assert score == 0.55  # 1.0 - 0.3 - 0.15
