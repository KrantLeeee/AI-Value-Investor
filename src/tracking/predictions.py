"""Prediction Tracking Module.

Implements PROJECT_ROADMAP.md P3-⑨:
- Store predictions in JSON format
- Track actual outcomes
- Calculate historical accuracy per agent
- Enable weight calibration (requires ≥20 predictions/industry)

Prediction Format:
{
    "ticker": "600000.SH",
    "market": "SH",
    "industry": "banking",
    "prediction_date": "2026-03-08",
    "signal": "bullish",
    "confidence": 0.75,
    "target_price": 12.50,
    "current_price": 10.00,
    "agent_signals": {...},
    "valuation_date": "2026-03-08",  # When to check outcome
    "actual_outcome": null,  # Updated later
    "outcome_price": null,
    "outcome_return": null,
    "agents_accuracy": null,
}
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from src.data.models import AgentSignal
from src.utils.config import get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)

PREDICTIONS_DIR = get_project_root() / "output" / "predictions"


def _ensure_predictions_dir():
    """Ensure predictions directory exists."""
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)


def _get_prediction_file(ticker: str, prediction_date: str) -> Path:
    """
    Get prediction file path for a ticker and date.

    Format: output/predictions/{ticker}_{YYYY-MM-DD}.json

    Args:
        ticker: Stock ticker
        prediction_date: Prediction date (YYYY-MM-DD)

    Returns:
        Path to prediction file
    """
    _ensure_predictions_dir()
    filename = f"{ticker}_{prediction_date}.json"
    return PREDICTIONS_DIR / filename


def save_prediction(
    ticker: str,
    market: str,
    industry: str,
    signal: str,
    confidence: float,
    current_price: float,
    agent_signals: dict[str, AgentSignal],
    target_price: Optional[float] = None,
    valuation_horizon_months: int = 12,
) -> Path:
    """
    Save prediction to JSON file.

    Args:
        ticker: Stock ticker
        market: Market code
        industry: Industry classification
        signal: Final aggregated signal
        confidence: Final confidence
        current_price: Current stock price
        agent_signals: Individual agent signals
        target_price: Optional target price
        valuation_horizon_months: Months until outcome check

    Returns:
        Path to saved prediction file
    """
    prediction_date = date.today().isoformat()
    valuation_date = (date.today() + timedelta(days=valuation_horizon_months * 30)).isoformat()

    # Convert AgentSignal objects to dicts
    agent_signals_dict = {}
    for agent_name, signal_obj in agent_signals.items():
        if isinstance(signal_obj, AgentSignal):
            agent_signals_dict[agent_name] = {
                "signal": signal_obj.signal,
                "confidence": signal_obj.confidence,
                "reasoning": signal_obj.reasoning[:200] if signal_obj.reasoning else None,  # Truncate
            }
        else:
            agent_signals_dict[agent_name] = signal_obj

    prediction = {
        "ticker": ticker,
        "market": market,
        "industry": industry,
        "prediction_date": prediction_date,
        "signal": signal,
        "confidence": confidence,
        "target_price": target_price,
        "current_price": current_price,
        "agent_signals": agent_signals_dict,
        "valuation_date": valuation_date,
        "valuation_horizon_months": valuation_horizon_months,
        # Outcome fields (to be filled later)
        "actual_outcome": None,
        "outcome_price": None,
        "outcome_return": None,
        "agents_accuracy": None,
    }

    file_path = _get_prediction_file(ticker, prediction_date)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(prediction, f, ensure_ascii=False, indent=2)

    logger.info(f"[Tracking] Saved prediction: {file_path}")
    return file_path


def update_prediction_outcome(
    ticker: str,
    prediction_date: str,
    outcome_price: float,
) -> bool:
    """
    Update prediction with actual outcome.

    Args:
        ticker: Stock ticker
        prediction_date: Original prediction date (YYYY-MM-DD)
        outcome_price: Actual stock price at outcome date

    Returns:
        True if updated successfully, False otherwise
    """
    file_path = _get_prediction_file(ticker, prediction_date)

    if not file_path.exists():
        logger.error(f"[Tracking] Prediction file not found: {file_path}")
        return False

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            prediction = json.load(f)

        current_price = prediction["current_price"]
        predicted_signal = prediction["signal"]

        # Calculate return
        actual_return = (outcome_price - current_price) / current_price

        # Determine actual outcome
        # Use >= and <= to include boundary cases
        if actual_return >= 0.10:
            actual_outcome = "bullish"
        elif actual_return <= -0.10:
            actual_outcome = "bearish"
        else:
            actual_outcome = "neutral"

        # Calculate agent-level accuracy
        agents_accuracy = {}
        for agent_name, signal_data in prediction["agent_signals"].items():
            agent_signal = signal_data["signal"]
            agent_conf = signal_data["confidence"]

            # Binary accuracy: correct if signal matches outcome
            correct = (agent_signal == actual_outcome)
            agents_accuracy[agent_name] = {
                "correct": correct,
                "confidence": agent_conf,
            }

        # Update prediction
        prediction.update({
            "actual_outcome": actual_outcome,
            "outcome_price": outcome_price,
            "outcome_return": actual_return,
            "outcome_date": date.today().isoformat(),
            "agents_accuracy": agents_accuracy,
        })

        # Save updated prediction
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(prediction, f, ensure_ascii=False, indent=2)

        logger.info(
            f"[Tracking] Updated outcome for {ticker} ({prediction_date}): "
            f"predicted={predicted_signal}, actual={actual_outcome}, "
            f"return={actual_return*100:.1f}%"
        )
        return True

    except Exception as e:
        logger.error(f"[Tracking] Failed to update prediction: {e}")
        return False


def get_all_predictions(industry: Optional[str] = None) -> list[dict]:
    """
    Get all predictions, optionally filtered by industry.

    Args:
        industry: Optional industry filter

    Returns:
        List of prediction dicts
    """
    _ensure_predictions_dir()

    predictions = []
    for file_path in PREDICTIONS_DIR.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                prediction = json.load(f)

            if industry is None or prediction.get("industry") == industry:
                predictions.append(prediction)

        except Exception as e:
            logger.warning(f"[Tracking] Failed to load {file_path}: {e}")

    return predictions


def calculate_agent_accuracy(
    agent_name: str,
    industry: Optional[str] = None,
) -> dict:
    """
    Calculate historical accuracy for an agent.

    Args:
        agent_name: Agent name (e.g., "fundamentals", "valuation")
        industry: Optional industry filter

    Returns:
        Dictionary with:
        - total_predictions: Total predictions made
        - correct_predictions: Number of correct predictions
        - accuracy: Accuracy percentage
        - avg_confidence: Average confidence when correct
        - calibration: Confidence vs accuracy gap
    """
    predictions = get_all_predictions(industry)

    # Filter for predictions with outcomes
    completed = [p for p in predictions if p.get("actual_outcome") is not None]

    if not completed:
        return {
            "agent_name": agent_name,
            "industry": industry or "all",
            "total_predictions": 0,
            "correct_predictions": 0,
            "accuracy": None,
            "avg_confidence": None,
            "calibration": None,
        }

    # Extract agent-specific accuracy
    correct_count = 0
    confidence_sum = 0.0

    for pred in completed:
        agent_acc = pred.get("agents_accuracy", {}).get(agent_name)
        if agent_acc:
            if agent_acc["correct"]:
                correct_count += 1
                confidence_sum += agent_acc["confidence"]

    total = len(completed)
    accuracy = correct_count / total if total > 0 else 0.0
    avg_confidence = confidence_sum / correct_count if correct_count > 0 else 0.0

    # Calibration: how well confidence matches accuracy
    # Ideal: confidence ≈ accuracy (well-calibrated)
    # If confidence > accuracy: overconfident
    # If confidence < accuracy: underconfident
    calibration = avg_confidence - accuracy if avg_confidence else None

    return {
        "agent_name": agent_name,
        "industry": industry or "all",
        "total_predictions": total,
        "correct_predictions": correct_count,
        "accuracy": accuracy,
        "avg_confidence": avg_confidence,
        "calibration": calibration,
    }


def calculate_all_agents_accuracy(industry: Optional[str] = None) -> dict[str, dict]:
    """
    Calculate accuracy for all agents.

    Args:
        industry: Optional industry filter

    Returns:
        Dictionary mapping agent_name -> accuracy stats
    """
    agent_names = [
        "fundamentals",
        "valuation",
        "warren_buffett",
        "ben_graham",
        "sentiment",
        "contrarian",
    ]

    results = {}
    for agent_name in agent_names:
        results[agent_name] = calculate_agent_accuracy(agent_name, industry)

    return results


def suggest_weight_calibration(industry: str) -> dict:
    """
    Suggest weight calibration based on historical accuracy.

    NOTE: Requires ≥20 predictions per industry for statistical validity.

    Args:
        industry: Industry classification

    Returns:
        Dictionary with:
        - ready_for_calibration: Boolean
        - prediction_count: Number of predictions
        - suggested_weights: New weights if ready
        - current_weights: Current weights
        - note: Explanation
    """
    from src.agents.industry_classifier import get_agent_weights

    predictions = get_all_predictions(industry)
    completed = [p for p in predictions if p.get("actual_outcome") is not None]

    current_weights = get_agent_weights(industry)

    if len(completed) < 20:
        return {
            "ready_for_calibration": False,
            "prediction_count": len(completed),
            "required_count": 20,
            "suggested_weights": None,
            "current_weights": current_weights,
            "note": f"需要至少20条历史预测，当前仅{len(completed)}条",
        }

    # Calculate accuracy for each agent
    accuracies = calculate_all_agents_accuracy(industry)

    # Weight by accuracy (simple proportional allocation)
    total_accuracy = sum(acc["accuracy"] for acc in accuracies.values() if acc["accuracy"])

    if total_accuracy == 0:
        return {
            "ready_for_calibration": False,
            "prediction_count": len(completed),
            "suggested_weights": None,
            "current_weights": current_weights,
            "note": "所有Agent准确率为0，无法校准权重",
        }

    suggested_weights = {}
    for agent_name, acc in accuracies.items():
        if acc["accuracy"]:
            suggested_weights[agent_name] = acc["accuracy"] / total_accuracy
        else:
            suggested_weights[agent_name] = 0.0

    return {
        "ready_for_calibration": True,
        "prediction_count": len(completed),
        "suggested_weights": suggested_weights,
        "current_weights": current_weights,
        "accuracy_data": accuracies,
        "note": "基于历史准确率的权重建议（仅供参考，需结合业务判断）",
    }
