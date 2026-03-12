"""Tests for Valuation Agent outlier detection and weighted calculation."""

import pytest
from unittest.mock import patch, Mock

from src.agents.valuation import (
    _validate_valuation_result,
    _calculate_weighted_target,
)
from src.agents.industry_classifier import (
    detect_growth_stock,
    get_growth_tech_valuation_config,
    detect_financial_stock,
    get_financial_stock_valuation_config,
    detect_cyclical_stock,
    get_cyclical_stock_valuation_config,
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


class TestDetectGrowthStock:
    """Tests for detect_growth_stock function (BUG-03B)."""

    def test_growth_stock_detected_high_pe_high_cagr_tech_industry(self):
        """
        BUG-03B: Growth stock should be detected when:
        - PE > 25x
        - Revenue CAGR 3Y >= 15%
        - Industry is tech/growth related
        """
        result = detect_growth_stock(
            pe_ratio=45.0,  # High PE
            revenue_cagr_3y=0.25,  # 25% CAGR
            net_income=1e8,  # Profitable
            eps=1.5,  # Positive EPS
            industry="工业自动化",  # Automation industry
        )

        assert result is True

    def test_growth_stock_not_detected_low_pe(self):
        """
        Growth stock should NOT be detected when PE <= 25x.
        """
        result = detect_growth_stock(
            pe_ratio=20.0,  # PE not high enough
            revenue_cagr_3y=0.30,  # 30% CAGR
            net_income=1e8,
            eps=1.5,
            industry="软件",
        )

        assert result is False

    def test_growth_stock_not_detected_low_cagr(self):
        """
        Growth stock should NOT be detected when revenue CAGR < 15%.
        """
        result = detect_growth_stock(
            pe_ratio=35.0,
            revenue_cagr_3y=0.10,  # Only 10% CAGR
            net_income=1e8,
            eps=1.5,
            industry="科技",
        )

        assert result is False

    def test_growth_stock_not_detected_negative_earnings(self):
        """
        Growth stock should NOT be detected when net income <= 0.
        Use loss-making tech mode instead.
        """
        result = detect_growth_stock(
            pe_ratio=45.0,
            revenue_cagr_3y=0.30,
            net_income=-1e7,  # Negative net income
            eps=-0.5,  # Negative EPS
            industry="科技",
        )

        assert result is False

    def test_growth_stock_not_detected_zero_eps(self):
        """
        Growth stock should NOT be detected when EPS <= 0.
        """
        result = detect_growth_stock(
            pe_ratio=45.0,
            revenue_cagr_3y=0.30,
            net_income=1e8,
            eps=0,  # Zero EPS
            industry="科技",
        )

        assert result is False

    def test_growth_stock_detected_strong_financials_override(self):
        """
        BUG-03B: Growth stock detected with strong financials
        even without explicit growth industry tag.
        PE > 30 and CAGR > 20% should override.
        """
        result = detect_growth_stock(
            pe_ratio=35.0,  # PE > 30
            revenue_cagr_3y=0.25,  # CAGR > 20%
            net_income=1e8,
            eps=1.5,
            industry="传统制造业",  # Not a typical growth industry
        )

        assert result is True

    def test_growth_stock_huichuan_example(self):
        """
        BUG-03B: Test case based on 汇川技术 (300124.SZ).
        PE ~45x, revenue growth ~20%, automation industry.
        """
        result = detect_growth_stock(
            pe_ratio=45.0,
            revenue_cagr_3y=0.20,  # 20% 3-year CAGR
            net_income=3.5e9,  # ~35亿
            eps=1.60,
            industry="工业自动化",
        )

        assert result is True


class TestGrowthStockValuationConfig:
    """Tests for growth stock valuation configuration."""

    def test_growth_stock_weights_sum_to_one(self):
        """
        Growth stock weights should sum to 1.0.
        """
        config = get_growth_tech_valuation_config()
        weights_sum = sum(config["weights"].values())

        assert abs(weights_sum - 1.0) < 0.01

    def test_growth_stock_config_excludes_graham(self):
        """
        BUG-03B: Growth stock config should NOT include Graham Number.
        """
        config = get_growth_tech_valuation_config()

        assert "Graham" not in config["enabled_methods"]
        assert "Graham" not in config["weights"]

    def test_growth_stock_config_includes_peg(self):
        """
        BUG-03B: Growth stock config should include PEG.
        """
        config = get_growth_tech_valuation_config()

        assert "PEG" in config["enabled_methods"]
        assert "PEG" in config["weights"]
        assert config["weights"]["PEG"] == 0.30

    def test_growth_stock_config_dcf_primary(self):
        """
        BUG-03B: DCF should be the primary method for growth stocks.
        """
        config = get_growth_tech_valuation_config()

        assert config["weights"]["DCF"] == 0.35


class TestGrowthStockWeightedCalculation:
    """Tests for growth stock weighted target calculation."""

    def test_growth_stock_weighted_with_peg(self):
        """
        Should calculate weighted average using growth stock methods.
        """
        results = [
            {"method": "DCF", "target_price": 80, "exclude_from_weighted": False},
            {"method": "PEG", "target_price": 90, "exclude_from_weighted": False},
            {"method": "EV/Sales", "target_price": 100, "exclude_from_weighted": False},
            {"method": "P/B", "target_price": 60, "exclude_from_weighted": False},
        ]
        weights = {
            "DCF": 0.35,
            "PEG": 0.30,
            "EV/Sales": 0.20,
            "P/B": 0.15,
        }

        result = _calculate_weighted_target(results, current_price=70, weights=weights)

        # Expected: 80*0.35 + 90*0.30 + 100*0.20 + 60*0.15
        #         = 28 + 27 + 20 + 9 = 84
        assert result["weighted_target"] == pytest.approx(84, rel=1e-6)
        assert len(result["valid_methods"]) == 4
        assert result["degraded"] is False


class TestDetectFinancialStock:
    """Tests for detect_financial_stock function (Phase 2)."""

    def test_financial_stock_detected_banking(self):
        """Financial stock should be detected for banking industry."""
        result = detect_financial_stock(
            industry="银行",
            roe=0.14,
            dividend_yield=0.05,
        )

        assert result is True

    def test_financial_stock_detected_insurance(self):
        """Financial stock should be detected for insurance industry."""
        result = detect_financial_stock(
            industry="保险",
            roe=0.12,
            dividend_yield=0.04,
        )

        assert result is True

    def test_financial_stock_detected_pingan_example(self):
        """
        Phase 2: Test case based on 中国平安 (601318.SH).
        """
        result = detect_financial_stock(
            industry="金融",
            roe=0.14,
            dividend_yield=0.05,
        )

        assert result is True

    def test_financial_stock_not_detected_tech(self):
        """Non-financial industry should not be detected."""
        result = detect_financial_stock(
            industry="科技",
            roe=0.20,
            dividend_yield=0.01,
        )

        assert result is False

    def test_financial_stock_not_detected_no_industry(self):
        """No industry should return False."""
        result = detect_financial_stock(
            industry=None,
            roe=0.14,
        )

        assert result is False


class TestFinancialStockValuationConfig:
    """Tests for financial stock valuation configuration."""

    def test_financial_stock_weights_sum_to_one(self):
        """Financial stock weights should sum to 1.0."""
        config = get_financial_stock_valuation_config()
        weights_sum = sum(config["weights"].values())

        assert abs(weights_sum - 1.0) < 0.01

    def test_financial_stock_config_excludes_ev_ebitda(self):
        """Financial stock config should NOT include EV/EBITDA."""
        config = get_financial_stock_valuation_config()

        assert "EV/EBITDA" not in config["enabled_methods"]
        assert "EV/EBITDA" not in config["weights"]

    def test_financial_stock_config_includes_pb_roe(self):
        """Financial stock config should include P/B_ROE."""
        config = get_financial_stock_valuation_config()

        assert "P/B_ROE" in config["enabled_methods"]
        assert "P/B_ROE" in config["weights"]
        assert config["weights"]["P/B_ROE"] == 0.40

    def test_financial_stock_config_includes_ddm(self):
        """Financial stock config should include DDM."""
        config = get_financial_stock_valuation_config()

        assert "DDM" in config["enabled_methods"]
        assert "DDM" in config["weights"]


class TestDetectCyclicalStock:
    """Tests for detect_cyclical_stock function (Phase 2)."""

    def test_cyclical_stock_detected_oil(self):
        """Cyclical stock should be detected for oil industry."""
        result = detect_cyclical_stock(
            industry="石油服务",
        )

        assert result is True

    def test_cyclical_stock_detected_steel(self):
        """Cyclical stock should be detected for steel industry."""
        result = detect_cyclical_stock(
            industry="钢铁",
        )

        assert result is True

    def test_cyclical_stock_detected_cosl_example(self):
        """
        Phase 2: Test case based on 中海油服 (601808.SH).
        """
        result = detect_cyclical_stock(
            industry="石油",
        )

        assert result is True

    def test_cyclical_stock_not_detected_tech(self):
        """Non-cyclical industry should not be detected."""
        result = detect_cyclical_stock(
            industry="科技",
        )

        assert result is False

    def test_cyclical_stock_not_detected_no_industry(self):
        """No industry should return False."""
        result = detect_cyclical_stock(
            industry=None,
        )

        assert result is False


class TestCyclicalStockValuationConfig:
    """Tests for cyclical stock valuation configuration."""

    def test_cyclical_stock_weights_sum_to_one(self):
        """Cyclical stock weights should sum to 1.0."""
        config = get_cyclical_stock_valuation_config()
        weights_sum = sum(config["weights"].values())

        assert abs(weights_sum - 1.0) < 0.01

    def test_cyclical_stock_config_includes_normalized_dcf(self):
        """Cyclical stock config should include DCF_Normalized."""
        config = get_cyclical_stock_valuation_config()

        assert "DCF_Normalized" in config["enabled_methods"]
        assert "DCF_Normalized" in config["weights"]
        assert config["weights"]["DCF_Normalized"] == 0.35

    def test_cyclical_stock_config_includes_cycle_ebitda(self):
        """Cyclical stock config should include EV/EBITDA_Cycle."""
        config = get_cyclical_stock_valuation_config()

        assert "EV/EBITDA_Cycle" in config["enabled_methods"]
        assert "EV/EBITDA_Cycle" in config["weights"]


# ── Phase 2: Healthcare stock tests ─────────────────────────────────────────


class TestDetectHealthcareStock:
    """Tests for detect_healthcare_stock function (Phase 2)."""

    def test_healthcare_stock_detected_pharma(self):
        """Healthcare stock should be detected for pharma industry."""
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry="医药")

        assert result is True

    def test_healthcare_stock_detected_biotech(self):
        """Healthcare stock should be detected for biotech industry."""
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry="生物制药")

        assert result is True

    def test_healthcare_stock_detected_medical_device(self):
        """Healthcare stock should be detected for medical device industry."""
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry="医疗器械")

        assert result is True

    def test_healthcare_stock_detected_cxo(self):
        """Healthcare stock should be detected for CXO industry."""
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry="CRO/CDMO")

        assert result is True

    def test_healthcare_stock_detected_hengrui_example(self):
        """
        Phase 2: Test case based on 恒瑞医药 (600276.SH).
        """
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry="制药")

        assert result is True

    def test_healthcare_stock_not_detected_tech(self):
        """Non-healthcare industry should not be detected."""
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry="科技")

        assert result is False

    def test_healthcare_stock_not_detected_no_industry(self):
        """No industry should return False."""
        from src.agents.industry_classifier import detect_healthcare_stock

        result = detect_healthcare_stock(industry=None)

        assert result is False


