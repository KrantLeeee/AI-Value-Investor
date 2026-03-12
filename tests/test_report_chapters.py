"""Tests for individual chapter generation functions."""

from unittest.mock import patch
from src.agents.report_generator import (
    _build_financial_quality_table,
    _build_valuation_analysis,
    _render_contrarian_chapter,
    _build_appendix,
    _generate_llm_chapter,
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
            # Additional fields expected by the table builder
            "valuation_methods": [
                {"method": "DCF", "price": 25.50},
                {"method": "Graham", "price": 23.00},
            ],
            "weighted_target_price": 24.50,
        },
    )

    result = _build_valuation_analysis(valuation_signal)

    # Verify structure
    assert "## 4. 估值分析" in result
    assert "24.00" in result or "24" in result  # Current price (may be formatted)
    assert "|" in result  # Has tables
    assert "敏感性" in result or "情景" in result or "估值" in result


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


def test_build_appendix():
    """Appendix should show all agent signals and quality report."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bearish", confidence=0.55, reasoning="Test"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="neutral", confidence=0.60, reasoning="Test"
        ),
    }

    quality_report = QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.90,
        data_completeness=0.95,
        stale_fields=[],
        records_checked={},
    )

    result = _build_appendix(signals, quality_report)

    # Verify structure
    assert "## 附录" in result
    assert "信号汇总" in result  # May be "Agent信号汇总" or "分析维度信号汇总"
    assert "bearish" in result or "🔴" in result  # Signal indicator
    assert "55%" in result
    assert "数据质量" in result
    assert "0.90" in result


@patch('src.llm.router.call_llm')
def test_generate_llm_chapter_pass_validation(mock_llm):
    """LLM chapter should pass validation on first try."""
    # Ch2 requires "护城河" or "竞争" keywords
    mock_llm.return_value = "公司具有强大的护城河，在竞争中占据优势。" * 50  # 500+ chars with required keywords

    signals = {
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bullish", confidence=0.70,
            reasoning="Strong moat",
            metrics={"moat_type": "Brand", "management_quality": "Excellent", "has_pricing_power": True}
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="bullish", confidence=0.65,
            reasoning="Value",
            metrics={"standards_passed": 5}
        ),
    }

    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    result = _generate_llm_chapter(
        "ch2_competitive", "TEST", "a_share", signals, quality_report, ""
    )

    assert len(result) > 500
    assert "⚠️" not in result  # No warning markers


@patch('src.llm.router.call_llm')
def test_generate_llm_chapter_fail_validation_retry(mock_llm):
    """LLM chapter should retry and append warning if validation fails."""
    # First call fails validation (too short)
    # Second call also fails
    # Third call also fails
    mock_llm.return_value = "短文本"

    signals = {}
    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    result = _generate_llm_chapter(
        "ch1_industry", "TEST", "a_share", signals, quality_report, "Test context"
    )

    # Should have warning marker
    assert "⚠️" in result
    assert "质量验证未通过" in result
    assert "字数不足" in result

    # Should have attempted retries (3 total calls)
    assert mock_llm.call_count == 3


def test_conservative_mode_output():
    """Test conservative mode generates proper warning output"""
    from src.agents.report_generator import generate_conservative_warning

    warning = generate_conservative_warning(
        company_name='某某公司',
        confidence=0.35,
        threshold=0.5
    )

    assert '⚠️' in warning
    assert '35%' in warning
    assert '置信度' in warning
    assert '仅供参考' in warning
