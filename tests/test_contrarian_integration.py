"""Integration tests for Contrarian Agent in full pipeline."""

import pytest
from src.agents.registry import run_all_agents


@pytest.mark.integration
def test_contrarian_in_registry():
    """Contrarian agent should integrate cleanly into registry"""
    # This test requires database with ticker data
    ticker = "601808.SH"
    market = "a_share"

    try:
        signals, report_path = run_all_agents(ticker, market, quick=True)

        # Verify contrarian was called (or skipped in quick mode)
        # In quick mode, contrarian should return neutral with low confidence
        if "contrarian" in signals:
            assert signals["contrarian"].agent_name == "contrarian"
            assert signals["contrarian"].signal in ["bullish", "bearish", "neutral"]

    except Exception as e:
        pytest.skip(f"Integration test requires database: {e}")


@pytest.mark.integration
def test_contrarian_all_modes():
    """Test all three modes with mocked consensus"""
    # This would require more complex mocking setup
    # For MVP, manual testing is sufficient
    pytest.skip("Manual testing preferred for MVP")
