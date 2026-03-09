"""Tests for profit warning (业绩预告) data source integration."""

from datetime import date

import pytest

from src.data.models import ProfitWarning


class TestProfitWarningModel:
    """Tests for ProfitWarning data model."""

    def test_profit_warning_creation(self):
        """ProfitWarning should be created with all fields."""
        pw = ProfitWarning(
            ticker="002230.SZ",
            report_date=date(2024, 12, 31),
            publish_date=date(2025, 1, 15),
            warning_type="预增",
            change_pct_min=50.0,
            change_pct_max=80.0,
            profit_min=5e9,
            profit_max=7e9,
            last_year_profit=4e9,
            reason="主营业务增长",
            source="akshare",
        )
        assert pw.ticker == "002230.SZ"
        assert pw.warning_type == "预增"
        assert pw.change_pct_min == 50.0
        assert pw.change_pct_max == 80.0

    def test_profit_warning_optional_fields(self):
        """ProfitWarning should work with optional fields as None."""
        pw = ProfitWarning(
            ticker="002230.SZ",
            report_date=date(2024, 12, 31),
            publish_date=date(2025, 1, 15),
            warning_type="不确定",
            source="akshare",
        )
        assert pw.change_pct_min is None
        assert pw.change_pct_max is None
        assert pw.profit_min is None
        assert pw.reason is None


class TestExtractProfitWarningInfo:
    """Tests for _extract_profit_warning_info function."""

    def test_extract_yuezeng_with_full_data(self):
        """预增 with full data should extract type and details."""
        from src.agents.sentiment import _extract_profit_warning_info

        warnings = [
            ProfitWarning(
                ticker="002230.SZ",
                report_date=date(2024, 12, 31),
                publish_date=date(2025, 1, 15),
                warning_type="预增",
                change_pct_min=50.0,
                change_pct_max=80.0,
                profit_min=5e9,
                profit_max=7e9,
                source="akshare",
            )
        ]
        warning_type, details = _extract_profit_warning_info(warnings)
        assert warning_type == "预增"
        assert "预计增幅50%~80%" in details
        assert "预计净利润50.00~70.00亿" in details
        assert "报告期:2024-12-31" in details

    def test_extract_yukui(self):
        """预亏 should extract warning type correctly."""
        from src.agents.sentiment import _extract_profit_warning_info

        warnings = [
            ProfitWarning(
                ticker="300001.SZ",
                report_date=date(2024, 12, 31),
                publish_date=date(2025, 1, 10),
                warning_type="预亏",
                change_pct_min=-100.0,
                change_pct_max=-80.0,
                profit_min=-2e9,
                profit_max=-1e9,
                source="akshare",
            )
        ]
        warning_type, details = _extract_profit_warning_info(warnings)
        assert warning_type == "预亏"
        assert details is not None

    def test_extract_niukui(self):
        """扭亏 should extract warning type correctly."""
        from src.agents.sentiment import _extract_profit_warning_info

        warnings = [
            ProfitWarning(
                ticker="600001.SH",
                report_date=date(2024, 12, 31),
                publish_date=date(2025, 1, 20),
                warning_type="扭亏",
                profit_min=1e8,
                profit_max=2e8,
                last_year_profit=-5e8,
                source="akshare",
            )
        ]
        warning_type, details = _extract_profit_warning_info(warnings)
        assert warning_type == "扭亏"
        assert "预计净利润" in details

    def test_extract_empty_warnings(self):
        """Empty warnings list should return (None, None)."""
        from src.agents.sentiment import _extract_profit_warning_info

        warning_type, details = _extract_profit_warning_info([])
        assert warning_type is None
        assert details is None

    def test_extract_same_change_pct(self):
        """Same min/max change pct should show single value."""
        from src.agents.sentiment import _extract_profit_warning_info

        warnings = [
            ProfitWarning(
                ticker="002230.SZ",
                report_date=date(2024, 12, 31),
                publish_date=date(2025, 1, 15),
                warning_type="略增",
                change_pct_min=30.0,
                change_pct_max=30.0,
                source="akshare",
            )
        ]
        warning_type, details = _extract_profit_warning_info(warnings)
        assert warning_type == "略增"
        assert "预计增幅30%" in details
        assert "~" not in details.split("，")[0]  # No range when same

    def test_extract_only_max_change(self):
        """Only max change pct should be handled."""
        from src.agents.sentiment import _extract_profit_warning_info

        warnings = [
            ProfitWarning(
                ticker="002230.SZ",
                report_date=date(2024, 12, 31),
                publish_date=date(2025, 1, 15),
                warning_type="预增",
                change_pct_max=100.0,
                source="akshare",
            )
        ]
        warning_type, details = _extract_profit_warning_info(warnings)
        assert warning_type == "预增"
        assert "100%" in details

    def test_extract_most_recent(self):
        """Should use the most recent (first) warning."""
        from src.agents.sentiment import _extract_profit_warning_info

        warnings = [
            ProfitWarning(
                ticker="002230.SZ",
                report_date=date(2024, 12, 31),
                publish_date=date(2025, 1, 15),
                warning_type="预增",
                change_pct_min=50.0,
                change_pct_max=80.0,
                source="akshare",
            ),
            ProfitWarning(
                ticker="002230.SZ",
                report_date=date(2024, 6, 30),
                publish_date=date(2024, 7, 15),
                warning_type="略增",
                change_pct_min=10.0,
                change_pct_max=20.0,
                source="akshare",
            ),
        ]
        warning_type, details = _extract_profit_warning_info(warnings)
        assert warning_type == "预增"  # Most recent
        assert "50%" in details


