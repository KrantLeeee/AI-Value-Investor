"""Signal Aggregator - Combines multiple agent signals into final recommendation.

Implements weighted aggregation from PROJECT_ROADMAP.md P1-⑥:
1. Convert signals to numerical values (bullish=+1, neutral=0, bearish=-1)
2. Apply industry-specific weights
3. Weight by confidence scores
4. Detect conflicts
5. Output final signal with confidence

Formula: weighted_score = Σ(signal_num × weight × confidence)
"""

from src.data.models import AgentSignal
from src.agents.industry_classifier import get_agent_weights
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _signal_to_number(signal: str) -> float:
    """Convert signal string to numerical value."""
    mapping = {
        "bullish": 1.0,
        "neutral": 0.0,
        "bearish": -1.0,
    }
    return mapping.get(signal, 0.0)


def _number_to_signal(score: float) -> str:
    """Convert numerical score to signal string."""
    if score > 0.25:
        return "bullish"
    elif score < -0.25:
        return "bearish"
    else:
        return "neutral"


def aggregate_signals(
    signals: dict[str, AgentSignal],
    industry: str = "default",
) -> tuple[str, float, dict[str, any]]:
    """
    Aggregate multiple agent signals into final recommendation.

    Args:
        signals: Dict of agent_name -> AgentSignal
        industry: Industry classification for weighting

    Returns:
        Tuple of (final_signal, final_confidence, metadata)
        metadata includes: weighted_score, conflicts, contributing_agents
    """
    # Get industry-specific weights
    weights = get_agent_weights(industry)

    # Track contributions
    weighted_sum = 0.0
    total_weight = 0.0
    confidence_sum = 0.0
    contributing_agents = []
    agent_contributions = {}

    # Calculate weighted score
    for agent_name, weight in weights.items():
        signal = signals.get(agent_name)

        if signal is None:
            logger.debug(f"[Aggregator] {agent_name} not available, skipping")
            continue

        # Convert signal to number
        signal_num = _signal_to_number(signal.signal)
        confidence = signal.confidence

        # Calculate contribution
        contribution = signal_num * weight * confidence
        weighted_sum += contribution
        total_weight += weight
        confidence_sum += confidence * weight

        contributing_agents.append(agent_name)
        agent_contributions[agent_name] = {
            "signal": signal.signal,
            "confidence": confidence,
            "weight": weight,
            "contribution": contribution,
        }

        logger.debug(
            f"[Aggregator] {agent_name}: signal={signal.signal}, "
            f"conf={confidence:.2f}, weight={weight:.2f}, "
            f"contribution={contribution:+.3f}"
        )

    # Handle no signals case
    if total_weight == 0:
        logger.warning("[Aggregator] No agent signals available")
        return "neutral", 0.10, {
            "weighted_score": 0.0,
            "conflicts": [],
            "contributing_agents": [],
            "agent_contributions": {},
        }

    # Normalize by total weight used
    weighted_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    avg_confidence = confidence_sum / total_weight if total_weight > 0 else 0.0

    # Detect conflicts: agents with opposite signals and high confidence
    conflicts = _detect_conflicts(signals, agent_contributions)

    # Apply conflict penalty
    conflict_penalty = len(conflicts) * 0.10  # 10% per conflict
    final_confidence = max(0.10, min(0.85, avg_confidence * (1 - conflict_penalty)))

    # Convert score to final signal
    final_signal = _number_to_signal(weighted_score)

    logger.info(
        f"[Aggregator] Final: {final_signal} (conf={final_confidence:.2f}, "
        f"score={weighted_score:+.2f}, conflicts={len(conflicts)})"
    )

    metadata = {
        "weighted_score": weighted_score,
        "conflicts": conflicts,
        "contributing_agents": contributing_agents,
        "agent_contributions": agent_contributions,
        "industry": industry,
        "conflict_penalty": conflict_penalty,
    }

    return final_signal, final_confidence, metadata


