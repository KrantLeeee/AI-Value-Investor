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
