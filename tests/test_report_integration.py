"""Integration tests for full report generation."""

import pytest
from unittest.mock import patch, MagicMock
from src.agents.report_generator import run
from src.data.models import AgentSignal, QualityReport


@patch('src.llm.router.call_llm')
def test_full_report_generation(mock_llm):
    """Full report should generate all 8 chapters."""
    # Mock LLM to return valid chapters
    mock_llm.return_value = "这是一个有效的章节内容，包含护城河和竞争优势分析。" * 60  # 600+ chars with keywords

    # Mock signals
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="neutral", confidence=0.55,
            reasoning="财务稳健",
            metrics={"total_score": 60, "revenue_score": 15, "profitability_score": 15,
                     "leverage_score": 15, "cash_flow_score": 15, "revenue": 1e10, "revenue_growth": 0.1,
                     "roe": 15, "debt_ratio": 0.5}
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="neutral", confidence=0.60,
            reasoning="估值合理",
            metrics={"dcf_per_share": 20, "current_price": 19, "margin_of_safety": 0.05}
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="neutral", confidence=0.50,
            reasoning="护城河一般",
            metrics={"moat_type": "Brand", "management_quality": "Good", "has_pricing_power": True}
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="neutral", confidence=0.50,
            reasoning="价值适中",
            metrics={"standards_passed": 4}
        ),
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="neutral", confidence=0.50,
            reasoning="市场情绪中性",
            metrics={"sentiment_score": 0.5}
        ),
        "contrarian": AgentSignal(
            ticker="TEST", agent_name="contrarian",
            signal="neutral", confidence=0.60,
            reasoning="信号分歧",
            metrics={
                "mode": "critical_questions",
                "consensus": {"direction": "mixed", "strength": 0.5},
                "core_contradiction": "基本面稳健但估值争议",
                "questions": [
                    {"question": "增长可持续性？", "preliminary_judgment": "不确定", "evidence_needed": "未来订单"}
                ]
            }
        ),
    }

    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    # Generate report
    report_text, report_path = run(
        ticker="TEST",
        market="a_share",
        signals=signals,
        quality_report=quality_report,
        analysis_date="2026-03-07",
        use_llm=True
    )

    # Verify all chapters present
    assert "## 1. 行业背景" in report_text or "## 行业背景" in report_text
    assert "## 2. 竞争力分析" in report_text or "## 竞争力" in report_text
    assert "## 3. 财务质量评估" in report_text
    assert "## 4. 估值分析" in report_text
    assert "## 5. 风险因素" in report_text
    assert "## 6. 市场情绪" in report_text or "## 市场" in report_text
    assert "## 7. 综合建议" in report_text or "## 综合" in report_text
    assert "## 附录" in report_text

    # Verify metadata
    assert "TEST 投资研究报告" in report_text
    assert "2026-03-07" in report_text
    assert "0.90/1.0" in report_text  # Quality score

    # Verify report length
    assert len(report_text) > 2000

    # Verify file saved
    assert report_path.exists()
    assert report_path.name == "TEST_2026-03-07.md"


def test_quick_report_unchanged():
    """Quick mode should still generate old-style report."""
    signals = {}
    quality_report = QualityReport(
        ticker="TEST", market="a_share",
        flags=[], overall_quality_score=0.90,
        data_completeness=0.95, stale_fields=[], records_checked={}
    )

    report_text, _ = run(
        ticker="TEST",
        market="a_share",
        signals=signals,
        quality_report=quality_report,
        use_llm=False  # Quick mode
    )

    # Should be old quick report format
    assert "投资研究快报（数据版）" in report_text
    assert "本报告为数据版" in report_text