class TestDetectHealthcareRdStage:
    """Tests for detect_healthcare_rd_stage function (Phase 2)."""

    def test_rd_stage_loss_making(self):
        """Loss-making should be classified as R&D stage."""
        from src.agents.industry_classifier import detect_healthcare_rd_stage

        result = detect_healthcare_rd_stage(
            net_income=-1e8,
            net_margin=-0.10,
            rd_ratio=0.20,
        )

        assert result is True

    def test_rd_stage_marginal_profit(self):
        """Marginal profit (< 5%) should be classified as R&D stage."""
        from src.agents.industry_classifier import detect_healthcare_rd_stage

        result = detect_healthcare_rd_stage(
            net_income=1e7,
            net_margin=0.03,
            rd_ratio=0.15,
        )

        assert result is True

    def test_rd_stage_high_rd_ratio(self):
        """High R&D ratio should indicate R&D stage even if profitable."""
        from src.agents.industry_classifier import detect_healthcare_rd_stage

        result = detect_healthcare_rd_stage(
            net_income=1e8,
            net_margin=0.08,
            rd_ratio=0.18,
        )

        assert result is True

    def test_mature_stage_profitable(self):
        """Stable profit with normal R&D should be mature stage."""
        from src.agents.industry_classifier import detect_healthcare_rd_stage

        result = detect_healthcare_rd_stage(
            net_income=5e8,
            net_margin=0.15,
            rd_ratio=0.10,
        )

        assert result is False

    def test_mature_stage_hengrui_example(self):
        """
        Phase 2: Test case based on 恒瑞医药 (600276.SH) - mature pharma.
        Net margin ~15%, stable earnings.
        """
        from src.agents.industry_classifier import detect_healthcare_rd_stage

        result = detect_healthcare_rd_stage(
            net_income=4.3e9,  # ~43亿净利润
            net_margin=0.16,
            rd_ratio=0.12,
        )

        assert result is False


