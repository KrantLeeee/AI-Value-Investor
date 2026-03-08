"""Tests for prediction tracking."""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import date
from unittest.mock import patch

from src.tracking.predictions import (
    save_prediction,
    update_prediction_outcome,
    get_all_predictions,
    calculate_agent_accuracy,
    calculate_all_agents_accuracy,
    suggest_weight_calibration,
    PREDICTIONS_DIR,
)
from src.data.models import AgentSignal


@pytest.fixture
def temp_predictions_dir(monkeypatch):
    """Create temporary predictions directory for testing."""
    temp_dir = Path(tempfile.mkdtemp())
    monkeypatch.setattr("src.tracking.predictions.PREDICTIONS_DIR", temp_dir)
    yield temp_dir
    shutil.rmtree(temp_dir)


def test_save_prediction(temp_predictions_dir):
    """Should save prediction to JSON file."""
    agent_signals = {
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

    file_path = save_prediction(
        ticker="600000.SH",
        market="SH",
        industry="banking",
        signal="bullish",
        confidence=0.75,
        current_price=10.0,
        agent_signals=agent_signals,
        target_price=12.0,
    )

    assert file_path.exists()

    with open(file_path, "r", encoding="utf-8") as f:
        prediction = json.load(f)

    assert prediction["ticker"] == "600000.SH"
    assert prediction["signal"] == "bullish"
    assert prediction["confidence"] == 0.75
    assert prediction["current_price"] == 10.0
    assert prediction["target_price"] == 12.0
    assert "fundamentals" in prediction["agent_signals"]
    assert "valuation" in prediction["agent_signals"]
    assert prediction["actual_outcome"] is None


def test_update_prediction_outcome(temp_predictions_dir):
    """Should update prediction with actual outcome."""
    # First save a prediction
    agent_signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Strong fundamentals",
        ),
    }

    today = date.today().isoformat()

    save_prediction(
        ticker="600000.SH",
        market="SH",
        industry="banking",
        signal="bullish",
        confidence=0.75,
        current_price=10.0,
        agent_signals=agent_signals,
    )

    # Update with outcome
    success = update_prediction_outcome(
        ticker="600000.SH",
        prediction_date=today,
        outcome_price=11.5,
    )

    assert success is True

    # Read updated prediction
    file_path = temp_predictions_dir / f"600000.SH_{today}.json"
    with open(file_path, "r", encoding="utf-8") as f:
        prediction = json.load(f)

    assert prediction["outcome_price"] == 11.5
    assert prediction["outcome_return"] == 0.15  # (11.5 - 10.0) / 10.0
    assert prediction["actual_outcome"] == "bullish"  # return > 10%
    assert "agents_accuracy" in prediction
    assert prediction["agents_accuracy"]["fundamentals"]["correct"] is True


def test_update_prediction_outcome_bearish(temp_predictions_dir):
    """Should correctly classify bearish outcome."""
    agent_signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Wrong prediction",
        ),
    }

    today = date.today().isoformat()

    save_prediction(
        ticker="600000.SH",
        market="SH",
        industry="banking",
        signal="bullish",
        confidence=0.75,
        current_price=10.0,
        agent_signals=agent_signals,
    )

    # Update with negative outcome
    update_prediction_outcome(
        ticker="600000.SH",
        prediction_date=today,
        outcome_price=8.5,
    )

    file_path = temp_predictions_dir / f"600000.SH_{today}.json"
    with open(file_path, "r", encoding="utf-8") as f:
        prediction = json.load(f)

    assert prediction["actual_outcome"] == "bearish"  # return < -10%
    assert prediction["agents_accuracy"]["fundamentals"]["correct"] is False


def test_update_prediction_outcome_neutral(temp_predictions_dir):
    """Should correctly classify neutral outcome."""
    agent_signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="neutral",
            confidence=0.50,
            reasoning="Neutral",
        ),
    }

    today = date.today().isoformat()

    save_prediction(
        ticker="600000.SH",
        market="SH",
        industry="banking",
        signal="neutral",
        confidence=0.50,
        current_price=10.0,
        agent_signals=agent_signals,
    )

    # Update with small movement
    update_prediction_outcome(
        ticker="600000.SH",
        prediction_date=today,
        outcome_price=10.5,
    )

    file_path = temp_predictions_dir / f"600000.SH_{today}.json"
    with open(file_path, "r", encoding="utf-8") as f:
        prediction = json.load(f)

    assert prediction["actual_outcome"] == "neutral"  # -10% < return < 10%
    assert prediction["agents_accuracy"]["fundamentals"]["correct"] is True


def test_update_prediction_outcome_file_not_found(temp_predictions_dir):
    """Should return False if prediction file not found."""
    success = update_prediction_outcome(
        ticker="999999.SH",
        prediction_date="2026-01-01",
        outcome_price=15.0,
    )

    assert success is False


def test_get_all_predictions(temp_predictions_dir):
    """Should retrieve all predictions."""
    # Save multiple predictions
    for i, ticker in enumerate(["600000.SH", "600519.SH", "601398.SH"]):
        agent_signals = {
            "fundamentals": AgentSignal(
                ticker=ticker,
                agent_name="fundamentals",
                signal="bullish",
                confidence=0.70,
                reasoning="Test",
            ),
        }

        save_prediction(
            ticker=ticker,
            market="SH",
            industry="banking" if i == 0 else "consumer",
            signal="bullish",
            confidence=0.75,
            current_price=10.0 + i,
            agent_signals=agent_signals,
        )

    predictions = get_all_predictions()
    assert len(predictions) == 3


