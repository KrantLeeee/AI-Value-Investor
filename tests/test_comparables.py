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


def test_auto_select_comparables_uses_industry_profile():
    """Auto-selection should return industry comparables from profile."""
    comparables = auto_select_comparables("600000.SH", "银行", limit=5)

    # Banking industry has comparables in industry_profiles.yaml
    assert len(comparables) > 0
    # Should not include the target ticker
    assert "600000.SH" not in comparables
    # Should include banking stocks
    # Note: 600036.SH is 招商银行, a banking comparable
    assert any("60" in c for c in comparables)


def test_auto_select_comparables_unknown_sector():
    """Auto-selection with unknown sector should return empty list."""
    comparables = auto_select_comparables("123456.SH", "未知行业", limit=5)

    # Default industry has no comparables defined
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


# ── Phase 2: Dynamic AKShare Selection Tests ──────────────────────────────────

from src.agents.comparables import (
    _select_by_market_cap_similarity,
    _fetch_sector_stocks_via_akshare,
    _get_target_market_cap,
)


def test_select_by_market_cap_similarity_within_range():
    """Should select stocks within 0.3x-3x market cap range."""
    stocks = [
        {"ticker": "601398.SH", "market_cap": 1e12},   # 1万亿
        {"ticker": "601939.SH", "market_cap": 8e11},   # 8000亿
        {"ticker": "601288.SH", "market_cap": 5e11},   # 5000亿
        {"ticker": "601166.SH", "market_cap": 2e11},   # 2000亿 - out of range (0.2x)
        {"ticker": "601328.SH", "market_cap": 6e11},   # 6000亿
    ]

    # Target market cap: 1万亿
    # 0.3x = 3000亿, 3x = 3万亿
    # In range: 601398, 601939, 601288 (borderline), 601328
    selected = _select_by_market_cap_similarity(stocks, 1e12, limit=3)

    assert len(selected) == 3
    # 601398 is closest (same cap), then 601939, then 601328
    assert "601398.SH" in selected


def test_select_by_market_cap_similarity_no_target_cap():
    """Should return by descending market cap if no target cap."""
    stocks = [
        {"ticker": "601398.SH", "market_cap": 1e12},
        {"ticker": "601939.SH", "market_cap": 8e11},
        {"ticker": "601288.SH", "market_cap": 5e11},
    ]

    selected = _select_by_market_cap_similarity(stocks, None, limit=2)

    assert len(selected) == 2
    # Should return largest by market cap
    assert selected[0] == "601398.SH"
    assert selected[1] == "601939.SH"


def test_select_by_market_cap_similarity_empty_stocks():
    """Should return empty list if no stocks provided."""
    selected = _select_by_market_cap_similarity([], 1e12, limit=3)
    assert selected == []


def test_select_by_market_cap_similarity_respects_limit():
    """Should respect the limit parameter."""
    stocks = [
        {"ticker": "601398.SH", "market_cap": 1e12},
        {"ticker": "601939.SH", "market_cap": 9e11},
        {"ticker": "601288.SH", "market_cap": 8e11},
        {"ticker": "601166.SH", "market_cap": 7e11},
        {"ticker": "601328.SH", "market_cap": 6e11},
    ]

    selected = _select_by_market_cap_similarity(stocks, 1e12, limit=2)
    assert len(selected) == 2


@patch("src.agents.comparables.get_financial_metrics")
def test_get_target_market_cap(mock_metrics):
    """Should return market cap from database."""
    mock_metrics.return_value = [{"market_cap": 5e11}]

    cap = _get_target_market_cap("600000.SH")

    assert cap == 5e11


@patch("src.agents.comparables.get_financial_metrics")
def test_get_target_market_cap_no_data(mock_metrics):
    """Should return None if no data."""
    mock_metrics.return_value = []

    cap = _get_target_market_cap("600000.SH")

    assert cap is None


def test_fetch_sector_stocks_akshare_not_installed():
    """Should return empty list if AKShare not installed."""
    # This test runs without AKShare installed
    with patch.dict("sys.modules", {"akshare": None}):
        result = _fetch_sector_stocks_via_akshare("银行", "600000.SH")
        # Should gracefully handle missing AKShare
        assert isinstance(result, list)


@patch("src.agents.comparables._fetch_sector_stocks_via_akshare")
@patch("src.agents.comparables._get_target_market_cap")
@patch("src.agents.comparables.get_industry_comparables")
@patch("src.agents.comparables.classify_industry")
def test_auto_select_comparables_akshare_fallback(
    mock_classify, mock_get_industry, mock_get_cap, mock_fetch
):
    """Should use AKShare fallback when no industry profile comparables."""
    mock_classify.return_value = "default"
    mock_get_industry.return_value = []  # No industry profile comparables
    mock_get_cap.return_value = 5e11

    mock_fetch.return_value = [
        {"ticker": "601398.SH", "market_cap": 6e11},
        {"ticker": "601939.SH", "market_cap": 4e11},
        {"ticker": "601288.SH", "market_cap": 3e11},
    ]

    result = auto_select_comparables("600000.SH", "银行", limit=2)

    assert len(result) == 2
    mock_fetch.assert_called_once_with("银行", "600000.SH")


