"""Tests for data source modules."""

import pytest


def test_fetch_company_basic_info():
    """Test company basic info fetching includes required fields"""
    from src.data.akshare_source import fetch_company_basic_info

    result = fetch_company_basic_info('600519')

    assert 'established_date' in result
    assert 'registered_capital' in result
    assert 'employee_count' in result