def test_get_all_predictions_filter_by_industry(temp_predictions_dir):
    """Should filter predictions by industry."""
    # Save predictions in different industries
    for ticker, industry in [("600000.SH", "banking"), ("600519.SH", "consumer")]:
        agent_signals = {
            "fundamentals": AgentSignal(
                ticker=ticker,
                agent_name="fundamentals",
                signal="bullish",
                confidence=0.70,
                reasoning="Test",
            ),
        }

        save_prediction(
            ticker=ticker,
            market="SH",
            industry=industry,
            signal="bullish",
            confidence=0.75,
            current_price=10.0,
            agent_signals=agent_signals,
        )

    banking_predictions = get_all_predictions(industry="banking")
    assert len(banking_predictions) == 1
    assert banking_predictions[0]["ticker"] == "600000.SH"


def test_calculate_agent_accuracy(temp_predictions_dir):
    """Should calculate agent accuracy correctly."""
    today = date.today().isoformat()

    # Create predictions with outcomes
    for i, (ticker, outcome_price) in enumerate([
        ("600000.SH", 11.0),  # Bullish prediction, bullish outcome (correct)
        ("600519.SH", 9.0),   # Bullish prediction, neutral outcome (incorrect)
        ("601398.SH", 12.0),  # Bullish prediction, bullish outcome (correct)
    ]):
        agent_signals = {
            "fundamentals": AgentSignal(
                ticker=ticker,
                agent_name="fundamentals",
                signal="bullish",
                confidence=0.70,
                reasoning="Test",
            ),
        }

        save_prediction(
            ticker=ticker,
            market="SH",
            industry="banking",
            signal="bullish",
            confidence=0.75,
            current_price=10.0,
            agent_signals=agent_signals,
        )

        # Update outcome
        update_prediction_outcome(ticker, today, outcome_price)

    # Calculate accuracy
    accuracy = calculate_agent_accuracy("fundamentals", "banking")

    assert accuracy["total_predictions"] == 3
    assert accuracy["correct_predictions"] == 2  # 2 out of 3 correct
    assert accuracy["accuracy"] == 2/3
    assert accuracy["avg_confidence"] == 0.70


def test_calculate_agent_accuracy_no_predictions(temp_predictions_dir):
    """Should handle case with no predictions."""
    accuracy = calculate_agent_accuracy("fundamentals", "banking")

    assert accuracy["total_predictions"] == 0
    assert accuracy["accuracy"] is None


def test_calculate_all_agents_accuracy(temp_predictions_dir):
    """Should calculate accuracy for all agents."""
    today = date.today().isoformat()

    agent_signals = {
        "fundamentals": AgentSignal(
            ticker="600000.SH",
            agent_name="fundamentals",
            signal="bullish",
            confidence=0.70,
            reasoning="Test",
        ),
        "valuation": AgentSignal(
            ticker="600000.SH",
            agent_name="valuation",
            signal="bearish",
            confidence=0.60,
            reasoning="Test",
        ),
    }

    save_prediction(
        ticker="600000.SH",
        market="SH",
        industry="banking",
        signal="bullish",
        confidence=0.75,
        current_price=10.0,
        agent_signals=agent_signals,
    )

    update_prediction_outcome("600000.SH", today, 11.0)  # Bullish outcome

    all_accuracy = calculate_all_agents_accuracy("banking")

    assert "fundamentals" in all_accuracy
    assert "valuation" in all_accuracy
    assert all_accuracy["fundamentals"]["accuracy"] == 1.0  # Correct
    assert all_accuracy["valuation"]["accuracy"] == 0.0     # Incorrect


@patch("src.agents.industry_classifier.get_agent_weights")
def test_suggest_weight_calibration_not_ready(mock_get_weights, temp_predictions_dir):
    """Should indicate not ready if < 20 predictions."""
    mock_get_weights.return_value = {
        "fundamentals": 0.25,
        "valuation": 0.25,
        "warren_buffett": 0.20,
        "ben_graham": 0.15,
        "sentiment": 0.15,
    }

    result = suggest_weight_calibration("banking")

    assert result["ready_for_calibration"] is False
    assert result["prediction_count"] < 20


@patch("src.agents.industry_classifier.get_agent_weights")
def test_suggest_weight_calibration_ready(mock_get_weights, temp_predictions_dir):
    """Should suggest weights if ≥20 predictions."""
    mock_get_weights.return_value = {
        "fundamentals": 0.25,
        "valuation": 0.25,
        "warren_buffett": 0.20,
        "ben_graham": 0.15,
        "sentiment": 0.15,
        "contrarian": 0.0,
    }

    today = date.today().isoformat()

    # Create 20 predictions
    for i in range(20):
        agent_signals = {
            "fundamentals": AgentSignal(
                ticker=f"60000{i}.SH",
                agent_name="fundamentals",
                signal="bullish",
                confidence=0.70,
                reasoning="Test",
            ),
            "valuation": AgentSignal(
                ticker=f"60000{i}.SH",
                agent_name="valuation",
                signal="bullish",
                confidence=0.60,
                reasoning="Test",
            ),
        }

        save_prediction(
            ticker=f"60000{i}.SH",
            market="SH",
            industry="banking",
            signal="bullish",
            confidence=0.75,
            current_price=10.0,
            agent_signals=agent_signals,
        )

        # All correct outcomes
        update_prediction_outcome(f"60000{i}.SH", today, 11.0)

    result = suggest_weight_calibration("banking")

    assert result["ready_for_calibration"] is True
    assert result["prediction_count"] == 20
    assert "suggested_weights" in result
    # Fundamentals and valuation both 100% accurate, should split 50/50
    assert result["suggested_weights"]["fundamentals"] == 0.5
    assert result["suggested_weights"]["valuation"] == 0.5
