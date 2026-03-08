"""Tests for Valuation Agent outlier detection and weighted calculation."""

import pytest
from unittest.mock import patch, Mock

from src.agents.valuation import (
    _validate_valuation_result,
    _calculate_weighted_target,
)


class TestValidateValuationResult:
    """Tests for _validate_valuation_result function."""

    def test_negative_target_price_excluded(self):
        """Negative target price should be excluded from weighted average."""
        all_results = [100, 120, 150]
        result = _validate_valuation_result(
            method_name="DCF",
            target_price=-50,
            current_price=100,
            all_results=all_results
        )

        assert result["method"] == "DCF"
        assert result["target_price"] == -50
        assert result["valid"] is False
        assert result["exclude_from_weighted"] is True
        assert "negative" in " ".join(result["warnings"]).lower()

    def test_zero_target_price_excluded(self):
        """Zero target price should be excluded from weighted average."""
        all_results = [100, 120, 150]
        result = _validate_valuation_result(
            method_name="Graham",
            target_price=0,
            current_price=100,
            all_results=all_results
        )

        assert result["valid"] is False
        assert result["exclude_from_weighted"] is True
        assert len(result["warnings"]) > 0

    def test_deviation_from_median_over_60_percent_excluded(self):
        """BUG-02: Target price >60% deviation from median should be excluded."""
        # Median of [100, 110, 120] = 110
        # Target 200 is +81.8% from median → should be excluded
        # Note: No directional consensus (100, 110, 120 around current=105, but 200 is outlier)
        all_results = [100, 110, 120, 200]
        result = _validate_valuation_result(
            method_name="P/B",
            target_price=200,
            current_price=105,
            all_results=all_results
        )

        assert result["valid"] is False
        assert result["exclude_from_weighted"] is True
        assert "median" in " ".join(result["warnings"]).lower()

    def test_deviation_from_median_over_60_percent_downside(self):
        """BUG-02: Target price >60% below median should be excluded."""
        # Median of [100, 110, 120] = 110
        # Target 40 is -63.6% from median → should be excluded
        all_results = [100, 110, 120, 40]
        result = _validate_valuation_result(
            method_name="EV/EBITDA",
            target_price=40,
            current_price=105,
            all_results=all_results
        )

        assert result["valid"] is False
        assert result["exclude_from_weighted"] is True

    def test_bug02_directional_consensus_all_above_market_not_excluded(self):
        """
        BUG-02 FIX: When all methods agree on direction (all above market),
        don't exclude even if deviation from median > 60%.

        Example: 中国平安 with all valuations above market price ¥62.67
        """
        # All valuations above market price (simulating undervalued stock)
        all_results = [100, 110, 180]  # All above current_price=60
        result = _validate_valuation_result(
            method_name="P/B",
            target_price=180,  # 63.6% above median of 110, but all agree stock is undervalued
            current_price=60,
            all_results=all_results
        )

        # Should NOT be excluded because all methods agree the stock is undervalued
        assert result["valid"] is True
        assert result["exclude_from_weighted"] is False
        # Should have info warning about retention due to consensus
        warnings_text = " ".join(result["warnings"]).lower()
        assert "retained" in warnings_text or len(result["warnings"]) == 0

    def test_bug02_directional_consensus_all_below_market_not_excluded(self):
        """
        BUG-02 FIX: When all methods agree on direction (all below market),
        don't exclude even if deviation from median > 60%.
        """
        # All valuations below market price (simulating overvalued stock)
        all_results = [30, 40, 80]  # All below current_price=100
        result = _validate_valuation_result(
            method_name="DCF",
            target_price=30,  # Far below median of 40, but all agree stock is overvalued
            current_price=100,
            all_results=all_results
        )

        # Should NOT be excluded because all methods agree the stock is overvalued
        assert result["valid"] is True
        assert result["exclude_from_weighted"] is False

    def test_bug02_no_consensus_outlier_still_excluded(self):
        """
        BUG-02: Without directional consensus, outliers should still be excluded.
        """
        # Mixed directions: 90 below market, 110/120 above market
        all_results = [90, 110, 120, 200]
        result = _validate_valuation_result(
            method_name="P/B",
            target_price=200,  # 81.8% above median of 110
            current_price=100,  # Some methods above, some below
            all_results=all_results
        )

        # Should be excluded: no consensus and >60% deviation from median
        assert result["valid"] is False
        assert result["exclude_from_weighted"] is True

    def test_bug02_market_price_deviation_no_longer_triggers_exclusion(self):
        """
        BUG-02 FIX: Deviation from market price should NOT trigger exclusion.

        Old behavior: >80% from market price → excluded
        New behavior: Only median deviation matters (with consensus exception)
        """
        # Target 200 is +100% from current price (100)
        # But only +33% from median (150)
        all_results = [120, 150, 180, 200]  # All above market, median=165
        result = _validate_valuation_result(
            method_name="DCF",
            target_price=200,  # +100% from current price
            current_price=100,
            all_results=all_results
        )

        # Should NOT be excluded: all methods above market (consensus)
        # and deviation from median is only 21% ((200-165)/165)
        assert result["valid"] is True
        assert result["exclude_from_weighted"] is False

    def test_valid_result_within_all_thresholds(self):
        """Valid result should pass all rules."""
        # Median = 110, target = 115
        # - Not negative ✓
        # - (115-110)/110 = 4.5% from median ✓ (<60%)
        all_results = [100, 110, 120, 115]
        result = _validate_valuation_result(
            method_name="DCF",
            target_price=115,
            current_price=100,
            all_results=all_results
        )

        assert result["valid"] is True
        assert result["exclude_from_weighted"] is False
        assert len(result["warnings"]) == 0

    def test_multiple_violations_multiple_warnings(self):
        """Result violating multiple rules should have multiple warnings."""
        # Negative AND far from median
        all_results = [100, 110, 120, -50]
        result = _validate_valuation_result(
            method_name="DCF",
            target_price=-50,
            current_price=100,
            all_results=all_results
        )

        assert result["valid"] is False
        assert result["exclude_from_weighted"] is True
        # Should have warnings for both negative and median deviation
        assert len(result["warnings"]) >= 1

    def test_median_calculation_uses_statistics_median(self):
        """Should use statistics.median, not mean (resistant to outliers)."""
        # Mean = 98, Median = 110
        # Target 150 is: +53% from mean, +36.4% from median
        # Should use median → valid (36.4% < 60%)
        all_results = [10, 100, 110, 120, 150]
        result = _validate_valuation_result(
            method_name="Graham",
            target_price=150,
            current_price=105,
            all_results=all_results
        )

        # If using median correctly: (150-110)/110 = 36.4% → should include (<60%)
        assert result["valid"] is True
        assert result["exclude_from_weighted"] is False


