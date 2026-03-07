"""Tests for individual chapter generation functions."""

from src.agents.report_generator import (
    _build_financial_quality_table,
    _build_valuation_analysis,
    _render_contrarian_chapter,
)
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


def test_build_valuation_analysis():
    """Ch4 should generate valuation tables from valuation agent."""
    valuation_signal = AgentSignal(
        ticker="TEST",
        agent_name="valuation",
        signal="neutral",
        confidence=0.60,
        reasoning="估值适中",
        metrics={
            "dcf_per_share": 25.50,
            "graham_number": 23.00,
            "current_price": 24.00,
            "margin_of_safety": 0.06,
            "ev_ebitda": 8.5,
        },
    )

    result = _build_valuation_analysis(valuation_signal)

    # Verify structure
    assert "## 4. 估值分析" in result
    assert "25.50" in result  # DCF value
    assert "23.00" in result  # Graham number
    assert "24.00" in result  # Current price
    assert "|" in result  # Has tables
    assert "敏感性" in result or "情景" in result


def test_render_contrarian_bear_case():
    """Ch5 should render bear_case Contrarian template."""
    contrarian_signal = AgentSignal(
        ticker="TEST",
        agent_name="contrarian",
        signal="bearish",
        confidence=0.60,
        reasoning="风险场景分析完成",
        metrics={
            "mode": "bear_case",
            "consensus": {"direction": "bullish", "strength": 0.75},
            "assumption_challenges": [
                {
                    "original_claim": "增长率20%",
                    "assumption": "需求持续",
                    "challenge": "需求可能饱和",
                    "impact_if_wrong": "增长停滞",
                    "severity": "high",
                }
            ],
            "risk_scenarios": [
                {
                    "scenario": "原材料价格上涨",
                    "probability": "30%",
                    "impact": "利润率下降5%",
                    "precedent": "2020年Q2",
                }
            ],
            "bear_case_target_price": 18.50,
        },
    )

    result = _render_contrarian_chapter(contrarian_signal)

    # Verify structure
    assert "## 5. 风险因素" in result
    assert "bullish" in result
    assert "75%" in result
    assert "Bear Case" in result
    assert "增长率20%" in result
    assert "18.50" in result
