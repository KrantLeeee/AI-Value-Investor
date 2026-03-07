"""Contrarian Agent — dialectical analysis to challenge consensus.

The Contrarian Agent identifies when multiple investment agents agree
(bullish/bearish consensus) and generates counter-arguments to test
the robustness of the investment thesis.

Methodology:
  1. Determine consensus from other agents (≥60% agreement = consensus)
  2. Select analysis mode: Challenge consensus or explore mixed signals
  3. Generate contrarian arguments using LLM
  4. Return structured output with confidence scoring

Signal thresholds:
  Consensus ≥ 60%  → Challenge mode (contrarian analysis)
  Consensus < 60%  → Mixed mode (explore uncertainty)
"""

from src.data.models import AgentSignal, SignalType
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "contrarian"


def _determine_consensus(signals: dict[str, AgentSignal | None]) -> tuple[str, float]:
    """
    Determine if there's a bullish/bearish consensus among agent signals.

    Args:
        signals: Dict mapping agent names to AgentSignal objects

    Returns:
        Tuple of (direction, strength):
        - direction: "bullish", "bearish", or "mixed"
        - strength: ratio of agents agreeing (0.0 to 1.0)

    Logic:
        - Count bullish/bearish signals (exclude None and neutral)
        - Calculate bull_ratio = bullish_count / total_count
        - Calculate bear_ratio = bearish_count / total_count
        - If bull_ratio >= 0.6 → ("bullish", bull_ratio)
        - If bear_ratio >= 0.6 → ("bearish", bear_ratio)
        - Otherwise → ("mixed", max(bull_ratio, bear_ratio))
        - Empty signals → ("mixed", 0.0)
    """
    # Filter out None values and extract AgentSignal objects
    valid_signals = [s for s in signals.values() if s is not None]

    if not valid_signals:
        return ("mixed", 0.0)

    total_count = len(valid_signals)
    bullish_count = sum(1 for s in valid_signals if s.signal == "bullish")
    bearish_count = sum(1 for s in valid_signals if s.signal == "bearish")

    bull_ratio = bullish_count / total_count
    bear_ratio = bearish_count / total_count

    # Consensus threshold: 60%
    if bull_ratio >= 0.6:
        return ("bullish", round(bull_ratio, 3))
    elif bear_ratio >= 0.6:
        return ("bearish", round(bear_ratio, 3))
    else:
        max_ratio = max(bull_ratio, bear_ratio)
        return ("mixed", round(max_ratio, 3))


def _select_mode(consensus_direction: str, consensus_strength: float) -> tuple[str, SignalType]:
    """
    Select analysis mode based on consensus direction.

    Args:
        consensus_direction: "bullish", "bearish", or "mixed"
        consensus_strength: Strength of consensus (0.0 to 1.0)

    Returns:
        Tuple of (mode, signal):
        - mode: "bear_case", "bull_case", or "critical_questions"
        - signal: "bearish", "bullish", or "neutral"

    Logic:
        - Bullish consensus → Challenge with bear_case mode (bearish signal)
        - Bearish consensus → Challenge with bull_case mode (bullish signal)
        - Mixed consensus → Explore with critical_questions mode (neutral signal)
    """
    if consensus_direction == "bullish":
        return ("bear_case", "bearish")
    elif consensus_direction == "bearish":
        return ("bull_case", "bullish")
    else:  # mixed
        return ("critical_questions", "neutral")
