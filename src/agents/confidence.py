"""Confidence Engine - Calculates reliable confidence scores for agent signals.

Implements the confidence formula from PROJECT_ROADMAP.md:
final = min(0.85, max(0.10, signal_strength × 0.5 + indicator_agreement × 0.5)) × data_quality_score

Design principles:
- 0.85 upper cap: No >85% confidence without historical calibration (Tetlock research)
- 0.10 lower bound: Some data better than no data
- Multiplicative quality penalty: Bad data degrades confidence
- Signal strength + indicator agreement weighted equally
"""

from src.data.models import AgentSignal, QualityReport
from src.utils.logger import get_logger

logger = get_logger(__name__)


def calculate_confidence(
    agent_name: str,
    signal_strength: float,
    indicator_agreement: float,
    quality_report: QualityReport | None = None,
    historical_calibration: float | None = None,
) -> float:
    """
    Calculate final confidence score for an agent signal.

    Args:
        agent_name: Agent identifier (for logging)
        signal_strength: 0.0-1.0, strength of primary signal
        indicator_agreement: 0.0-1.0, agreement among indicators
        quality_report: Data quality report (None = assume 0.5)
        historical_calibration: Future P3 feature (None = no adjustment)

    Returns:
        Final confidence score (0.10-0.85)
    """
    # Base confidence: equal weight to strength and agreement
    base_confidence = (signal_strength * 0.5) + (indicator_agreement * 0.5)

    # Apply bounds
    bounded_confidence = min(0.85, max(0.10, base_confidence))

    # Apply data quality penalty (multiplicative)
    data_quality_score = quality_report.overall_quality_score if quality_report else 0.5
    final_confidence = bounded_confidence * data_quality_score

    # Ensure final bounds still respected
    final_confidence = min(0.85, max(0.10, final_confidence))

    logger.debug(
        f"[Confidence] {agent_name}: strength={signal_strength:.2f}, "
        f"agreement={indicator_agreement:.2f}, quality={data_quality_score:.2f} "
        f"→ {final_confidence:.2f}"
    )

    # Future: Apply historical calibration when P3 is implemented
    if historical_calibration is not None:
        logger.info(f"[Confidence] Historical calibration not yet implemented")

    return final_confidence


def calculate_fundamentals_confidence(
    score: int,
    revenue_score: int,
    profitability_score: int,
    leverage_score: int,
    cash_flow_score: int,
    quality_report: QualityReport | None = None,
) -> float:
    """
    Calculate confidence for Fundamentals agent.

    Signal strength: Deviation from neutral point (57.5/100)
    Indicator agreement: Consistency among 4 sub-dimensions

    Args:
        score: Total fundamentals score (0-100)
        revenue_score: Revenue quality score (0-25)
        profitability_score: Profitability score (0-25)
        leverage_score: Leverage health score (0-25)
        cash_flow_score: Cash flow quality score (0-25)
        quality_report: Data quality report

    Returns:
        Confidence score (0.10-0.85)
    """
    # Signal strength: How far from neutral (57.5)
    neutral_point = 57.5
    signal_strength = abs(score - neutral_point) / neutral_point
    signal_strength = min(1.0, signal_strength)  # Cap at 1.0

    # Indicator agreement: Check if all 4 dimensions agree on direction
    # Neutral = 12.5/25, bullish > 15, bearish < 10
    scores = [revenue_score, profitability_score, leverage_score, cash_flow_score]
    bullish_dims = sum(1 for s in scores if s > 15)
    bearish_dims = sum(1 for s in scores if s < 10)
    neutral_dims = 4 - bullish_dims - bearish_dims

    # Perfect agreement = 1.0, total disagreement = 0.0
    if bullish_dims == 4 or bearish_dims == 4:
        indicator_agreement = 1.0
    elif bullish_dims == 3 or bearish_dims == 3:
        indicator_agreement = 0.75
    elif bullish_dims == 2 or bearish_dims == 2:
        indicator_agreement = 0.50
    else:
        indicator_agreement = 0.25

    return calculate_confidence(
        "fundamentals", signal_strength, indicator_agreement, quality_report
    )


def calculate_valuation_confidence(
    margin_of_safety: float | None,
    dcf_per_share: float | None,
    graham_number: float | None,
    current_price: float | None,
    quality_report: QualityReport | None = None,
) -> float:
    """
    Calculate confidence for Valuation agent.

    Signal strength: Absolute value of margin of safety
    Indicator agreement: DCF and Graham Number agreement

    Args:
        margin_of_safety: (intrinsic_value - price) / intrinsic_value
        dcf_per_share: DCF intrinsic value
        graham_number: Graham Number intrinsic value
        current_price: Current stock price
        quality_report: Data quality report

    Returns:
        Confidence score (0.10-0.85)
    """
    # Signal strength: Larger margin of safety = stronger signal
    if margin_of_safety is None:
        signal_strength = 0.10
    else:
        signal_strength = min(1.0, abs(margin_of_safety))

    # Indicator agreement: Do DCF and Graham agree on direction?
    if dcf_per_share and graham_number and current_price:
        dcf_signal = "bullish" if dcf_per_share > current_price else "bearish"
        graham_signal = "bullish" if graham_number > current_price else "bearish"
        indicator_agreement = 1.0 if dcf_signal == graham_signal else 0.3
    else:
        # Missing data = low agreement
        indicator_agreement = 0.3

    return calculate_confidence(
        "valuation", signal_strength, indicator_agreement, quality_report
    )


