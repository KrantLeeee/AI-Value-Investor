"""Tests for signal aggregation."""

import pytest
from src.data.models import AgentSignal, QualityReport
from src.agents.signal_aggregator import (
    aggregate_signals,
    explain_aggregation,
    create_aggregated_signal,
    _signal_to_number,
    _number_to_signal,
    _detect_conflicts,
)


def test_signal_to_number():
    """Signal conversion should map correctly."""
    assert _signal_to_number("bullish") == 1.0
    assert _signal_to_number("neutral") == 0.0
    assert _signal_to_number("bearish") == -1.0
    assert _signal_to_number("invalid") == 0.0


def test_number_to_signal():
    """Number conversion should apply thresholds."""
    assert _number_to_signal(0.5) == "bullish"
    assert _number_to_signal(0.26) == "bullish"
    assert _number_to_signal(0.25) == "neutral"
    assert _number_to_signal(0.0) == "neutral"
    assert _number_to_signal(-0.25) == "neutral"
    assert _number_to_signal(-0.26) == "bearish"
    assert _number_to_signal(-0.5) == "bearish"


def test_aggregate_signals_all_bullish():
    """All bullish signals should result in bullish."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bullish",
            confidence=0.65,
            reasoning="Undervalued",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    assert final_signal == "bullish"
    assert final_confidence > 0.5
    assert metadata["weighted_score"] > 0.25
    assert len(metadata["conflicts"]) == 0


def test_aggregate_signals_all_bearish():
    """All bearish signals should result in bearish."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bearish",
            confidence=0.70,
            reasoning="Weak fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.65,
            reasoning="Overvalued",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    assert final_signal == "bearish"
    assert final_confidence > 0.5
    assert metadata["weighted_score"] < -0.25
    assert len(metadata["conflicts"]) == 0


def test_aggregate_signals_mixed_neutral():
    """Mixed signals should result in neutral."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.50,
            reasoning="OK fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.50,
            reasoning="Fairly valued",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    assert final_signal == "neutral"
    assert -0.25 <= metadata["weighted_score"] <= 0.25


def test_aggregate_signals_industry_weights():
    """Industry-specific weights should be applied."""
    signals = {
        "sentiment": AgentSignal(
            ticker="600000.SH",
            agent_name="sentiment",
            signal="bullish",
            confidence=0.80,
            reasoning="Very positive sentiment",
        ),
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="neutral",
            confidence=0.50,
            reasoning="OK fundamentals",
        ),
    }

    # Tech industry has high sentiment weight (0.30 vs 0.15 default)
    tech_signal, tech_conf, tech_meta = aggregate_signals(signals, "tech")
    default_signal, default_conf, default_meta = aggregate_signals(signals, "default")

    # Tech should be more bullish due to higher sentiment weight
    assert tech_meta["weighted_score"] > default_meta["weighted_score"]


def test_aggregate_signals_no_signals():
    """No signals should return neutral with low confidence."""
    signals = {}

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    assert final_signal == "neutral"
    assert final_confidence == 0.10
    assert metadata["weighted_score"] == 0.0
    assert len(metadata["contributing_agents"]) == 0


def test_aggregate_signals_missing_agents():
    """Missing agents should be skipped gracefully."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
        # valuation, warren_buffett, ben_graham, sentiment missing
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    # Should still work with only fundamentals
    assert final_signal in ["bullish", "neutral", "bearish"]
    assert len(metadata["contributing_agents"]) == 1
    assert "fundamentals" in metadata["contributing_agents"]


def test_detect_conflicts_opposite_high_confidence():
    """Opposite signals with high confidence should be detected."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.75,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.70,
            reasoning="Overvalued",
        ),
    }

    agent_contributions = {
        "fundamentals": {
            "signal": "bullish",
            "confidence": 0.75,
            "weight": 0.25,
            "contribution": 0.1875,
        },
        "valuation": {
            "signal": "bearish",
            "confidence": 0.70,
            "weight": 0.25,
            "contribution": -0.175,
        },
    }

    conflicts = _detect_conflicts(signals, agent_contributions)

    assert len(conflicts) == 1
    assert ("fundamentals", "valuation") in conflicts or (
        "valuation",
        "fundamentals",
    ) in conflicts


def test_detect_conflicts_opposite_low_confidence():
    """Opposite signals with low confidence should NOT be detected."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.50,
            reasoning="OK fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.45,
            reasoning="Slightly overvalued",
        ),
    }

    agent_contributions = {
        "fundamentals": {
            "signal": "bullish",
            "confidence": 0.50,
            "weight": 0.25,
            "contribution": 0.125,
        },
        "valuation": {
            "signal": "bearish",
            "confidence": 0.45,
            "weight": 0.25,
            "contribution": -0.1125,
        },
    }

    conflicts = _detect_conflicts(signals, agent_contributions)

    # No conflicts because confidence < 0.6
    assert len(conflicts) == 0