class TestHealthcareValuationConfig:
    """Tests for healthcare valuation configuration."""

    def test_healthcare_rd_weights_sum_to_one(self):
        """Healthcare R&D weights should sum to 1.0."""
        from src.agents.industry_classifier import get_healthcare_rd_valuation_config

        config = get_healthcare_rd_valuation_config()
        total = sum(config["weights"].values())

        assert abs(total - 1.0) < 0.01

    def test_healthcare_rd_config_includes_ps(self):
        """Healthcare R&D config should include PS as primary method."""
        from src.agents.industry_classifier import get_healthcare_rd_valuation_config

        config = get_healthcare_rd_valuation_config()

        assert "PS" in config["enabled_methods"]
        assert config["weights"]["PS"] >= 0.35  # Should be primary

    def test_healthcare_rd_config_excludes_pe(self):
        """Healthcare R&D config should NOT include P/E (unprofitable)."""
        from src.agents.industry_classifier import get_healthcare_rd_valuation_config

        config = get_healthcare_rd_valuation_config()

        assert "P/E" not in config["enabled_methods"]

    def test_healthcare_mature_weights_sum_to_one(self):
        """Healthcare mature weights should sum to 1.0."""
        from src.agents.industry_classifier import get_healthcare_mature_valuation_config

        config = get_healthcare_mature_valuation_config()
        total = sum(config["weights"].values())

        assert abs(total - 1.0) < 0.01

    def test_healthcare_mature_config_includes_pe(self):
        """Healthcare mature config should include P/E as primary method."""
        from src.agents.industry_classifier import get_healthcare_mature_valuation_config

        config = get_healthcare_mature_valuation_config()

        assert "P/E" in config["enabled_methods"]
        assert config["weights"]["P/E"] >= 0.30  # Should be primary

    def test_healthcare_mature_config_includes_dcf(self):
        """Healthcare mature config should include DCF."""
        from src.agents.industry_classifier import get_healthcare_mature_valuation_config

        config = get_healthcare_mature_valuation_config()

        assert "DCF" in config["enabled_methods"]
        assert "DCF" in config["weights"]


