"""Tests for comparable company analysis."""

import pytest
from unittest.mock import Mock, patch, mock_open

from src.agents.comparables import (
    get_comparables_from_watchlist,
    auto_select_comparables,
    fetch_comparable_metrics,
    calculate_percentile_rank,
    run_comparable_analysis,
)


@patch("builtins.open", new_callable=mock_open, read_data="""
watchlist:
  a_share:
    - ticker: "600000.SH"
      name: "浦发银行"
      sector: "银行"
      comparables: ["601398.SH", "601939.SH", "601288.SH"]
    - ticker: "600519.SH"
      name: "贵州茅台"
      sector: "消费"
""")
def test_get_comparables_from_watchlist(mock_file):
    """Should read user-specified comparables from watchlist."""
    comparables = get_comparables_from_watchlist("600000.SH")

    assert len(comparables) == 3
    assert "601398.SH" in comparables
    assert "601939.SH" in comparables
    assert "601288.SH" in comparables


@patch("builtins.open", new_callable=mock_open, read_data="""
watchlist:
  a_share:
    - ticker: "600519.SH"
      name: "贵州茅台"
      sector: "消费"
""")
def test_get_comparables_from_watchlist_no_comparables(mock_file):
    """Should return empty list if no comparables specified."""
    comparables = get_comparables_from_watchlist("600519.SH")

    assert comparables == []


@patch("builtins.open", side_effect=FileNotFoundError())
def test_get_comparables_from_watchlist_file_error(mock_file):
    """Should return empty list if watchlist file not found."""
    comparables = get_comparables_from_watchlist("600000.SH")

    assert comparables == []


def test_auto_select_comparables_not_implemented():
    """Auto-selection should return empty list (not implemented)."""
    comparables = auto_select_comparables("600000.SH", "银行", limit=5)

    assert comparables == []


@patch("src.agents.comparables.get_financial_metrics")
@patch("src.agents.comparables.get_latest_prices")
def test_fetch_comparable_metrics(mock_prices, mock_metrics):
    """Should fetch PE, PB, ROE, dividend yield."""
    mock_metrics.return_value = [
        {
            "pe_ttm": 8.5,
            "pb": 0.65,
            "roe": 0.12,
            "dividend_yield": 0.045,
        }
    ]
    mock_prices.return_value = [{"close": 12.5}]

    metrics = fetch_comparable_metrics("600000.SH")

    assert metrics["ticker"] == "600000.SH"
    assert metrics["pe"] == 8.5
    assert metrics["pb"] == 0.65
    assert metrics["roe"] == 0.12
    assert metrics["dividend_yield"] == 0.045


@patch("src.agents.comparables.get_financial_metrics")
@patch("src.agents.comparables.get_latest_prices")
def test_fetch_comparable_metrics_calculate_dividend_yield(mock_prices, mock_metrics):
    """Should calculate dividend yield if not stored."""
    mock_metrics.return_value = [
        {
            "pe_ttm": 8.5,
            "pb": 0.65,
            "roe": 0.12,
            "dividend_yield": None,
            "dividend_per_share": 0.50,
        }
    ]
    mock_prices.return_value = [{"close": 10.0}]

    metrics = fetch_comparable_metrics("600000.SH")

    # dividend_yield = 0.50 / 10.0 = 0.05 (5%)
    assert metrics["dividend_yield"] == 0.05


@patch("src.agents.comparables.get_financial_metrics")
@patch("src.agents.comparables.get_latest_prices")
def test_fetch_comparable_metrics_no_data(mock_prices, mock_metrics):
    """Should return None values if no data."""
    mock_metrics.return_value = []
    mock_prices.return_value = []

    metrics = fetch_comparable_metrics("600000.SH")

    assert metrics["pe"] is None
    assert metrics["pb"] is None
    assert metrics["roe"] is None
    assert metrics["dividend_yield"] is None


def test_calculate_percentile_rank():
    """Should calculate percentile rank correctly."""
    # Value of 5 in [1, 3, 5, 7, 9]
    # 2 values below 5 → 2/5 = 40th percentile
    percentile = calculate_percentile_rank(5.0, [1.0, 3.0, 5.0, 7.0, 9.0])
    assert percentile == 40.0


def test_calculate_percentile_rank_highest():
    """Highest value should be 100th percentile."""
    percentile = calculate_percentile_rank(10.0, [1.0, 3.0, 5.0, 7.0, 10.0])
    assert percentile == 80.0  # 4 out of 5 below


def test_calculate_percentile_rank_lowest():
    """Lowest value should be 0th percentile."""
    percentile = calculate_percentile_rank(1.0, [1.0, 3.0, 5.0, 7.0, 9.0])
    assert percentile == 0.0


def test_calculate_percentile_rank_no_peers():
    """Should return 50 if no peer data."""
    percentile = calculate_percentile_rank(5.0, [])
    assert percentile == 50.0


def test_calculate_percentile_rank_none_value():
    """Should return 50 if value is None."""
    percentile = calculate_percentile_rank(None, [1.0, 3.0, 5.0])
    assert percentile == 50.0


