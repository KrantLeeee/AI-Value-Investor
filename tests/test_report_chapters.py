"""Tests for individual chapter generation functions."""

from src.agents.report_generator import _build_financial_quality_table
from src.data.models import AgentSignal, QualityReport


def test_build_financial_quality_table():
    """Ch3 should generate financial quality table from fundamentals."""
    fundamentals_signal = AgentSignal(
        ticker="TEST",
        agent_name="fundamentals",
        signal="bearish",
        confidence=0.55,
        reasoning="财务质量一般",
        metrics={
            "total_score": 42,
            "revenue_score": 15,
            "profitability_score": 10,
            "leverage_score": 8,
            "cash_flow_score": 9,
        },
    )

    quality_report = QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.85,
        data_completeness=0.90,
        stale_fields=[],
        records_checked={},
    )

    result = _build_financial_quality_table("TEST", fundamentals_signal, quality_report)

    # Verify structure
    assert "## 3. 财务质量评估" in result
    assert "基本面评分" in result
    assert "42/100" in result
    assert "|" in result  # Has table
    assert "数据质量" in result
    assert "0.85" in result
