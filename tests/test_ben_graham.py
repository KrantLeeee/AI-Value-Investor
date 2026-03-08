"""Tests for Ben Graham Agent signal logic and criteria evaluation."""

import pytest
from unittest.mock import patch, MagicMock
from src.agents.ben_graham import run, SIGNAL_ORDER, _apply_signal_cap


class TestSignalOrder:
    """Test SIGNAL_ORDER constant exists and has correct values."""

    def test_signal_order_constant(self):
        """Verify SIGNAL_ORDER constant is defined correctly."""
        # Access from module
        from src.agents import ben_graham
        assert hasattr(ben_graham, 'SIGNAL_ORDER')
        assert ben_graham.SIGNAL_ORDER == {"bearish": 0, "neutral": 1, "bullish": 2}


class TestApplySignalCap:
    """Test _apply_signal_cap function."""

    def test_apply_signal_cap_no_downgrade_when_within_limit(self):
        """Signal should remain unchanged when within allowed limit."""
        from src.agents import ben_graham
        # 5/7 criteria allows neutral or bullish
        result_signal, result_conf = ben_graham._apply_signal_cap(
            llm_signal="bullish",
            llm_confidence=0.75,
            criteria_passed=5,
            criteria_total=7,
            data_completeness=1.0
        )
        assert result_signal == "bullish"
        assert result_conf == 0.75

    def test_apply_signal_cap_downgrades_bullish_to_neutral(self):
        """Bullish signal should be downgraded to neutral when criteria_passed is 3-4."""
        from src.agents import ben_graham
        # 3/7 criteria caps at neutral
        result_signal, result_conf = ben_graham._apply_signal_cap(
            llm_signal="bullish",
            llm_confidence=0.80,
            criteria_passed=3,
            criteria_total=7,
            data_completeness=1.0
        )
        assert result_signal == "neutral"
        # Confidence should be adjusted down
        assert result_conf < 0.80

    def test_apply_signal_cap_downgrades_neutral_to_bearish(self):
        """Neutral signal should be downgraded to bearish when criteria_passed is 1-2."""
        from src.agents import ben_graham
        # 2/7 criteria forces bearish
        result_signal, result_conf = ben_graham._apply_signal_cap(
            llm_signal="neutral",
            llm_confidence=0.60,
            criteria_passed=2,
            criteria_total=7,
            data_completeness=0.8
        )
        assert result_signal == "bearish"
        # Confidence should be dynamic: 0.40 + 0.15 * 0.8 = 0.52
        assert abs(result_conf - 0.52) < 0.01