class TestSentimentAgentIntegration:
    """Tests for sentiment agent integration with profit warnings."""

    def test_sentiment_metrics_include_profit_warning(self, monkeypatch):
        """Sentiment agent should include profit_warning in metrics."""
        from src.data.models import ProfitWarning as PWModel
        from src.agents import sentiment

        # Mock _get_profit_warnings to return test data
        def mock_get_profit_warnings(ticker, market):
            return [
                PWModel(
                    ticker=ticker,
                    report_date=date(2024, 12, 31),
                    publish_date=date(2025, 1, 15),
                    warning_type="预增",
                    change_pct_min=30.0,
                    change_pct_max=50.0,
                    source="test",
                )
            ]

        # Mock news to avoid actual API calls
        def mock_get_news_akshare(ticker, market):
            return ["公司业绩大幅增长", "订单持续增加"]

        # Mock LLM to avoid actual calls
        def mock_call_llm(task, system, user):
            return '{"signal": "bullish", "confidence": 0.7, "sentiment_score": 0.5}'

        # Mock DB insert
        def mock_insert(signal):
            pass

        monkeypatch.setattr(sentiment, "_get_profit_warnings", mock_get_profit_warnings)
        monkeypatch.setattr(sentiment, "_get_news_from_akshare", mock_get_news_akshare)
        monkeypatch.setattr("src.agents.sentiment.insert_agent_signal", mock_insert)

        # Run agent
        result = sentiment.run("002230.SZ", "a_share", use_llm=False)

        # Verify profit_warning is in metrics
        assert result.metrics.get("profit_warning") == "预增"
        assert "预计增幅30%~50%" in result.metrics.get("profit_warning_details", "")

    def test_sentiment_no_profit_warning(self, monkeypatch):
        """Sentiment agent should handle no profit warning gracefully."""
        from src.agents import sentiment

        def mock_get_profit_warnings(ticker, market):
            return []

        def mock_get_news_akshare(ticker, market):
            return ["普通新闻"]

        def mock_insert(signal):
            pass

        monkeypatch.setattr(sentiment, "_get_profit_warnings", mock_get_profit_warnings)
        monkeypatch.setattr(sentiment, "_get_news_from_akshare", mock_get_news_akshare)
        monkeypatch.setattr("src.agents.sentiment.insert_agent_signal", mock_insert)

        result = sentiment.run("600000.SH", "a_share", use_llm=False)

        assert result.metrics.get("profit_warning") is None
        assert result.metrics.get("profit_warning_details") is None


class TestChapterContextIntegration:
    """Tests for ChapterContext integration with profit warnings."""

    def test_chapter_context_extracts_profit_warning(self):
        """ChapterContext should extract profit_warning from sentiment metrics."""
        from src.agents.chapter_context import ChapterContext
        from src.data.models import AgentSignal

        signals = {
            "sentiment": AgentSignal(
                ticker="002230.SZ",
                agent_name="sentiment",
                signal="bullish",
                confidence=0.7,
                metrics={
                    "profit_warning": "预增",
                    "profit_warning_details": "预计增幅50%~80%",
                    "key_events": ["业绩大增"],
                },
            ),
        }

        context = ChapterContext.from_agent_signals(signals)

        assert context.profit_warning == "预增"
        assert context.profit_warning_details == "预计增幅50%~80%"

    def test_chapter_context_profit_warning_in_block(self):
        """Ch7 context block should include profit warning."""
        from src.agents.chapter_context import ChapterContext

        context = ChapterContext(
            profit_warning="预增",
            profit_warning_details="预计净利增长50%~80%",
        )

        block = context.get_ch7_context_block()

        assert "业绩预告" in block
        assert "预增" in block
        assert "50%~80%" in block

    def test_chapter_context_consistency_rule_profit_warning(self):
        """Consistency requirements should include profit warning rule."""
        from src.agents.chapter_context import ChapterContext

        context = ChapterContext(
            profit_warning="预亏",
            profit_warning_details="预计亏损1~2亿",
        )

        rules = context.get_consistency_requirements()

        assert "业绩预告" in rules
        assert "预亏" in rules
        assert "明确提及" in rules