def calculate_buffett_confidence(
    roe_values: list[float],
    net_income_values: list[float],
    has_pricing_power: bool,
    moat_type: str,
    quality_report: QualityReport | None = None,
) -> float:
    """
    Calculate confidence for Warren Buffett agent.

    Signal strength: ROE consistency (code-based)
    Indicator agreement: Code prediction vs LLM judgment alignment

    Args:
        roe_values: Last 5 years ROE values
        net_income_values: Last 5 years net income values
        has_pricing_power: LLM judgment on pricing power
        moat_type: LLM judgment on moat type
        quality_report: Data quality report

    Returns:
        Confidence score (0.10-0.85)
    """
    # Signal strength: ROE consistency and level
    if len(roe_values) >= 3:
        avg_roe = sum(roe_values) / len(roe_values)
        # High average ROE = strong signal
        signal_strength = min(1.0, avg_roe / 25.0)  # 25% ROE = max strength
    else:
        signal_strength = 0.2

    # Indicator agreement: Check if code metrics support LLM moat judgment
    # High ROE + stable NI = should have moat
    has_high_roe = len(roe_values) > 0 and sum(roe_values) / len(roe_values) > 15
    ni_stable = (
        len(net_income_values) >= 3
        and all(ni > 0 for ni in net_income_values)
    )

    code_suggests_moat = has_high_roe and ni_stable
    llm_confirms_moat = moat_type not in ["无", "N/A", "无明显护城河"]

    if code_suggests_moat and llm_confirms_moat:
        indicator_agreement = 0.9
    elif not code_suggests_moat and not llm_confirms_moat:
        indicator_agreement = 0.8  # Both agree on weak moat
    else:
        indicator_agreement = 0.4  # Disagreement

    return calculate_confidence(
        "warren_buffett", signal_strength, indicator_agreement, quality_report
    )


def calculate_graham_confidence(
    standards_passed: int,
    standards_details: dict[str, bool],
    quality_report: QualityReport | None = None,
) -> float:
    """
    Calculate confidence for Ben Graham agent.

    Signal strength: Proportion of standards passed
    Indicator agreement: Consistency among standards

    Args:
        standards_passed: Number of standards passed (0-7)
        standards_details: Dict of individual standard results
        quality_report: Data quality report

    Returns:
        Confidence score (0.10-0.85)
    """
    # Signal strength: More standards passed = stronger signal
    signal_strength = standards_passed / 7.0

    # Indicator agreement: Check if standards agree on direction
    # Categorize standards: valuation-based vs financial-health-based
    valuation_standards = ["pe_ratio", "pb_ratio", "dividend_yield"]
    health_standards = ["debt_ratio", "current_ratio", "earning_stability", "earnings_growth"]

    val_pass = sum(1 for k in valuation_standards if standards_details.get(k, False))
    health_pass = sum(1 for k in health_standards if standards_details.get(k, False))

    # Agreement: Both categories show similar pass rates
    val_rate = val_pass / len(valuation_standards)
    health_rate = health_pass / len(health_standards)
    agreement_diff = abs(val_rate - health_rate)
    indicator_agreement = 1.0 - agreement_diff  # Lower diff = higher agreement

    return calculate_confidence(
        "ben_graham", signal_strength, indicator_agreement, quality_report
    )


def calculate_sentiment_confidence(
    sentiment_score: float,
    positive_count: int,
    negative_count: int,
    neutral_count: int,
    quality_report: QualityReport | None = None,
) -> float:
    """
    Calculate confidence for Sentiment agent.

    Signal strength: Extremity of positive vs negative ratio
    Indicator agreement: Consistency among news sources (future: source weighting)

    Args:
        sentiment_score: Overall sentiment (-1.0 to +1.0)
        positive_count: Number of positive news items
        negative_count: Number of negative news items
        neutral_count: Number of neutral news items
        quality_report: Data quality report

    Returns:
        Confidence score (0.10-0.85)
    """
    # Signal strength: How extreme is the sentiment?
    signal_strength = abs(sentiment_score)

    # Indicator agreement: How polarized vs mixed is the news?
    total = positive_count + negative_count + neutral_count
    if total == 0:
        indicator_agreement = 0.1
    else:
        # High agreement = most news in one category
        max_category = max(positive_count, negative_count, neutral_count)
        indicator_agreement = max_category / total

    return calculate_confidence(
        "sentiment", signal_strength, indicator_agreement, quality_report
    )


def calculate_contrarian_confidence(
    consensus_strength: float,
    mode: str,
    num_challenges: int,
    quality_report: QualityReport | None = None,
) -> float:
    """
    Calculate confidence for Contrarian agent.

    Signal strength: Strength of consensus being challenged
    Indicator agreement: Number and quality of challenges/questions

    Args:
        consensus_strength: Strength of detected consensus (0.0-1.0)
        mode: Contrarian mode (bear_case/bull_case/critical_questions)
        num_challenges: Number of challenges or questions raised
        quality_report: Data quality report

    Returns:
        Confidence score (0.10-0.85)
    """
    # Signal strength: Stronger consensus = more reliable contrarian analysis
    signal_strength = consensus_strength

    # Indicator agreement: More challenges = better analysis
    # Expect 3-5 challenges for bear/bull case, 3 questions for critical
    if mode in ["bear_case", "bull_case"]:
        expected = 4
    else:  # critical_questions
        expected = 3

    indicator_agreement = min(1.0, num_challenges / expected)

    return calculate_confidence(
        "contrarian", signal_strength, indicator_agreement, quality_report
    )