def test_detect_conflicts_same_signal():
    """Same signals should never conflict."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.80,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bullish",
            confidence=0.75,
            reasoning="Undervalued",
        ),
    }

    agent_contributions = {
        "fundamentals": {
            "signal": "bullish",
            "confidence": 0.80,
            "weight": 0.25,
            "contribution": 0.20,
        },
        "valuation": {
            "signal": "bullish",
            "confidence": 0.75,
            "weight": 0.25,
            "contribution": 0.1875,
        },
    }

    conflicts = _detect_conflicts(signals, agent_contributions)

    assert len(conflicts) == 0


def test_aggregate_signals_conflict_penalty():
    """Conflicts should reduce final confidence."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.75,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.70,
            reasoning="Overvalued",
        ),
        "sentiment": AgentSignal(
            ticker="600000.SH",
            agent_name="sentiment",
            signal="bullish",
            confidence=0.65,
            reasoning="Positive sentiment",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    # Should have 1 conflict (fundamentals vs valuation)
    assert len(metadata["conflicts"]) >= 1
    # Conflict penalty should be applied
    assert metadata["conflict_penalty"] >= 0.10


def test_explain_aggregation_format():
    """Explanation should be well-formatted markdown."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="neutral",
            confidence=0.50,
            reasoning="Fair value",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "tech")

    explanation = explain_aggregation(metadata, signals)

    # Check markdown structure
    assert "### 信号聚合详情" in explanation
    assert "**加权评分**" in explanation
    assert "**行业分类**" in explanation
    assert "| Agent | 信号 | 置信度 | 权重 | 贡献 |" in explanation
    assert "fundamentals" in explanation
    assert "valuation" in explanation


def test_explain_aggregation_with_conflicts():
    """Explanation should include conflict warnings."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.75,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.70,
            reasoning="Overvalued",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    explanation = explain_aggregation(metadata, signals)

    if len(metadata["conflicts"]) > 0:
        assert "⚠️ 检测到" in explanation
        assert "信号冲突" in explanation
        assert "置信度惩罚" in explanation


def test_explain_aggregation_no_conflicts():
    """Explanation should show no conflicts message."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bullish",
            confidence=0.65,
            reasoning="Undervalued",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    explanation = explain_aggregation(metadata, signals)

    assert "✅ 无信号冲突" in explanation


def test_create_aggregated_signal():
    """create_aggregated_signal should return valid AgentSignal."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bullish",
            confidence=0.65,
            reasoning="Undervalued",
        ),
    }

    aggregated = create_aggregated_signal("600000.SH", signals, "consumer")

    assert isinstance(aggregated, AgentSignal)
    assert aggregated.ticker == "600000.SH"
    assert aggregated.agent_name == "signal_aggregator"
    assert aggregated.signal in ["bullish", "neutral", "bearish"]
    assert 0.10 <= aggregated.confidence <= 0.85
    assert "信号聚合详情" in aggregated.reasoning
    assert aggregated.metrics is not None
    assert "weighted_score" in aggregated.metrics
    assert "industry" in aggregated.metrics


def test_aggregate_signals_confidence_bounds():
    """Final confidence should respect 0.10-0.85 bounds."""
    # High confidence signals
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.85,
            reasoning="Excellent fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bullish",
            confidence=0.85,
            reasoning="Very undervalued",
        ),
        "warren_buffett": AgentSignal(
            ticker="600000.SH",
            agent_name="warren_buffett",
            signal="bullish",
            confidence=0.85,
            reasoning="Wide moat",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    # Should not exceed 0.85
    assert final_confidence <= 0.85
    # Should be at least 0.10
    assert final_confidence >= 0.10


def test_aggregate_signals_all_five_agents():
    """Test aggregation with all five agents."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bullish",
            confidence=0.65,
            reasoning="Undervalued",
        ),
        "warren_buffett": AgentSignal(
            ticker="600000.SH",
            agent_name="warren_buffett",
            signal="neutral",
            confidence=0.55,
            reasoning="Moderate moat",
        ),
        "ben_graham": AgentSignal(
            ticker="600000.SH",
            agent_name="ben_graham",
            signal="bullish",
            confidence=0.60,
            reasoning="Meets standards",
        ),
        "sentiment": AgentSignal(
            ticker="600000.SH",
            agent_name="sentiment",
            signal="bearish",
            confidence=0.50,
            reasoning="Negative sentiment",
        ),
    }

    final_signal, final_confidence, metadata = aggregate_signals(signals, "default")

    assert final_signal in ["bullish", "neutral", "bearish"]
    assert len(metadata["contributing_agents"]) == 5
    assert 0.10 <= final_confidence <= 0.85
    # With mixed signals, should likely be bullish or neutral
    # (4 bullish/neutral vs 1 bearish)