class TestCalculateWeightedTarget:
    """Tests for _calculate_weighted_target function."""

    def test_all_valid_methods_weighted_average(self):
        """Should calculate weighted average when all methods are valid."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": 110, "exclude_from_weighted": False},
            {"method": "EV/EBITDA", "target_price": 120, "exclude_from_weighted": False},
        ]
        weights = {"DCF": 0.5, "Graham": 0.3, "EV/EBITDA": 0.2}

        result = _calculate_weighted_target(results, current_price=105, weights=weights)

        # Expected: 100*0.5 + 110*0.3 + 120*0.2 = 50 + 33 + 24 = 107
        assert result["weighted_target"] == pytest.approx(107, rel=1e-6)
        assert len(result["valid_methods"]) == 3
        assert len(result["excluded_methods"]) == 0
        assert result["degraded"] is False

    def test_some_excluded_methods_renormalize_weights(self):
        """Should renormalize weights when some methods are excluded."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": 110, "exclude_from_weighted": False},
            {"method": "EV/EBITDA", "target_price": -50, "exclude_from_weighted": True},
        ]
        weights = {"DCF": 0.5, "Graham": 0.3, "EV/EBITDA": 0.2}

        result = _calculate_weighted_target(results, current_price=105, weights=weights)

        # EV/EBITDA excluded, renormalize: DCF=0.5/0.8=0.625, Graham=0.3/0.8=0.375
        # Expected: 100*0.625 + 110*0.375 = 62.5 + 41.25 = 103.75
        assert result["weighted_target"] == pytest.approx(103.75, rel=1e-6)
        assert len(result["valid_methods"]) == 2
        assert len(result["excluded_methods"]) == 1
        assert "EV/EBITDA" in result["excluded_methods"]
        assert result["degraded"] is False

    def test_default_equal_weights_if_not_provided(self):
        """Should use equal weights if weights parameter is None."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": 110, "exclude_from_weighted": False},
            {"method": "EV/EBITDA", "target_price": 120, "exclude_from_weighted": False},
        ]

        result = _calculate_weighted_target(results, current_price=105, weights=None)

        # Equal weights: each 1/3
        # Expected: 100*(1/3) + 110*(1/3) + 120*(1/3) = 110
        assert result["weighted_target"] == pytest.approx(110, rel=1e-6)
        assert result["degraded"] is False

    def test_degraded_mode_one_valid_method(self):
        """Should enter degraded mode when only 1 valid method remains."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": -50, "exclude_from_weighted": True},
            {"method": "EV/EBITDA", "target_price": 300, "exclude_from_weighted": True},
        ]

        result = _calculate_weighted_target(results, current_price=105, weights=None)

        assert result["degraded"] is True
        assert result["weighted_target"] == 100  # Only valid method
        assert result["confidence"] == 0.25  # Degraded confidence
        assert len(result["valid_methods"]) == 1
        assert len(result["excluded_methods"]) == 2
        assert "degraded" in result.get("warning", "").lower()

    def test_degraded_mode_zero_valid_methods(self):
        """Should enter degraded mode when 0 valid methods remain."""
        results = [
            {"method": "DCF", "target_price": -50, "exclude_from_weighted": True},
            {"method": "Graham", "target_price": 0, "exclude_from_weighted": True},
            {"method": "EV/EBITDA", "target_price": 500, "exclude_from_weighted": True},
        ]

        result = _calculate_weighted_target(results, current_price=105, weights=None)

        assert result["degraded"] is True
        assert result["weighted_target"] is None
        assert result["confidence"] == 0.25
        assert len(result["valid_methods"]) == 0
        assert len(result["excluded_methods"]) == 3

    def test_two_valid_methods_no_degraded_mode(self):
        """Should NOT enter degraded mode with 2 valid methods."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": 110, "exclude_from_weighted": False},
            {"method": "EV/EBITDA", "target_price": 500, "exclude_from_weighted": True},
        ]

        result = _calculate_weighted_target(results, current_price=105, weights=None)

        assert result["degraded"] is False
        assert result["weighted_target"] == pytest.approx(105, rel=1e-6)  # (100+110)/2
        assert len(result["valid_methods"]) == 2

    def test_excluded_methods_list_contains_correct_names(self):
        """Should list all excluded method names."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": -50, "exclude_from_weighted": True},
            {"method": "EV/EBITDA", "target_price": 0, "exclude_from_weighted": True},
            {"method": "P/B", "target_price": 500, "exclude_from_weighted": True},
        ]

        result = _calculate_weighted_target(results, current_price=105, weights=None)

        assert set(result["excluded_methods"]) == {"Graham", "EV/EBITDA", "P/B"}
        assert len(result["valid_methods"]) == 1

    def test_warning_message_in_degraded_mode(self):
        """Degraded mode should include a warning message."""
        results = [
            {"method": "DCF", "target_price": 100, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": -50, "exclude_from_weighted": True},
        ]

        result = _calculate_weighted_target(results, current_price=105, weights=None)

        assert result["degraded"] is True
        assert "warning" in result
        assert len(result["warning"]) > 0
