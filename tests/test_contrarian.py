"""Tests for Contrarian Agent - Task 1: Consensus Calculation Logic."""

import json
import pytest

from src.agents.contrarian import _determine_consensus, _select_mode, _build_prompt, _validate_json
from src.data.models import AgentSignal, QualityReport


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


# ── Task 2: Mode Selection Logic Tests ───────────────────────────────────────


def test_mode_bear_case():
    """Bullish consensus → bear_case mode with bearish signal."""
    mode, signal = _select_mode("bullish", 0.8)
    assert mode == "bear_case"
    assert signal == "bearish"


def test_mode_bull_case():
    """Bearish consensus → bull_case mode with bullish signal."""
    mode, signal = _select_mode("bearish", 0.75)
    assert mode == "bull_case"
    assert signal == "bullish"


def test_mode_critical_questions():
    """Mixed consensus → critical_questions mode with neutral signal."""
    mode, signal = _select_mode("mixed", 0.5)
    assert mode == "critical_questions"
    assert signal == "neutral"


# ── Task 4: Prompt Construction Logic Tests ──────────────────────────────────


def test_prompt_extracts_strongest_args():
    """Prompt should include reasoning from consensus-aligned agents."""
    signals = {
        "fundamentals": AgentSignal(
            ticker="TEST", agent_name="fundamentals",
            signal="bullish", confidence=0.7,
            reasoning="Strong fundamentals with ROE 25% and debt ratio 0.3"
        ),
        "valuation": AgentSignal(
            ticker="TEST", agent_name="valuation",
            signal="bullish", confidence=0.6,
            reasoning="DCF shows 36% margin of safety with WACC 10%"
        ),
    }

    quality_report = QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.9,
        data_completeness=0.85,
        stale_fields=[],
        records_checked={}
    )

    mode = "bear_case"
    consensus_direction = "bullish"
    consensus_strength = 1.0

    system, user = _build_prompt(
        mode, consensus_direction, consensus_strength, signals, quality_report
    )

    # Verify system prompt is correct
    assert "辩证分析师" in system

    # Verify user prompt contains arguments
    assert "Strong fundamentals" in user
    assert "36% margin of safety" in user

    # Verify consensus info
    assert "bullish" in user
    assert "100%" in user or "1.0" in user


# ── Task 6: JSON Validation Tests ────────────────────────────────────────────


def test_validate_bear_case_json():
    """Valid bear case JSON should pass validation"""
    json_str = json.dumps({
        "mode": "bear_case",
        "consensus": {"direction": "bullish", "strength": 0.75},
        "assumption_challenges": [{
            "original_claim": "安全边际36%",
            "assumption": "WACC=10%",
            "challenge": "应用12%WACC",
            "impact_if_wrong": "安全边际缩至8%",
            "severity": "high"
        }],
        "risk_scenarios": [{
            "scenario": "油价下跌",
            "probability": "20-30%",
            "impact": "-25%营收",
            "precedent": "2020年Q1"
        }],
        "bear_case_target_price": 12.50,
        "reasoning": "综合分析"
    })

    is_valid, data = _validate_json(json_str, "bear_case")
    assert is_valid
    assert data["mode"] == "bear_case"
    assert len(data["assumption_challenges"]) == 1


def test_validate_invalid_json():
    """Invalid JSON should be caught"""
    json_str = "not valid json"

    is_valid, data = _validate_json(json_str, "bear_case")
    assert not is_valid
    assert data is None


# ── Task 7: Main run() Function Integration Tests ────────────────────────────


def test_run_no_signals():
    """No signals → return neutral with low confidence"""
    from src.agents.contrarian import run

    quality_report = QualityReport(
        ticker="TEST",
        market="a_share",
        flags=[],
        overall_quality_score=0.9,
        data_completeness=0.85,
        stale_fields=[],
        records_checked={}
    )

    result = run(
        ticker="TEST",
        market="a_share",
        signals={},
        quality_report=quality_report,
        use_llm=True,
    )

    assert result.agent_name == "contrarian"
    assert result.signal == "neutral"
    assert result.confidence == 0.20
    assert "无可用信号" in result.reasoning


def test_run_bullish_consensus_bear_case():
    """Bullish consensus → BEAR_CASE mode → bearish signal"""
    from src.agents.contrarian import run
    from unittest.mock import patch

    with patch('src.agents.contrarian._call_llm') as mock_llm:
        # Mock LLM response
        mock_llm.return_value = json.dumps({
            "mode": "bear_case",
            "consensus": {"direction": "bullish", "strength": 0.8},
            "assumption_challenges": [{
                "original_claim": "安全边际36%",
                "assumption": "WACC=10%",
                "challenge": "应用12%",
                "impact_if_wrong": "缩至8%",
                "severity": "high"
            }],
            "risk_scenarios": [{
                "scenario": "油价下跌",
                "probability": "20%",
                "impact": "-25%",
                "precedent": "2020"
            }],
            "bear_case_target_price": 12.50,
            "reasoning": "存在下行风险"
        })

        signals = {
            "fundamentals": AgentSignal(
                ticker="TEST", agent_name="fundamentals",
                signal="bullish", confidence=0.7, reasoning="Good"
            ),
            "valuation": AgentSignal(
                ticker="TEST", agent_name="valuation",
                signal="bullish", confidence=0.8, reasoning="Undervalued"
            ),
            "warren_buffett": AgentSignal(
                ticker="TEST", agent_name="warren_buffett",
                signal="bullish", confidence=0.8, reasoning="Moat"
            ),
            "ben_graham": AgentSignal(
                ticker="TEST", agent_name="ben_graham",
                signal="bullish", confidence=0.7, reasoning="Safe"
            ),
        }

        quality_report = QualityReport(
            ticker="TEST",
            market="a_share",
            flags=[],
            overall_quality_score=0.9,
            data_completeness=0.85,
            stale_fields=[],
            records_checked={}
        )

        result = run(
            ticker="TEST",
            market="a_share",
            signals=signals,
            quality_report=quality_report,
            use_llm=True,
        )

        assert result.agent_name == "contrarian"
        assert result.signal == "bearish"  # Challenge bulls
        assert result.confidence == 0.60  # Fixed MVP confidence
        assert "存在下行风险" in result.reasoning
        assert result.metrics["mode"] == "bear_case"
        assert result.metrics["consensus"]["direction"] == "bullish"
