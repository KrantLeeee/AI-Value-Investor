"""Tests for industry classification confidence mechanism."""

import pytest
from dataclasses import dataclass


def test_classification_result_dataclass():
    """Test IndustryClassificationResult dataclass structure"""
    from src.agents.industry_classifier import IndustryClassificationResult

    result = IndustryClassificationResult(
        industry_type='bank',
        display_name='银行',
        confidence=0.95,
        confidence_factors={'keyword': 0.45},
        conservative_mode=False,
        classification_path=['关键词匹配: bank']
    )

    assert result.industry_type == 'bank'
    assert result.confidence == 0.95
    assert result.conservative_mode is False


def test_keyword_matching_primary():
    """Test primary keyword matching gives 0.45 confidence"""
    from src.agents.industry_classifier import match_keywords

    industry, score = match_keywords(
        company_name='招商银行股份有限公司',
        business_desc='商业银行业务',
        akshare_industry='银行'
    )

    assert industry == 'bank'
    assert score >= 0.45  # Primary keyword in company name


def test_confidence_threshold_triggers_conservative():
    """Test confidence below 0.5 triggers conservative mode"""
    from src.agents.industry_classifier import classify_industry_with_confidence

    result = classify_industry_with_confidence(
        stock_code='999999',
        company_info={'name': '某某公司', 'business_description': '综合业务'},
        metrics={}
    )

    # Unknown company should fall back to generic with low confidence
    assert result.conservative_mode is True
    assert result.confidence < 0.5


def test_high_confidence_no_conservative():
    """Test high confidence does not trigger conservative mode"""
    from src.agents.industry_classifier import classify_industry_with_confidence

    result = classify_industry_with_confidence(
        stock_code='600036',
        company_info={
            'name': '招商银行',
            'business_description': '商业银行业务',
            'akshare_industry': '银行'
        },
        metrics={'debt_equity': 10.0, 'net_margin': 35.0, 'roe': 12.0}
    )

    assert result.industry_type == 'bank'
    assert result.conservative_mode is False
    assert result.confidence >= 0.5
