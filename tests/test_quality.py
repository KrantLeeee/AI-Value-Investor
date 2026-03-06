"""Tests for data quality models."""

from datetime import date

from src.data.models import QualityFlag, QualityReport


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
