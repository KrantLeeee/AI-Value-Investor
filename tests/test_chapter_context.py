"""Tests for ChapterContext cross-chapter information sharing."""

import pytest
from src.agents.chapter_context import ChapterContext
from src.data.models import AgentSignal, QualityReport, QualityFlag


def _make_signal(
    agent: str,
    signal: str = "neutral",
    confidence: float = 0.5,
    metrics: dict | None = None,
    reasoning: str = "test",
) -> AgentSignal:
    """Helper to create AgentSignal for testing."""
    return AgentSignal(
        ticker="TEST.SH",
        agent_name=agent,
        signal=signal,
        confidence=confidence,
        metrics=metrics or {},
        reasoning=reasoning,
    )


class TestChapterContextFromSignals:
    """Tests for ChapterContext.from_agent_signals()."""

    def test_empty_signals(self):
        """Empty signals should return default context."""
        context = ChapterContext.from_agent_signals({})
        assert context.fundamental_signal == "neutral"
        assert context.valuation_signal == "neutral"
        assert context.sentiment_direction == "neutral"
        assert context.data_quality_score == 0.0

    def test_fundamentals_signal_extraction(self):
        """Fundamentals signal and metrics should be extracted."""
        signals = {
            "fundamentals": _make_signal(
                "fundamentals",
                signal="bullish",
                confidence=0.75,
                metrics={
                    "score": 85,
                    "roe": 22.5,
                    "net_margin": 18.0,
                    "de_ratio": 0.3,
                    "revenue_growth": 15.0,
                    "operating_cash_flow": 1e9,
                },
            ),
        }
        context = ChapterContext.from_agent_signals(signals)
        assert context.fundamental_signal == "bullish"
        assert context.fundamental_score == 85
        assert context.fundamental_key_metrics["roe"] == 22.5
        assert context.fundamental_key_metrics["net_margin"] == 18.0

    def test_valuation_signal_extraction(self):
        """Valuation signal and metrics should be extracted."""
        signals = {
            "valuation": _make_signal(
                "valuation",
                signal="bullish",
                confidence=0.8,
                metrics={
                    "valuation_mode": "growth_stock",
                    "margin_of_safety": 0.25,
                    "validation": {
                        "weighted_target": 75.0,
                        "valid_methods": ["DCF", "PEG"],
                        "excluded_methods": ["Graham"],
                    },
                    "industry": "tech",
                },
            ),
        }
        context = ChapterContext.from_agent_signals(signals)
        assert context.valuation_signal == "bullish"
        assert context.valuation_mode == "growth_stock"
        assert context.valuation_target_price == 75.0
        assert context.valuation_margin_of_safety == 0.25
        assert "DCF" in context.valuation_methods_used
        assert "Graham" in context.valuation_methods_excluded
        assert context.industry == "tech"

    def test_sentiment_signal_extraction_bullish(self):
        """Bullish sentiment should map to positive direction."""
        signals = {
            "sentiment": _make_signal(
                "sentiment",
                signal="bullish",
                confidence=0.7,
                metrics={
                    "key_events": ["Q3超预期", "产品涨价"],
                    "profit_warning": "预增",
                    "profit_warning_details": "预计净利增长50%",
                },
            ),
        }
        context = ChapterContext.from_agent_signals(signals)
        assert context.sentiment_direction == "positive"
        assert context.sentiment_confidence == 0.7
        assert "Q3超预期" in context.sentiment_key_events
        assert context.profit_warning == "预增"
        assert context.profit_warning_details == "预计净利增长50%"

    def test_sentiment_signal_extraction_bearish(self):
        """Bearish sentiment should map to negative direction."""
        signals = {
            "sentiment": _make_signal(
                "sentiment",
                signal="bearish",
                confidence=0.65,
                metrics={"key_events": ["业绩下滑"]},
            ),
        }
        context = ChapterContext.from_agent_signals(signals)
        assert context.sentiment_direction == "negative"

    def test_contrarian_signal_extraction(self):
        """Contrarian mode and key points should be extracted."""
        signals = {
            "contrarian": _make_signal(
                "contrarian",
                signal="neutral",
                metrics={
                    "mode": "bear_case",
                    "key_points": ["油价风险", "地缘政治"],
                },
            ),
        }
        context = ChapterContext.from_agent_signals(signals)
        assert context.contrarian_mode == "bear_case"
        assert "油价风险" in context.contrarian_key_points

    def test_quality_report_extraction(self):
        """Quality report data should be extracted."""
        signals = {}
        quality_report = QualityReport(
            ticker="TEST",
            market="a_share",
            overall_quality_score=0.85,
            data_completeness=0.9,
            flags=[
                QualityFlag(flag="data_delay", field="price", detail="数据延迟", severity="critical"),
                QualityFlag(flag="roe_volatility", field="roe", detail="ROE异常波动", severity="warning"),
            ],
            stale_fields=[],
            records_checked={},
        )
        context = ChapterContext.from_agent_signals(signals, quality_report)
        assert context.data_quality_score == 0.85
        assert "数据延迟" in context.data_quality_critical_flags
        assert "ROE异常波动" in context.data_quality_warnings


