"""Tests for data quality models."""

from datetime import date

from src.data.models import QualityFlag, QualityReport
from src.data.quality import _calculate_quality_score


def test_quality_flag_instantiation():
    """Test QualityFlag can be created with required fields."""
    flag = QualityFlag(
        flag="missing_revenue",
        field="revenue",
        detail="Revenue is None for Q4 2023",
        severity="critical"
    )
    assert flag.flag == "missing_revenue"
    assert flag.field == "revenue"
    assert flag.detail == "Revenue is None for Q4 2023"
    assert flag.severity == "critical"


def test_quality_report_instantiation():
    """Test QualityReport can be created with defaults."""
    report = QualityReport(
        ticker="AAPL",
        market="us",
        flags=[],
        overall_quality_score=0.95,
        data_completeness=0.90,
        stale_fields=[],
        records_checked={"income": 4, "balance": 4}
    )
    assert report.ticker == "AAPL"
    assert report.market == "us"
    assert isinstance(report.check_date, date)
    assert report.overall_quality_score == 0.95
    assert report.data_completeness == 0.90
    assert report.records_checked == {"income": 4, "balance": 4}


def test_quality_score_single_critical():
    """Single critical flag: 1.0 × 0.70 = 0.70"""
    flags = [
        QualityFlag(flag="test", field="f", detail="", severity="critical")
    ]
    score = _calculate_quality_score(flags)
    assert abs(score - 0.70) < 0.01


def test_quality_score_single_warning():
    """Single warning flag: 1.0 × 0.90 = 0.90"""
    flags = [
        QualityFlag(flag="test", field="f", detail="", severity="warning")
    ]
    score = _calculate_quality_score(flags)
    assert abs(score - 0.90) < 0.01


def test_quality_score_single_info():
    """Info flags don't affect score: 1.0 × 1.0 = 1.0"""
    flags = [
        QualityFlag(flag="test", field="f", detail="", severity="info")
    ]
    score = _calculate_quality_score(flags)
    assert abs(score - 1.0) < 0.01


def test_quality_score_multiple_critical():
    """Two critical flags: 1.0 × 0.70 × 0.70 = 0.49"""
    flags = [
        QualityFlag(flag="test1", field="f1", detail="", severity="critical"),
        QualityFlag(flag="test2", field="f2", detail="", severity="critical"),
    ]
    score = _calculate_quality_score(flags)
    assert abs(score - 0.49) < 0.01


def test_quality_score_mixed():
    """1 critical + 1 warning: 1.0 × 0.70 × 0.90 = 0.63"""
    flags = [
        QualityFlag(flag="test1", field="f1", detail="", severity="critical"),
        QualityFlag(flag="test2", field="f2", detail="", severity="warning"),
    ]
    score = _calculate_quality_score(flags)
    assert abs(score - 0.63) < 0.01


def test_quality_score_empty():
    """No flags: perfect score of 1.0"""
    flags = []
    score = _calculate_quality_score(flags)
    assert score == 1.0