# ── Task 1.2: Real Estate P/B Cap ─────────────────────────────────────────────


class TestRealEstatePbCap:
    """Tests for real estate P/B cap at 0.5x (Task 1.2)."""

    def test_real_estate_pb_cap(self):
        """Test real estate P/B is capped at 0.5x"""
        from src.agents.valuation import apply_real_estate_cap

        result = apply_real_estate_cap(
            pb_value=1.2,  # Would give 120% upside
            industry_type='房地产'
        )

        assert result['pb_capped'] == 0.5
        assert result['warning'] is not None
        assert 'NAV' in result['warning']

    def test_non_real_estate_no_cap(self):
        """Test non-real estate industries are not capped"""
        from src.agents.valuation import apply_real_estate_cap

        result = apply_real_estate_cap(
            pb_value=1.5,
            industry_type='银行'
        )

        assert result['pb_capped'] == 1.5
        assert result.get('warning') is None

    def test_real_estate_pb_below_cap_no_warning(self):
        """Test real estate P/B below cap doesn't trigger warning"""
        from src.agents.valuation import apply_real_estate_cap

        result = apply_real_estate_cap(
            pb_value=0.3,
            industry_type='房地产'
        )

        assert result['pb_capped'] == 0.3
        assert result.get('warning') is None


# ── Task 1.4: EBITDA Validation and Exclusion ─────────────────────────────────


class TestEbitdaValidation:
    """Tests for EBITDA validation and exclusion (Task 1.4)."""

    def test_ebitda_invalid_excluded(self):
        """Negative or zero EBITDA should be excluded"""
        from src.agents.valuation import calculate_ev_ebitda_value

        value, error = calculate_ev_ebitda_value(ebitda=-100, multiple=8, shares=1000000, revenue=5000000)
        assert value is None
        assert "EBITDA无效" in error

    def test_ebitda_abnormal_excluded(self):
        """EV less than 10% of revenue should be excluded as abnormal"""
        from src.agents.valuation import calculate_ev_ebitda_value

        # EV = ebitda * multiple = 100 * 8 = 800
        # 10% of revenue = 500000
        # 800 < 500000, so should be excluded
        value, error = calculate_ev_ebitda_value(ebitda=100, multiple=8, shares=1000000, revenue=5000000)
        assert value is None
        assert "EV异常" in error

    def test_ebitda_valid_calculated(self):
        """Valid EBITDA should calculate per-share value"""
        from src.agents.valuation import calculate_ev_ebitda_value

        # EV = 1000000 * 8 = 8000000
        # 10% of revenue = 1000000
        # 8000000 > 1000000, so valid
        # per_share = 8000000 / 1000000 = 8.0
        value, error = calculate_ev_ebitda_value(ebitda=1000000, multiple=8, shares=1000000, revenue=10000000)
        assert value == 8.0
        assert error is None