class TestChapterContextBlocks:
    """Tests for context block generation methods."""

    def test_get_ch7_context_block_basic(self):
        """Basic context block should contain all sections."""
        context = ChapterContext(
            fundamental_signal="bullish",
            fundamental_score=80,
            valuation_signal="bullish",
            valuation_target_price=50.0,
            valuation_margin_of_safety=0.2,
            valuation_mode="standard",
            valuation_methods_used=["DCF", "EV/EBITDA"],
            sentiment_direction="positive",
            sentiment_confidence=0.7,
        )
        block = context.get_ch7_context_block()
        assert "基本面信号" in block
        assert "bullish" in block
        assert "80/100" in block
        assert "估值信号" in block
        assert "¥50.00" in block
        assert "20.0%" in block
        assert "情绪方向" in block
        assert "positive" in block

    def test_get_ch7_context_block_with_profit_warning(self):
        """Profit warning should appear in context block."""
        context = ChapterContext(
            profit_warning="预增",
            profit_warning_details="净利增长50%",
        )
        block = context.get_ch7_context_block()
        assert "业绩预告" in block
        assert "预增" in block
        assert "净利增长50%" in block

    def test_get_ch7_context_block_with_contrarian(self):
        """Contrarian mode should appear in context block."""
        context = ChapterContext(
            contrarian_mode="bear_case",
            contrarian_key_points=["估值过高", "竞争加剧"],
        )
        block = context.get_ch7_context_block()
        assert "辩证分析模式" in block
        assert "bear_case" in block
        assert "估值过高" in block

    def test_get_ch7_context_block_with_quality_warnings(self):
        """Quality warnings should appear in context block."""
        context = ChapterContext(
            data_quality_critical_flags=["数据缺失", "数据过期"],
        )
        block = context.get_ch7_context_block()
        assert "数据质量警告" in block
        assert "数据缺失" in block


class TestConsistencyRequirements:
    """Tests for consistency requirements generation."""

    def test_sentiment_consistency_rule(self):
        """Sentiment direction should generate consistency rule."""
        context = ChapterContext(sentiment_direction="negative")
        rules = context.get_consistency_requirements()
        assert "情绪方向" in rules
        assert "negative" in rules
        assert "一致" in rules

    def test_profit_warning_rule(self):
        """Profit warning should generate mandatory mention rule."""
        context = ChapterContext(profit_warning="预亏")
        rules = context.get_consistency_requirements()
        assert "业绩预告" in rules
        assert "预亏" in rules
        assert "明确提及" in rules

    def test_valuation_mode_rule_growth_stock(self):
        """Non-standard valuation mode should generate awareness rule."""
        context = ChapterContext(valuation_mode="growth_stock")
        rules = context.get_consistency_requirements()
        assert "盈利成长股" in rules
        assert "PEG" in rules

    def test_valuation_mode_rule_financial(self):
        """Financial mode should generate appropriate rule."""
        context = ChapterContext(valuation_mode="financial")
        rules = context.get_consistency_requirements()
        assert "金融股" in rules
        assert "P/B-ROE" in rules

    def test_valuation_mode_rule_cyclical(self):
        """Cyclical mode should generate appropriate rule."""
        context = ChapterContext(valuation_mode="cyclical")
        rules = context.get_consistency_requirements()
        assert "周期股" in rules

    def test_data_quality_rule(self):
        """Data quality issues should generate caution rule."""
        context = ChapterContext(
            data_quality_critical_flags=["关键数据缺失"]
        )
        rules = context.get_consistency_requirements()
        assert "数据质量" in rules
        assert "关键数据缺失" in rules
        assert "谨慎" in rules

    def test_standard_mode_no_rule(self):
        """Standard valuation mode should not generate extra rule."""
        context = ChapterContext(valuation_mode="standard")
        rules = context.get_consistency_requirements()
        # Standard mode doesn't add a specific rule
        assert "估值方法已针对性调整" not in rules