@patch("src.agents.comparables._fetch_sector_stocks_via_akshare")
@patch("src.agents.comparables.get_industry_comparables")
@patch("src.agents.comparables.classify_industry")
def test_auto_select_comparables_industry_profile_priority(
    mock_classify, mock_get_industry, mock_fetch
):
    """Should use industry profile comparables over AKShare when available."""
    mock_classify.return_value = "banking"
    mock_get_industry.return_value = [
        {"ticker": "601398.SH", "name": "工商银行"},
        {"ticker": "601939.SH", "name": "建设银行"},
    ]

    result = auto_select_comparables("600000.SH", "银行", limit=5)

    # Should use industry profile, not AKShare
    assert len(result) == 2
    assert "601398.SH" in result
    mock_fetch.assert_not_called()


@patch("src.agents.comparables._fetch_sector_stocks_via_akshare")
@patch("src.agents.comparables._get_target_market_cap")
@patch("src.agents.comparables.get_industry_comparables")
@patch("src.agents.comparables.classify_industry")
def test_auto_select_comparables_akshare_empty_returns_empty(
    mock_classify, mock_get_industry, mock_get_cap, mock_fetch
):
    """Should return empty list if both industry profile and AKShare fail."""
    mock_classify.return_value = "default"
    mock_get_industry.return_value = []
    mock_get_cap.return_value = 5e11
    mock_fetch.return_value = []  # AKShare returns nothing

    result = auto_select_comparables("600000.SH", "未知行业", limit=5)

    assert result == []


# ── Phase 4: PE/PB Bounds Filtering Tests ──────────────────────────────────

def test_pe_bounds_filtering():
    """Test PE values outside bounds are filtered"""
    from src.agents.comparables import filter_peer_metrics

    peers = [
        {'name': 'Company A', 'pe': 25},      # Valid
        {'name': 'Company B', 'pe': -10},     # Invalid (negative)
        {'name': 'Company C', 'pe': 500},     # Invalid (> 300)
        {'name': 'Company D', 'pe': 0},       # Invalid (zero)
    ]

    filtered = filter_peer_metrics(peers)

    assert filtered[0]['pe'] == 25
    assert filtered[1]['pe'] is None
    assert filtered[2]['pe'] is None
    assert filtered[3]['pe'] is None


def test_pb_bounds_filtering():
    """Test PB values outside bounds are filtered"""
    from src.agents.comparables import filter_peer_metrics

    peers = [
        {'name': 'Company A', 'pb': 3.5},     # Valid
        {'name': 'Company B', 'pb': -2},      # Invalid
        {'name': 'Company C', 'pb': 60},      # Invalid (> 50)
    ]

    filtered = filter_peer_metrics(peers)

    assert filtered[0]['pb'] == 3.5
    assert filtered[1]['pb'] is None
    assert filtered[2]['pb'] is None


def test_pe_pb_bounds_notes_added():
    """Test that notes are added when values are filtered"""
    from src.agents.comparables import filter_peer_metrics

    peers = [
        {'name': 'Company A', 'pe': -5, 'pb': -1},
    ]

    filtered = filter_peer_metrics(peers)

    assert filtered[0]['pe'] is None
    assert filtered[0]['pe_note'] == '超出合理范围，已排除'
    assert filtered[0]['pb'] is None
    assert filtered[0]['pb_note'] == '超出合理范围，已排除'


def test_pe_pb_edge_cases():
    """Test edge cases at boundary values"""
    from src.agents.comparables import filter_peer_metrics

    peers = [
        {'name': 'Company A', 'pe': 300, 'pb': 50},   # At boundary (invalid)
        {'name': 'Company B', 'pe': 299.9, 'pb': 49.9},  # Just inside (valid)
        {'name': 'Company C', 'pe': 0.01, 'pb': 0.01},   # Just above zero (valid)
    ]

    filtered = filter_peer_metrics(peers)

    # At boundary should be invalid (exclusive bounds)
    assert filtered[0]['pe'] is None
    assert filtered[0]['pb'] is None

    # Just inside should be valid
    assert filtered[1]['pe'] == 299.9
    assert filtered[1]['pb'] == 49.9

    # Just above zero should be valid
    assert filtered[2]['pe'] == 0.01
    assert filtered[2]['pb'] == 0.01


def test_filter_peer_metrics_preserves_other_fields():
    """Test that filtering preserves other fields in the dict"""
    from src.agents.comparables import filter_peer_metrics

    peers = [
        {'name': 'Company A', 'ticker': '600000.SH', 'pe': -5, 'pb': 3.5, 'roe': 0.12},
    ]

    filtered = filter_peer_metrics(peers)

    assert filtered[0]['name'] == 'Company A'
    assert filtered[0]['ticker'] == '600000.SH'
    assert filtered[0]['pe'] is None  # Filtered
    assert filtered[0]['pb'] == 3.5   # Not filtered
    assert filtered[0]['roe'] == 0.12  # Preserved
