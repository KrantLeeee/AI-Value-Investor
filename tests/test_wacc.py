"""Tests for WACC calculation module."""

import pytest
import numpy as np
from unittest.mock import Mock, patch

from src.agents.wacc import (
    get_risk_free_rate,
    calculate_beta,
    get_interest_bearing_debt,
    calculate_cost_of_debt,
    calculate_effective_tax_rate,
    calculate_cost_of_equity,
    calculate_wacc,
    generate_sensitivity_matrix,
    MRP,
    RF_FALLBACK,
)


def test_get_risk_free_rate():
    """Risk-free rate should return fallback value."""
    rf = get_risk_free_rate()
    assert rf == RF_FALLBACK
    assert 0.01 <= rf <= 0.05  # Reasonable range for treasury yield


def test_calculate_beta_not_implemented():
    """Beta calculation currently returns None (not implemented)."""
    beta = calculate_beta("600000.SH")
    assert beta is None


@patch("src.agents.wacc.get_balance_sheets")
def test_get_interest_bearing_debt(mock_get_bs):
    """Should calculate debt from balance sheet components."""
    mock_get_bs.return_value = [
        {
            "short_term_debt": 1000000000,  # 10亿
            "long_term_debt": 2000000000,    # 20亿
            "bonds_payable": 500000000,      # 5亿
        }
    ]

    debt = get_interest_bearing_debt("600000.SH")
    assert debt == 3500000000  # 35亿


@patch("src.agents.wacc.get_balance_sheets")
def test_get_interest_bearing_debt_no_data(mock_get_bs):
    """Should return 0 if no balance sheet data."""
    mock_get_bs.return_value = []

    debt = get_interest_bearing_debt("600000.SH")
    assert debt == 0.0


@patch("src.agents.wacc.get_balance_sheets")
def test_get_interest_bearing_debt_fallback_to_liabilities(mock_get_bs):
    """Should use 50% of total liabilities if specific debt unavailable."""
    mock_get_bs.return_value = [
        {
            "short_term_debt": None,
            "long_term_debt": None,
            "bonds_payable": None,
            "total_liabilities": 10000000000,  # 100亿
            "total_equity": 5000000000,        # 50亿
        }
    ]

    debt = get_interest_bearing_debt("600000.SH")
    assert debt == 5000000000  # 50亿 (50% of liabilities)


@patch("src.agents.wacc.get_income_statements")
@patch("src.agents.wacc.get_interest_bearing_debt")
def test_calculate_cost_of_debt(mock_get_debt, mock_get_income):
    """Should calculate cost of debt from interest expense."""
    mock_get_income.return_value = [
        {"interest_expense": 200000000}  # 2亿利息
    ]
    mock_get_debt.return_value = 4000000000  # 40亿债务

    rd = calculate_cost_of_debt("600000.SH")
    assert rd == 0.05  # 2亿 / 40亿 = 5%


@patch("src.agents.wacc.get_income_statements")
def test_calculate_cost_of_debt_fallback(mock_get_income):
    """Should return 5% fallback if no data."""
    mock_get_income.return_value = []

    rd = calculate_cost_of_debt("600000.SH")
    assert rd == 0.05


@patch("src.agents.wacc.get_income_statements")
def test_calculate_effective_tax_rate(mock_get_income):
    """Should calculate effective tax rate from actual taxes paid."""
    mock_get_income.return_value = [
        {
            "profit_before_tax": 1000000000,  # 10亿利润
            "income_tax_expense": 250000000,   # 2.5亿税
        }
    ]

    tc = calculate_effective_tax_rate("600000.SH")
    assert tc == 0.25  # 25%


@patch("src.agents.wacc.get_income_statements")
def test_calculate_effective_tax_rate_fallback(mock_get_income):
    """Should return 25% statutory rate if no data."""
    mock_get_income.return_value = []

    tc = calculate_effective_tax_rate("600000.SH")
    assert tc == 0.25


@patch("src.agents.wacc.calculate_beta")
@patch("src.agents.wacc.get_risk_free_rate")
@patch("src.agents.wacc.get_scoring_thresholds")
def test_calculate_cost_of_equity(mock_get_thresholds, mock_get_rf, mock_calc_beta):
    """Should calculate cost of equity using CAPM."""
    mock_get_rf.return_value = 0.03  # 3% rf
    mock_calc_beta.return_value = None  # Will use default beta
    mock_get_thresholds.return_value = {"default_beta": 1.0}

    re = calculate_cost_of_equity("600000.SH", "default")

    # re = rf + β × MRP = 0.03 + 1.0 × 0.055 = 0.085 (8.5%)
    assert abs(re - 0.085) < 0.001


@patch("src.agents.wacc.calculate_beta")
@patch("src.agents.wacc.get_risk_free_rate")
def test_calculate_cost_of_equity_with_beta(mock_get_rf, mock_calc_beta):
    """Should use provided beta if available."""
    mock_get_rf.return_value = 0.03
    mock_calc_beta.return_value = 1.2  # Higher beta

    re = calculate_cost_of_equity("600000.SH", "tech", beta=1.2)

    # re = 0.03 + 1.2 × 0.055 = 0.096 (9.6%)
    assert abs(re - 0.096) < 0.001