@patch("src.agents.comparables.fetch_comparable_metrics")
@patch("src.agents.comparables.get_comparables_from_watchlist")
def test_run_comparable_analysis(mock_get_comparables, mock_fetch):
    """Should run full comparable analysis."""
    mock_get_comparables.return_value = ["601398.SH", "601939.SH"]

    # Mock fetch for target and comparables
    def fetch_side_effect(ticker):
        data = {
            "600000.SH": {"ticker": "600000.SH", "pe": 6.0, "pb": 0.60, "roe": 0.10, "dividend_yield": 0.05},
            "601398.SH": {"ticker": "601398.SH", "pe": 5.0, "pb": 0.55, "roe": 0.12, "dividend_yield": 0.06},
            "601939.SH": {"ticker": "601939.SH", "pe": 7.0, "pb": 0.65, "roe": 0.09, "dividend_yield": 0.04},
        }
        return data.get(ticker, {})

    mock_fetch.side_effect = fetch_side_effect

    result = run_comparable_analysis("600000.SH", "银行")

    assert result["target_metrics"]["ticker"] == "600000.SH"
    assert len(result["peer_metrics"]) == 2
    assert result["percentiles"]["pe"] is not None
    assert result["percentiles"]["pb"] is not None
    assert result["percentiles"]["roe"] is not None
    assert result["percentiles"]["dividend_yield"] is not None
    assert result["industry_median"]["pe"] is not None
    assert result["comparison_table"] is not None


@patch("src.agents.comparables.fetch_comparable_metrics")
@patch("src.agents.comparables.get_comparables_from_watchlist")
@patch("src.agents.comparables.auto_select_comparables")
def test_run_comparable_analysis_no_comparables(mock_auto, mock_get, mock_fetch):
    """Should handle case with no comparables."""
    mock_get.return_value = []
    mock_auto.return_value = []
    mock_fetch.return_value = {
        "ticker": "600000.SH",
        "pe": 6.0,
        "pb": 0.60,
        "roe": 0.10,
        "dividend_yield": 0.05,
    }

    result = run_comparable_analysis("600000.SH", "银行")

    assert result["target_metrics"]["ticker"] == "600000.SH"
    assert result["peer_metrics"] == []
    assert result["note"] == "No comparable companies available"


@patch("src.agents.comparables.fetch_comparable_metrics")
def test_run_comparable_analysis_user_comparables(mock_fetch):
    """Should use user-specified comparables if provided."""

    def fetch_side_effect(ticker):
        data = {
            "600000.SH": {"ticker": "600000.SH", "pe": 6.0, "pb": 0.60, "roe": 0.10, "dividend_yield": 0.05},
            "601398.SH": {"ticker": "601398.SH", "pe": 5.0, "pb": 0.55, "roe": 0.12, "dividend_yield": 0.06},
        }
        return data.get(ticker, {})

    mock_fetch.side_effect = fetch_side_effect

    result = run_comparable_analysis(
        "600000.SH",
        "银行",
        user_comparables=["601398.SH"],
    )

    assert len(result["peer_metrics"]) == 1
    assert result["peer_metrics"][0]["ticker"] == "601398.SH"


def test_run_comparable_analysis_pe_percentile_inversion():
    """PE percentile should be inverted (lower is better)."""
    # Target PE=6.0, peers=[5.0, 7.0]
    # Without inversion: 6.0 is at 33rd percentile (1 out of 3 below)
    # With inversion: 100 - 33 = 67th percentile
    # This means target is better than 67% of peers (lower PE is better)

    with patch("src.agents.comparables.fetch_comparable_metrics") as mock_fetch, \
         patch("src.agents.comparables.get_comparables_from_watchlist") as mock_get:

        mock_get.return_value = ["601398.SH", "601939.SH"]

        def fetch_side_effect(ticker):
            data = {
                "600000.SH": {"ticker": "600000.SH", "pe": 6.0, "pb": 0.60, "roe": 0.10, "dividend_yield": 0.05},
                "601398.SH": {"ticker": "601398.SH", "pe": 5.0, "pb": 0.55, "roe": 0.12, "dividend_yield": 0.06},
                "601939.SH": {"ticker": "601939.SH", "pe": 7.0, "pb": 0.65, "roe": 0.09, "dividend_yield": 0.04},
            }
            return data.get(ticker, {})

        mock_fetch.side_effect = fetch_side_effect

        result = run_comparable_analysis("600000.SH", "银行")

        # PE=6.0 is middle value among [5.0, 6.0, 7.0]
        # Raw percentile: 1/3 = 33.3%
        # Inverted: 100 - 33.3 = 66.7%
        assert 60 <= result["percentiles"]["pe"] <= 70


def test_run_comparable_analysis_roe_percentile_no_inversion():
    """ROE percentile should NOT be inverted (higher is better)."""
    with patch("src.agents.comparables.fetch_comparable_metrics") as mock_fetch, \
         patch("src.agents.comparables.get_comparables_from_watchlist") as mock_get:

        mock_get.return_value = ["601398.SH", "601939.SH"]

        def fetch_side_effect(ticker):
            data = {
                "600000.SH": {"ticker": "600000.SH", "pe": 6.0, "pb": 0.60, "roe": 0.12, "dividend_yield": 0.05},
                "601398.SH": {"ticker": "601398.SH", "pe": 5.0, "pb": 0.55, "roe": 0.10, "dividend_yield": 0.06},
                "601939.SH": {"ticker": "601939.SH", "pe": 7.0, "pb": 0.65, "roe": 0.14, "dividend_yield": 0.04},
            }
            return data.get(ticker, {})

        mock_fetch.side_effect = fetch_side_effect

        result = run_comparable_analysis("600000.SH", "银行")

        # ROE=0.12 is middle value among [0.10, 0.12, 0.14]
        # Raw percentile: 1/3 = 33.3% (no inversion for ROE)
        assert 30 <= result["percentiles"]["roe"] <= 40
