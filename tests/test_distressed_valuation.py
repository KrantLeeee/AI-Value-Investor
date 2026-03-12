"""Tests for distressed company valuation framework."""

import pytest


def test_detect_distressed_company():
    """Test distressed company detection"""
    from src.agents.valuation import detect_distressed_company

    # Clearly distressed
    metrics = {
        'net_margin': -25,
        'roe': -20,
        'fcf': -100_000_000,
        'ocf': -50_000_000,
        'debt_equity': 150
    }
    assert detect_distressed_company(metrics) is True

    # Healthy company
    metrics = {
        'net_margin': 15,
        'roe': 12,
        'fcf': 100_000_000,
        'ocf': 150_000_000,
        'debt_equity': 50
    }
    assert detect_distressed_company(metrics) is False


def test_classify_distressed_type():
    """Test distressed company type classification"""
    from src.agents.valuation import classify_distressed_type

    # Asset-intensive (retail)
    result = classify_distressed_type(
        company_info={'name': '永辉超市', 'business_description': '超市零售'},
        metrics={}
    )
    assert result == 'asset_intensive'

    # Contract-based (infrastructure)
    result = classify_distressed_type(
        company_info={'name': '碧水源', 'business_description': 'PPP环保工程'},
        metrics={}
    )
    assert result == 'contract_based'

    # Receivables-heavy
    result = classify_distressed_type(
        company_info={'name': 'XX公司', 'business_description': '一般业务'},
        metrics={'accounts_receivable': 3_000_000_000, 'revenue': 5_000_000_000}
    )
    assert result == 'receivables_heavy'


def test_is_delisting_risk():
    """Test delisting risk assessment"""
    from src.agents.valuation import is_delisting_risk

    # High risk: 3 consecutive losses
    metrics = {'net_income_history': [100, -50, -80, -120]}
    result = is_delisting_risk(metrics)
    assert result['level'] == 'HIGH'
    assert '连续三年亏损' in result['factors']

    # Medium risk: 2 consecutive losses
    metrics = {'net_income_history': [100, 50, -80, -120]}
    result = is_delisting_risk(metrics)
    assert result['level'] == 'MEDIUM'


def test_distressed_valuation_asset_intensive():
    """Test asset-intensive distressed valuation"""
    from src.agents.valuation import distressed_valuation

    metrics = {
        'fixed_assets': 10_000_000_000,
        'inventory': 5_000_000_000,
        'revenue': 50_000_000_000,
        'shares': 1_000_000_000,
        'net_income_history': [100, -50]
    }
    company_info = {'name': '永辉超市', 'business_description': '超市零售'}

    result = distressed_valuation(metrics, company_info)

    assert result['distressed_type'] == 'asset_intensive'
    assert 'asset_replacement' in result
    assert result['asset_replacement']['value'] > 0