def _detect_conflicts(
    signals: dict[str, AgentSignal],
    agent_contributions: dict[str, dict],
) -> list[tuple[str, str]]:
    """
    Detect conflicts between agents.

    A conflict is defined as two agents with:
    - Opposite signals (bullish vs bearish)
    - Both with confidence > 0.6

    Args:
        signals: All agent signals
        agent_contributions: Contribution details from aggregation

    Returns:
        List of (agent1, agent2) tuples representing conflicts
    """
    conflicts = []
    agent_list = list(agent_contributions.keys())

    for i, agent1 in enumerate(agent_list):
        for agent2 in agent_list[i + 1 :]:
            sig1 = signals[agent1]
            sig2 = signals[agent2]

            # Check for opposite signals
            if (
                (sig1.signal == "bullish" and sig2.signal == "bearish")
                or (sig1.signal == "bearish" and sig2.signal == "bullish")
            ):
                # Check if both are confident
                if sig1.confidence > 0.6 and sig2.confidence > 0.6:
                    conflicts.append((agent1, agent2))
                    logger.warning(
                        f"[Aggregator] Conflict detected: {agent1} ({sig1.signal}, "
                        f"{sig1.confidence:.2f}) vs {agent2} ({sig2.signal}, "
                        f"{sig2.confidence:.2f})"
                    )

    return conflicts


def explain_aggregation(
    metadata: dict[str, any],
    signals: dict[str, AgentSignal],
) -> str:
    """
    Generate human-readable explanation of aggregation.

    Args:
        metadata: Metadata from aggregate_signals()
        signals: Original agent signals

    Returns:
        Markdown-formatted explanation
    """
    lines = ["### 信号聚合详情", ""]

    # Overall result
    weighted_score = metadata["weighted_score"]
    final_signal = _number_to_signal(weighted_score)
    lines.append(f"**加权评分**: {weighted_score:+.2f} → **{final_signal.upper()}**")
    lines.append(f"**行业分类**: {metadata['industry']}")
    lines.append("")

    # Agent contributions
    lines.append("| Agent | 信号 | 置信度 | 权重 | 贡献 |")
    lines.append("|:------|:-----|:-------|:-----|:-----|")

    for agent, contrib in metadata["agent_contributions"].items():
        lines.append(
            f"| {agent} | {contrib['signal']} | "
            f"{contrib['confidence']:.0%} | "
            f"{contrib['weight']:.0%} | "
            f"{contrib['contribution']:+.3f} |"
        )

    lines.append("")

    # Conflicts
    if metadata["conflicts"]:
        lines.append(f"**⚠️ 检测到 {len(metadata['conflicts'])} 个信号冲突:**")
        lines.append("")
        for agent1, agent2 in metadata["conflicts"]:
            sig1 = signals[agent1]
            sig2 = signals[agent2]
            lines.append(
                f"- {agent1} ({sig1.signal}, {sig1.confidence:.0%}) "
                f"vs {agent2} ({sig2.signal}, {sig2.confidence:.0%})"
            )
        lines.append("")
        lines.append(
            f"置信度惩罚: {metadata['conflict_penalty']:.0%} "
            f"({len(metadata['conflicts'])} × 10%)"
        )
        lines.append("")
    else:
        lines.append("✅ 无信号冲突，Agent一致性良好")
        lines.append("")

    return "\n".join(lines)


def create_aggregated_signal(
    ticker: str,
    signals: dict[str, AgentSignal],
    industry: str,
) -> AgentSignal:
    """
    Create an AgentSignal for the aggregated result.

    Args:
        ticker: Stock ticker
        signals: Individual agent signals
        industry: Industry classification

    Returns:
        AgentSignal representing the aggregated recommendation
    """
    final_signal, final_confidence, metadata = aggregate_signals(signals, industry)

    # Generate explanation
    reasoning = explain_aggregation(metadata, signals)

    # Create signal
    return AgentSignal(
        ticker=ticker,
        agent_name="signal_aggregator",
        signal=final_signal,
        confidence=final_confidence,
        reasoning=reasoning,
        metrics=metadata,
    )
