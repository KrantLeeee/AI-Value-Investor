"""Tests for Contrarian Agent - Task 1: Consensus Calculation Logic."""

import pytest

from src.agents.contrarian import _determine_consensus
from src.data.models import SignalType


def test_consensus_bullish():
    """4/5 agents bullish should return ('bullish', 0.8)."""
    signals = {
        "fundamentals": "bullish",
        "valuation": "bullish",
        "warren_buffett": "bullish",
        "ben_graham": "bullish",
        "sentiment": "bearish",
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "bullish"
    assert strength == 0.8


def test_consensus_bearish():
    """3/4 agents bearish should return ('bearish', 0.75)."""
    signals = {
        "fundamentals": "bearish",
        "valuation": "neutral",
        "warren_buffett": "bearish",
        "sentiment": "bearish",
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "bearish"
    assert strength == 0.75


def test_consensus_mixed():
    """2 bullish, 2 bearish should return ('mixed', 0.5)."""
    signals = {
        "fundamentals": "bullish",
        "valuation": "bearish",
        "warren_buffett": "bullish",
        "ben_graham": "bearish",
    }
    direction, strength = _determine_consensus(signals)
    assert direction == "mixed"
    assert strength == 0.5


def test_consensus_threshold():
    """Exactly 60% should trigger consensus (3/5 = 0.6)."""
    signals = {
        "fundamentals": "bullish",
        "valuation": "bullish",
        "warren_buffett": "bullish",
        "ben_graham": "bearish",
        "sentiment": "bearish",
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
        "fundamentals": "bullish",
        "valuation": None,
        "warren_buffett": "bullish",
        "ben_graham": None,
        "sentiment": "bearish",
    }
    direction, strength = _determine_consensus(signals)
    # 2 bullish, 1 bearish out of 3 total = 2/3 = 0.667 bullish
    assert direction == "bullish"
    assert abs(strength - 0.667) < 0.01


def test_consensus_with_neutral_signals():
    """Neutral signals should not count towards bullish or bearish."""
    signals = {
        "fundamentals": "bullish",
        "valuation": "neutral",
        "warren_buffett": "neutral",
        "ben_graham": "bearish",
        "sentiment": "bearish",
    }
    direction, strength = _determine_consensus(signals)
    # 1 bullish, 2 bearish out of 5 total = bearish at 2/5 = 0.4 (< 0.6)
    # 1 bullish = 1/5 = 0.2 (< 0.6)
    # Should return mixed with max(0.4, 0.2) = 0.4
    assert direction == "mixed"
    assert strength == 0.4
