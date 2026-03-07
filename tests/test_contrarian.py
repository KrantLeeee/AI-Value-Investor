"""Tests for Contrarian Agent - Task 1: Consensus Calculation Logic."""

import pytest

from src.agents.contrarian import _determine_consensus
from src.data.models import AgentSignal


def test_consensus_bullish():
    """4/5 agents bullish should return ('bullish', 0.8)."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bullish", confidence=0.8, reasoning="Strong fundamentals"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="bullish", confidence=0.7, reasoning="Undervalued"
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bullish", confidence=0.9, reasoning="Quality business"
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="bullish", confidence=0.75, reasoning="Margin of safety"
        ),
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="bearish", confidence=0.6, reasoning="Negative sentiment"
        ),
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "bullish"
    assert strength == 0.8


def test_consensus_bearish():
    """3/4 agents bearish should return ('bearish', 0.75)."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bearish", confidence=0.7, reasoning="Weak fundamentals"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="neutral", confidence=0.5, reasoning="Fair value"
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bearish", confidence=0.8, reasoning="Poor moat"
        ),
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="bearish", confidence=0.75, reasoning="Negative sentiment"
        ),
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "bearish"
    assert strength == 0.75


def test_consensus_mixed():
    """2 bullish, 2 bearish should return ('mixed', 0.5)."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bullish", confidence=0.6, reasoning="Mixed signals"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="bearish", confidence=0.6, reasoning="Overvalued"
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bullish", confidence=0.7, reasoning="Quality business"
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="bearish", confidence=0.65, reasoning="No margin of safety"
        ),
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "mixed"
    assert strength == 0.5


def test_consensus_threshold():
    """Exactly 60% should trigger consensus (3/5 = 0.6)."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bullish", confidence=0.7, reasoning="Good fundamentals"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="bullish", confidence=0.65, reasoning="Fair value"
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bullish", confidence=0.8, reasoning="Quality company"
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="bearish", confidence=0.6, reasoning="Price concerns"
        ),
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="bearish", confidence=0.55, reasoning="Market skepticism"
        ),
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "bullish"
    assert strength == 0.6


def test_consensus_no_signals():
    """Empty dict should return ('mixed', 0.0)."""
    signals = {}
    direction, strength = _determine_consensus(signals)
    assert direction == "mixed"
    assert strength == 0.0


def test_consensus_with_none_values():
    """None values should be excluded from calculation."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bullish", confidence=0.7, reasoning="Strong fundamentals"
        ),
        "valuation": None,
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="bullish", confidence=0.8, reasoning="Quality business"
        ),
        "ben_graham": None,
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="bearish", confidence=0.6, reasoning="Negative sentiment"
        ),
    }
    direction, strength = _determine_consensus(signals)
    # 2 bullish, 1 bearish out of 3 total = 2/3 = 0.667 bullish
    assert direction == "bullish"
    assert abs(strength - 0.667) < 0.01


def test_consensus_with_neutral_signals():
    """Neutral signals should not count towards bullish or bearish."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bullish", confidence=0.6, reasoning="Moderate fundamentals"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="neutral", confidence=0.5, reasoning="Fair value"
        ),
        "warren_buffett": AgentSignal(
            ticker="TEST", agent_name="warren_buffett",
            signal="neutral", confidence=0.5, reasoning="Uncertain moat"
        ),
        "ben_graham": AgentSignal(
            ticker="TEST", agent_name="ben_graham",
            signal="bearish", confidence=0.65, reasoning="Limited margin of safety"
        ),
        "sentiment": AgentSignal(
            ticker="TEST", agent_name="sentiment",
            signal="bearish", confidence=0.7, reasoning="Negative sentiment"
        ),
    }
    direction, strength = _determine_consensus(signals)
    # 1 bullish, 2 bearish out of 5 total = bearish at 2/5 = 0.4 (< 0.6)
    # 1 bullish = 1/5 = 0.2 (< 0.6)
    # Should return mixed with max(0.4, 0.2) = 0.4
    assert direction == "mixed"
    assert strength == 0.4
