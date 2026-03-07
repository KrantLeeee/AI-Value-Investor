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

from src.data.models import AgentSignal, SignalType, MarketType, QualityReport
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "contrarian"


def _determine_consensus(signals: dict[str, SignalType | None]) -> tuple[str, float]:
    """
    Determine if there's a bullish/bearish consensus among agent signals.

    Args:
        signals: Dict mapping agent names to their signals (bullish/neutral/bearish/None)

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
    # Filter out None values
    valid_signals = {k: v for k, v in signals.items() if v is not None}

    if not valid_signals:
        return ("mixed", 0.0)

    total_count = len(valid_signals)
    bullish_count = sum(1 for signal in valid_signals.values() if signal == "bullish")
    bearish_count = sum(1 for signal in valid_signals.values() if signal == "bearish")

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