@patch("src.agents.wacc.get_latest_prices")
@patch("src.agents.wacc.get_income_statements")
@patch("src.agents.wacc.get_interest_bearing_debt")
@patch("src.agents.wacc.calculate_cost_of_equity")
@patch("src.agents.wacc.calculate_cost_of_debt")
@patch("src.agents.wacc.calculate_effective_tax_rate")
def test_calculate_wacc_full(
    mock_tc, mock_rd, mock_re, mock_debt, mock_income, mock_prices
):
    """Should calculate WACC with all components."""
    # Mock data
    mock_prices.return_value = [{"close": 10.0}]  # ¥10/股
    mock_income.return_value = [
        {
            "shares_outstanding": 1000000000,  # 10亿股
            "net_income": 500000000,
            "eps": 0.5,
        }
    ]
    mock_debt.return_value = 2000000000  # 20亿债务
    mock_re.return_value = 0.10  # 10% equity cost
    mock_rd.return_value = 0.05  # 5% debt cost
    mock_tc.return_value = 0.25  # 25% tax rate

    result = calculate_wacc("600000.SH", "SH", "default", 10.0)

    # E = 10亿股 × ¥10 = 100亿
    # D = 20亿
    # Total = 120亿
    # E/V = 100/120 = 83.3%
    # D/V = 20/120 = 16.7%
    # WACC = 0.833 × 0.10 + 0.167 × 0.05 × (1-0.25)
    #      = 0.0833 + 0.00625 = 0.08955 (~9.0%)

    assert result["fallback_used"] is False
    assert abs(result["equity_weight"] - 0.833) < 0.01
    assert abs(result["debt_weight"] - 0.167) < 0.01
    assert abs(result["wacc"] - 0.0896) < 0.005


@patch("src.agents.wacc.get_latest_prices")
@patch("src.agents.wacc.get_scoring_thresholds")
def test_calculate_wacc_fallback_no_price(mock_get_thresholds, mock_prices):
    """Should use industry midpoint if price unavailable."""
    mock_prices.return_value = []
    mock_get_thresholds.return_value = {"wacc_range": [0.08, 0.10]}

    result = calculate_wacc("600000.SH", "SH", "default")

    assert result["fallback_used"] is True
    assert result["wacc"] == 0.09  # Midpoint of [0.08, 0.10]


def test_generate_sensitivity_matrix():
    """Should generate sensitivity matrix for WACC × growth."""
    matrix_result = generate_sensitivity_matrix(
        base_fcf=1000000000,  # 10亿 FCF
        wacc_current=0.10,
        shares=1000000000,  # 10亿股
        wacc_range=(0.08, 0.12),
        growth_range=(0.0, 0.10),
        terminal_growth=0.03,
        years=10,
    )

    assert "matrix" in matrix_result
    assert "wacc_values" in matrix_result
    assert "growth_values" in matrix_result
    assert "current_wacc" in matrix_result

    # Matrix should be 7x7 (7 WACC values × 7 growth values)
    assert len(matrix_result["matrix"]) == 7
    assert len(matrix_result["matrix"][0]) == 7

    # WACC values should range from 0.08 to 0.12
    assert abs(matrix_result["wacc_values"][0] - 0.08) < 0.01
    assert abs(matrix_result["wacc_values"][-1] - 0.12) < 0.01

    # Growth values should range from 0.0 to 0.10
    assert abs(matrix_result["growth_values"][0] - 0.0) < 0.01
    assert abs(matrix_result["growth_values"][-1] - 0.10) < 0.01

    # All values should be positive
    for row in matrix_result["matrix"]:
        for val in row:
            assert val > 0


def test_generate_sensitivity_matrix_values_decrease_with_wacc():
    """DCF value should decrease as WACC increases (all else equal)."""
    matrix_result = generate_sensitivity_matrix(
        base_fcf=1000000000,
        wacc_current=0.10,
        shares=1000000000,
        wacc_range=(0.08, 0.12),
        growth_range=(0.05, 0.05),  # Fixed growth
        terminal_growth=0.03,
        years=10,
    )

    # For fixed growth, DCF should decrease as WACC increases
    fixed_growth_col = [row[0] for row in matrix_result["matrix"]]
    for i in range(len(fixed_growth_col) - 1):
        assert fixed_growth_col[i] > fixed_growth_col[i + 1]


def test_generate_sensitivity_matrix_values_increase_with_growth():
    """DCF value should increase as growth rate increases (all else equal)."""
    matrix_result = generate_sensitivity_matrix(
        base_fcf=1000000000,
        wacc_current=0.10,
        shares=1000000000,
        wacc_range=(0.10, 0.10),  # Fixed WACC
        growth_range=(0.0, 0.10),
        terminal_growth=0.03,
        years=10,
    )

    # For fixed WACC, DCF should increase as growth increases
    fixed_wacc_row = matrix_result["matrix"][0]
    for i in range(len(fixed_wacc_row) - 1):
        assert fixed_wacc_row[i] < fixed_wacc_row[i + 1]
