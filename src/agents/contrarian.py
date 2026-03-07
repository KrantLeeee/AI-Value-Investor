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

from src.data.models import AgentSignal, SignalType, QualityReport
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


def _format_quality_context(quality_report: QualityReport) -> str:
    """Format quality report into human-readable context."""
    lines = []
    lines.append(f"质量分数: {quality_report.overall_quality_score:.2f}")
    lines.append(f"完整度: {quality_report.data_completeness:.2%}")

    if quality_report.flags:
        lines.append(f"\n发现 {len(quality_report.flags)} 个数据质量问题:")
        for flag in quality_report.flags[:3]:  # Limit to top 3
            lines.append(f"- [{flag.severity.upper()}] {flag.detail}")

    if not quality_report.flags:
        lines.append("数据质量良好，无重大问题。")

    return "\n".join(lines)


def _build_prompt(
    mode: str,
    consensus_direction: str,
    consensus_strength: float,
    signals: dict[str, AgentSignal | None],
    quality_report: QualityReport,
) -> tuple[str, str]:
    """
    Construct dynamic prompts based on mode and consensus.

    Args:
        mode: "bear_case" | "bull_case" | "critical_questions"
        consensus_direction: "bullish" | "bearish" | "mixed"
        consensus_strength: 0.0-1.0
        signals: Front-running agent signals
        quality_report: Data quality context

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    from src.llm.prompts import (
        CONTRARIAN_BEAR_CASE_SYSTEM, CONTRARIAN_BEAR_CASE_USER,
        CONTRARIAN_BULL_CASE_SYSTEM, CONTRARIAN_BULL_CASE_USER,
        CONTRARIAN_CRITICAL_QUESTIONS_SYSTEM, CONTRARIAN_CRITICAL_QUESTIONS_USER,
    )

    # Select system prompt
    system_prompts = {
        "bear_case": CONTRARIAN_BEAR_CASE_SYSTEM,
        "bull_case": CONTRARIAN_BULL_CASE_SYSTEM,
        "critical_questions": CONTRARIAN_CRITICAL_QUESTIONS_SYSTEM,
    }
    system_prompt = system_prompts[mode]

    # Extract strongest arguments
    arguments = []
    valid_signals = [s for s in signals.values() if s is not None]

    if mode == "bear_case":
        # Extract bullish arguments
        for sig in valid_signals:
            if sig.signal == "bullish":
                # Limit to 200 chars
                reasoning = sig.reasoning[:200] if sig.reasoning else "无具体理由"
                arguments.append(f"[{sig.agent_name}] {reasoning}")

    elif mode == "bull_case":
        # Extract bearish arguments
        for sig in valid_signals:
            if sig.signal == "bearish":
                reasoning = sig.reasoning[:200] if sig.reasoning else "无具体理由"
                arguments.append(f"[{sig.agent_name}] {reasoning}")

    else:  # critical_questions
        # Extract all arguments
        for sig in valid_signals:
            reasoning = sig.reasoning[:200] if sig.reasoning else "无具体理由"
            arguments.append(f"[{sig.agent_name}/{sig.signal}] {reasoning}")

    # Format arguments
    if not arguments:
        arguments_text = "（前序分析师未提供明确论据）"
    else:
        arguments_text = "\n".join(arguments)

    # Format quality context
    quality_context = _format_quality_context(quality_report)

    # Select user template and fill
    user_templates = {
        "bear_case": CONTRARIAN_BEAR_CASE_USER,
        "bull_case": CONTRARIAN_BULL_CASE_USER,
        "critical_questions": CONTRARIAN_CRITICAL_QUESTIONS_USER,
    }
    user_template = user_templates[mode]

    # Fill user prompt
    if mode in ["bear_case", "bull_case"]:
        user_prompt = user_template.format(
            consensus_direction=consensus_direction,
            consensus_strength=consensus_strength,
            strongest_arguments=arguments_text,
            quality_context=quality_context,
        )
    else:  # critical_questions
        user_prompt = user_template.format(
            consensus_direction=consensus_direction,
            consensus_strength=consensus_strength,
            all_arguments=arguments_text,
            quality_context=quality_context,
        )

    return system_prompt, user_prompt