class TestHardRules:
    """Test hard rules for signal determination based on criteria count."""

    @patch('src.agents.ben_graham.get_income_statements')
    @patch('src.agents.ben_graham.get_balance_sheets')
    @patch('src.agents.ben_graham.get_financial_metrics')
    @patch('src.agents.ben_graham.insert_agent_signal')
    def test_zero_criteria_returns_bearish(self, mock_insert, mock_metrics, mock_balance, mock_income):
        """0/7 criteria should return hard bearish (0.70) and skip LLM."""
        # Mock data that will fail all criteria
        mock_income.return_value = [
            {"eps": -1.0, "net_income": -1000000, "fiscal_year": 2023}
        ]
        mock_balance.return_value = [
            {
                "current_assets": 1000000,
                "current_liabilities": 2000000,  # CR < 2.0
                "total_debt": 5000000,
                "total_equity": 1000000,  # D/E > 0.5
                "total_liabilities": 6000000
            }
        ]
        mock_metrics.return_value = [
            {
                "current_ratio": 0.5,
                "debt_to_equity": 5.0,
                "pe_ratio": 25.0,  # > 15
                "pb_ratio": 3.0    # P/E × P/B > 22.5
            }
        ]

        # Run with use_llm=True to verify LLM is bypassed
        # Note: call_llm is imported inside the try block, so we patch it where it's used
        with patch('src.llm.router.call_llm') as mock_llm:
            result = run(ticker="TEST", market="SH", use_llm=True)

            # Should not call LLM for 0/7 criteria (hard rule skips it)
            mock_llm.assert_not_called()
            assert result.signal == "bearish"
            assert result.confidence == 0.70
            assert result.metrics["criteria_passed"] == 0

    @patch('src.agents.ben_graham.get_income_statements')
    @patch('src.agents.ben_graham.get_balance_sheets')
    @patch('src.agents.ben_graham.get_financial_metrics')
    @patch('src.agents.ben_graham.insert_agent_signal')
    def test_one_to_two_criteria_returns_bearish_dynamic_confidence(self, mock_insert, mock_metrics, mock_balance, mock_income):
        """1-2/7 criteria should return bearish with dynamic confidence."""
        # Mock data that passes only current ratio
        mock_income.return_value = [
            {"eps": -1.0, "net_income": -1000000, "fiscal_year": 2023}
        ]
        mock_balance.return_value = [
            {
                "current_assets": 4000000,
                "current_liabilities": 1000000,  # CR = 4.0 ✓
                "total_debt": 5000000,
                "total_equity": 1000000,  # D/E = 5.0 ✗
                "total_liabilities": 6000000
            }
        ]
        mock_metrics.return_value = [
            {
                "current_ratio": 4.0,
                "debt_to_equity": 5.0,
                "pe_ratio": 25.0,
                "pb_ratio": 3.0
            }
        ]

        result = run(ticker="TEST", market="SH", use_llm=False)

        assert result.signal == "bearish"
        # With data_completeness = 1.0: 0.40 + 0.15 * 1.0 = 0.55
        # But we need to calculate actual data_completeness
        # For now, just verify it's in the expected range
        assert 0.40 <= result.confidence <= 0.55
        assert 1 <= result.metrics["criteria_passed"] <= 2

    @patch('src.llm.router.call_llm')
    @patch('src.agents.ben_graham.get_income_statements')
    @patch('src.agents.ben_graham.get_balance_sheets')
    @patch('src.agents.ben_graham.get_financial_metrics')
    @patch('src.agents.ben_graham.insert_agent_signal')
    def test_three_to_four_criteria_caps_at_neutral(self, mock_insert, mock_metrics, mock_balance, mock_income, mock_llm):
        """3-4/7 criteria should cap at neutral even if LLM says bullish."""
        # Mock data that passes 3 criteria
        mock_income.return_value = [
            {"eps": 1.5, "net_income": 1000000, "fiscal_year": 2023},
            {"eps": 1.4, "net_income": 950000, "fiscal_year": 2022},
            {"eps": 1.3, "net_income": 900000, "fiscal_year": 2021},
            {"eps": 1.2, "net_income": 850000, "fiscal_year": 2020},
            {"eps": 1.1, "net_income": 800000, "fiscal_year": 2019},
            {"eps": 1.0, "net_income": 750000, "fiscal_year": 2018},
        ]
        mock_balance.return_value = [
            {
                "current_assets": 4000000,
                "current_liabilities": 1000000,  # CR = 4.0 ✓
                "total_debt": 300000,
                "total_equity": 1000000,  # D/E = 0.3 ✓
                "total_liabilities": 1300000
            }
        ]
        mock_metrics.return_value = [
            {
                "current_ratio": 4.0,
                "debt_to_equity": 0.3,
                "pe_ratio": 25.0,  # > 15 ✗
                "pb_ratio": 3.0    # P/E × P/B = 75 > 22.5 ✗
            }
        ]

        # LLM tries to return bullish
        mock_llm.return_value = '{"signal": "bullish", "confidence": 0.80, "reasoning": "Great company!"}'

        result = run(ticker="TEST", market="SH", use_llm=True)

        # Should be capped at neutral
        assert result.signal == "neutral"
        # Confidence should be adjusted down from 0.80
        assert result.confidence < 0.80

    @patch('src.agents.ben_graham.get_income_statements')
    @patch('src.agents.ben_graham.get_balance_sheets')
    @patch('src.agents.ben_graham.get_financial_metrics')
    @patch('src.agents.ben_graham.insert_agent_signal')
    def test_missing_criteria_passed_returns_neutral(self, mock_insert, mock_metrics, mock_balance, mock_income):
        """Missing criteria_passed should return neutral (0.30)."""
        # Mock empty data
        mock_income.return_value = []
        mock_balance.return_value = []
        mock_metrics.return_value = []

        result = run(ticker="TEST", market="SH", use_llm=False)

        # With empty data, insufficient_data flag is set → neutral (0.30)
        assert result.signal == "neutral"
        assert result.confidence == 0.30
        assert result.metrics["criteria_passed"] == 0
        # Note: criteria_total may be 1 (profitable_years is always evaluated)
        # but the insufficient_data logic overrides it