class TestInjectIntoPrompt:
    """Tests for prompt template injection."""

    def test_inject_chapter_context_placeholder(self):
        """chapter_context placeholder should be replaced."""
        context = ChapterContext(
            fundamental_signal="bullish",
            fundamental_score=75,
        )
        template = "分析结果:\n{chapter_context}\n结束"
        result = context.inject_into_prompt(template)
        assert "{chapter_context}" not in result
        assert "基本面信号" in result
        assert "bullish" in result

    def test_inject_consistency_requirements_placeholder(self):
        """consistency_requirements placeholder should be replaced."""
        context = ChapterContext(
            sentiment_direction="positive",
            profit_warning="扭亏",
        )
        template = "要求:\n{consistency_requirements}\n结束"
        result = context.inject_into_prompt(template)
        assert "{consistency_requirements}" not in result
        assert "情绪方向" in result
        assert "业绩预告" in result

    def test_inject_individual_placeholders(self):
        """Individual field placeholders should be replaced."""
        context = ChapterContext(
            fundamental_signal="bearish",
            fundamental_score=45,
            valuation_signal="neutral",
            valuation_target_price=30.0,
            valuation_mode="cyclical",
            sentiment_direction="negative",
            industry="energy",
        )
        template = (
            "信号: {fundamental_signal}, 评分: {fundamental_score}, "
            "估值: {valuation_signal}, 目标: {valuation_target_price}, "
            "模式: {valuation_mode}, 情绪: {sentiment_direction}, "
            "行业: {industry}"
        )
        result = context.inject_into_prompt(template)
        assert "bearish" in result
        assert "45" in result
        assert "neutral" in result
        assert "¥30.00" in result
        assert "cyclical" in result
        assert "negative" in result
        assert "energy" in result

    def test_inject_none_values_as_na(self):
        """None values should be replaced with appropriate defaults."""
        context = ChapterContext(
            fundamental_score=None,
            valuation_target_price=None,
            profit_warning=None,
            contrarian_mode=None,
        )
        template = (
            "评分: {fundamental_score}, 目标: {valuation_target_price}, "
            "预告: {profit_warning}, 辩证: {contrarian_mode}"
        )
        result = context.inject_into_prompt(template)
        assert "N/A" in result
        assert "无" in result

    def test_inject_empty_events(self):
        """Empty events list should show '无'."""
        context = ChapterContext(sentiment_key_events=[])
        template = "事件: {sentiment_key_events}"
        result = context.inject_into_prompt(template)
        assert "无" in result


class TestIntegrationScenarios:
    """Integration tests with realistic signal combinations."""

    def test_growth_stock_full_context(self):
        """Growth stock with all signals should produce complete context."""
        signals = {
            "fundamentals": _make_signal(
                "fundamentals",
                signal="bullish",
                metrics={"score": 82, "roe": 25.0, "revenue_growth": 30.0},
            ),
            "valuation": _make_signal(
                "valuation",
                signal="bullish",
                metrics={
                    "valuation_mode": "growth_stock",
                    "margin_of_safety": 0.15,
                    "validation": {
                        "weighted_target": 80.0,
                        "valid_methods": ["DCF", "PEG"],
                        "excluded_methods": [],
                    },
                    "industry": "tech",
                },
            ),
            "sentiment": _make_signal(
                "sentiment",
                signal="bullish",
                confidence=0.8,
                metrics={"key_events": ["订单增长", "新产品发布"]},
            ),
            "contrarian": _make_signal(
                "contrarian",
                metrics={"mode": "bear_case", "key_points": ["估值偏高"]},
            ),
        }
        context = ChapterContext.from_agent_signals(signals)

        # Verify context extraction
        assert context.valuation_mode == "growth_stock"
        assert context.valuation_target_price == 80.0
        assert context.sentiment_direction == "positive"
        assert context.industry == "tech"

        # Verify context block
        block = context.get_ch7_context_block()
        assert "growth_stock" in block
        assert "PEG" in block
        assert "订单增长" in block

        # Verify consistency requirements
        rules = context.get_consistency_requirements()
        assert "盈利成长股" in rules

    def test_financial_stock_with_quality_issues(self):
        """Financial stock with data quality issues."""
        signals = {
            "valuation": _make_signal(
                "valuation",
                signal="neutral",
                metrics={
                    "valuation_mode": "financial",
                    "industry": "banking",
                },
            ),
        }
        quality_report = QualityReport(
            ticker="TEST",
            market="a_share",
            overall_quality_score=0.65,
            data_completeness=0.7,
            flags=[
                QualityFlag(flag="missing_bs", field="balance_sheet", detail="资产负债表数据缺失", severity="critical"),
            ],
            stale_fields=[],
            records_checked={},
        )
        context = ChapterContext.from_agent_signals(signals, quality_report)

        assert context.valuation_mode == "financial"
        assert context.data_quality_score == 0.65
        assert "资产负债表数据缺失" in context.data_quality_critical_flags

        rules = context.get_consistency_requirements()
        assert "金融股" in rules
        assert "数据质量" in rules
