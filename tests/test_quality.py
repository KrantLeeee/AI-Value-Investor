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


def test_financial_freshness_all_current():
    """All financial statements within 120 days - no flags"""
    from datetime import timedelta

    from src.data.models import BalanceSheet, CashFlow, IncomeStatement
    from src.data.quality import check_financial_freshness

    recent = date.today() - timedelta(days=100)

    income = [IncomeStatement(
        ticker="TEST", period_end_date=recent, period_type="quarterly",
        revenue=1000.0, net_income=100.0, source="test"
    )]
    balance = [BalanceSheet(
        ticker="TEST", period_end_date=recent, period_type="quarterly",
        total_assets=5000.0, total_liabilities=3000.0, total_equity=2000.0, source="test"
    )]
    cash_flow = [CashFlow(
        ticker="TEST", period_end_date=recent, period_type="quarterly",
        operating_cash_flow=200.0, source="test"
    )]

    flags = check_financial_freshness(income, balance, cash_flow)
    assert len(flags) == 0


def test_financial_freshness_critical():
    """Financial data > 180 days old - critical flag"""
    from datetime import timedelta

    from src.data.models import IncomeStatement
    from src.data.quality import check_financial_freshness

    stale = date.today() - timedelta(days=500)

    income = [IncomeStatement(
        ticker="TEST", period_end_date=stale, period_type="quarterly",
        revenue=1000.0, net_income=100.0, source="test"
    )]

    flags = check_financial_freshness(income, [], [])
    assert len(flags) == 1
    assert flags[0].flag == "stale_financials"
    assert flags[0].severity == "critical"
    assert "500 days old" in flags[0].detail


def test_financial_freshness_warning():
    """Financial data 120-180 days old - warning flag"""
    from datetime import timedelta

    from src.data.models import BalanceSheet
    from src.data.quality import check_financial_freshness

    aging = date.today() - timedelta(days=150)

    balance = [BalanceSheet(
        ticker="TEST", period_end_date=aging, period_type="quarterly",
        total_assets=5000.0, total_liabilities=3000.0, total_equity=2000.0, source="test"
    )]

    flags = check_financial_freshness([], balance, [])
    assert len(flags) == 1
    assert flags[0].flag == "aging_financials"
    assert flags[0].severity == "warning"
    assert "150 days old" in flags[0].detail


def test_financial_freshness_empty():
    """No financial data - critical flag for missing data"""
    from src.data.quality import check_financial_freshness

    flags = check_financial_freshness([], [], [])
    assert len(flags) == 1
    assert flags[0].flag == "missing_financials"
    assert flags[0].severity == "critical"


def test_price_freshness_current():
    """Price data within 5 trading days - no flags"""
    from datetime import timedelta

    from src.data.models import DailyPrice
    from src.data.quality import check_price_freshness

    recent = date.today() - timedelta(days=3)

    prices = [DailyPrice(
        ticker="TEST", market="a_share", date=recent,
        close=10.0, open=9.5, high=10.5, low=9.0, volume=1000000, source="test"
    )]

    flags = check_price_freshness(prices)
    assert len(flags) == 0


def test_price_freshness_warning():
    """Price data 5-10 trading days old - warning flag"""
    from datetime import timedelta

    from src.data.models import DailyPrice
    from src.data.quality import check_price_freshness

    aging = date.today() - timedelta(days=7)

    prices = [DailyPrice(
        ticker="TEST", market="a_share", date=aging,
        close=10.0, open=9.5, high=10.5, low=9.0, volume=1000000, source="test"
    )]

    flags = check_price_freshness(prices)
    assert len(flags) == 1
    assert flags[0].flag == "aging_prices"
    assert flags[0].severity == "warning"
    assert "7 days old" in flags[0].detail


def test_price_freshness_critical():
    """Price data > 10 trading days old - critical flag"""
    from datetime import timedelta

    from src.data.models import DailyPrice
    from src.data.quality import check_price_freshness

    stale = date.today() - timedelta(days=15)

    prices = [DailyPrice(
        ticker="TEST", market="a_share", date=stale,
        close=10.0, open=9.5, high=10.5, low=9.0, volume=1000000, source="test"
    )]

    flags = check_price_freshness(prices)
    assert len(flags) == 1
    assert flags[0].flag == "stale_prices"
    assert flags[0].severity == "critical"
    assert "15 days old" in flags[0].detail


def test_price_freshness_empty():
    """No price data - critical flag for missing data"""
    from src.data.quality import check_price_freshness

    flags = check_price_freshness([])
    assert len(flags) == 1
    assert flags[0].flag == "missing_prices"
    assert flags[0].severity == "critical"


def test_negative_equity_positive():
    """Positive equity - no flags"""
    from src.data.models import BalanceSheet
    from src.data.quality import check_negative_equity

    balance = [BalanceSheet(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        period_type="quarterly", total_assets=5000.0, total_liabilities=3000.0,
        total_equity=2000.0, source="test"
    )]

    flags = check_negative_equity(balance)
    assert len(flags) == 0


def test_negative_equity_negative():
    """Negative equity - critical flag"""
    from src.data.models import BalanceSheet
    from src.data.quality import check_negative_equity

    balance = [BalanceSheet(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        period_type="quarterly", total_assets=3000.0, total_liabilities=5000.0,
        total_equity=-2000.0, source="test"
    )]

    flags = check_negative_equity(balance)
    assert len(flags) == 1
    assert flags[0].flag == "negative_equity"
    assert flags[0].field == "total_equity"
    assert flags[0].severity == "critical"
    assert "-2000.0" in flags[0].detail


def test_negative_equity_zero():
    """Zero equity - warning flag (bankruptcy risk)"""
    from src.data.models import BalanceSheet
    from src.data.quality import check_negative_equity

    balance = [BalanceSheet(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        period_type="quarterly", total_assets=5000.0, total_liabilities=5000.0,
        total_equity=0.0, source="test"
    )]

    flags = check_negative_equity(balance)
    assert len(flags) == 1
    assert flags[0].flag == "zero_equity"
    assert flags[0].severity == "warning"


def test_negative_equity_empty():
    """No balance sheet data - no flags (handled by freshness check)"""
    from src.data.quality import check_negative_equity

    flags = check_negative_equity([])
    assert len(flags) == 0


def test_negative_equity_none():
    """None equity - no flags (missing data doesn't trigger negative equity)"""
    from src.data.models import BalanceSheet
    from src.data.quality import check_negative_equity

    balance = [BalanceSheet(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        period_type="quarterly", total_assets=5000.0, total_liabilities=3000.0,
        total_equity=None, source="test"
    )]

    flags = check_negative_equity(balance)
    assert len(flags) == 0


def test_magnitude_normal():
    """Revenue > net_income - no flags"""
    from src.data.models import IncomeStatement
    from src.data.quality import check_magnitude

    income = [IncomeStatement(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        revenue=1e10, net_income=5e8, period_type="quarterly", source="test"  # 100亿 revenue, 5亿 profit
    )]

    flags = check_magnitude(income)
    assert len(flags) == 0


def test_magnitude_error():
    """Net income > revenue - critical flag (unit conversion error)"""
    from src.data.models import IncomeStatement
    from src.data.quality import check_magnitude

    income = [IncomeStatement(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        revenue=5e8, net_income=1e10, period_type="quarterly", source="test"  # Revenue 5亿, profit 100亿 (错误!)
    )]

    flags = check_magnitude(income)
    assert len(flags) == 1
    assert flags[0].flag == "magnitude_error"
    assert flags[0].severity == "critical"
    assert "5.00亿" in flags[0].detail
    assert "100.00亿" in flags[0].detail


def test_magnitude_none_revenue():
    """None revenue - no flags"""
    from src.data.models import IncomeStatement
    from src.data.quality import check_magnitude

    income = [IncomeStatement(
        ticker="TEST", market="a_share", period_end_date=date.today(),
        revenue=None, net_income=1e10, period_type="quarterly", source="test"
    )]

    flags = check_magnitude(income)
    assert len(flags) == 0


def test_magnitude_empty():
    """No income data - no flags"""
    from src.data.quality import check_magnitude

    flags = check_magnitude([])
    assert len(flags) == 0


# ── Rule 3: Revenue/Profit Anomaly Tests ──────────────────────────────────


def test_revenue_anomaly_normal():
    """Normal YoY change should not trigger flag"""
    from datetime import timedelta

    from src.data.models import IncomeStatement
    from src.data.quality import check_revenue_profit_anomaly

    income = [
        IncomeStatement(
            ticker="TEST",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            revenue=1e10,
            net_income=5e8,
            source="test"
        ),
        IncomeStatement(
            ticker="TEST",
            period_end_date=date(2023, 12, 31),
            period_type="annual",
            revenue=9e9,  # +11% YoY
            net_income=4.5e8,
            source="test"
        ),
    ]

    flags = check_revenue_profit_anomaly(income)
    assert len(flags) == 0


def test_revenue_anomaly_flagged():
    """YoY change > 80% with absolute > 500M should flag"""
    from src.data.models import IncomeStatement
    from src.data.quality import check_revenue_profit_anomaly

    income = [
        IncomeStatement(
            ticker="TEST",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            revenue=2e10,
            net_income=1e9,
            source="test"
        ),
        IncomeStatement(
            ticker="TEST",
            period_end_date=date(2023, 12, 31),
            period_type="annual",
            revenue=1e10,  # +100% YoY, Δ=10亿
            net_income=5e8,
            source="test"
        ),
    ]

    flags = check_revenue_profit_anomaly(income)
    assert len(flags) >= 1
    assert any(f.flag == "revenue_anomaly" for f in flags)
    assert all(f.severity == "warning" for f in flags)


# ── Rule 4: NI vs OCF Divergence Tests ────────────────────────────────────


def test_ni_ocf_divergence_ok():
    """Both positive or both negative should not flag"""
    from src.data.models import CashFlow, IncomeStatement
    from src.data.quality import check_ni_ocf_divergence

    income = [
        IncomeStatement(ticker="TEST", period_end_date=date(2024, 12, 31),
                       period_type="annual", net_income=1e9, source="test"),
        IncomeStatement(ticker="TEST", period_end_date=date(2023, 12, 31),
                       period_type="annual", net_income=9e8, source="test"),
    ]
    cashflow = [
        CashFlow(ticker="TEST", period_end_date=date(2024, 12, 31),
                period_type="annual", operating_cash_flow=8e8, source="test"),
        CashFlow(ticker="TEST", period_end_date=date(2023, 12, 31),
                period_type="annual", operating_cash_flow=7e8, source="test"),
    ]

    flags = check_ni_ocf_divergence(income, cashflow)
    assert len(flags) == 0


def test_ni_ocf_divergence_flagged():
    """NI > 0 but OCF < 0 for 2 consecutive years should flag"""
    from src.data.models import CashFlow, IncomeStatement
    from src.data.quality import check_ni_ocf_divergence

    income = [
        IncomeStatement(ticker="TEST", period_end_date=date(2024, 12, 31),
                       period_type="annual", net_income=1e9, source="test"),
        IncomeStatement(ticker="TEST", period_end_date=date(2023, 12, 31),
                       period_type="annual", net_income=9e8, source="test"),
    ]
    cashflow = [
        CashFlow(ticker="TEST", period_end_date=date(2024, 12, 31),
                period_type="annual", operating_cash_flow=-5e8, source="test"),
        CashFlow(ticker="TEST", period_end_date=date(2023, 12, 31),
                period_type="annual", operating_cash_flow=-3e8, source="test"),
    ]

    flags = check_ni_ocf_divergence(income, cashflow)
    assert len(flags) == 1
    assert flags[0].flag == "ni_ocf_divergence"
    assert flags[0].severity == "warning"


# ── Rule 6: Missing Core Fields Tests ─────────────────────────────────────


def test_missing_fields_none():
    """All core fields present should not flag"""
    from datetime import timedelta

    from src.data.models import BalanceSheet, CashFlow, IncomeStatement
    from src.data.quality import check_missing_fields

    raw_data = {
        'income': [IncomeStatement(
            ticker="TEST", period_end_date=date.today() - timedelta(days=180),
            period_type="annual", revenue=1e10, net_income=5e8,
            eps=0.5, shares_outstanding=1e9, source="test"
        )],
        'balance': [BalanceSheet(
            ticker="TEST", period_end_date=date.today() - timedelta(days=180),
            period_type="annual", total_assets=5e10, total_equity=2e10,
            total_debt=1e10, current_assets=2e10, current_liabilities=5e9,
            source="test"
        )],
        'cashflow': [CashFlow(
            ticker="TEST", period_end_date=date.today() - timedelta(days=180),
            period_type="annual", operating_cash_flow=8e8,
            free_cash_flow=3e8, source="test"
        )],
    }

    flags = check_missing_fields(raw_data)
    assert len(flags) == 0


def test_missing_fields_critical():
    """Missing >= 4 core fields should trigger critical"""
    from datetime import timedelta

    from src.data.models import IncomeStatement
    from src.data.quality import check_missing_fields

    raw_data = {
        'income': [IncomeStatement(
            ticker="TEST", period_end_date=date.today() - timedelta(days=180),
            period_type="annual", revenue=1e10,  # Missing: net_income, eps, shares
            source="test"
        )],
        'balance': [],  # Missing all balance fields
        'cashflow': [],  # Missing all cashflow fields
    }

    flags = check_missing_fields(raw_data)
    assert len(flags) == 1
    assert flags[0].severity == "critical"


# ── Rule 7: FCF Approximation Tests ───────────────────────────────────────


def test_fcf_approximation_flagged():
    """FCF present but capex missing should flag as approximation"""
    from datetime import timedelta

    from src.data.models import CashFlow
    from src.data.quality import check_fcf_approximation

    cashflow = [CashFlow(
        ticker="TEST", period_end_date=date.today() - timedelta(days=180),
        period_type="annual", operating_cash_flow=8e8,
        free_cash_flow=3e8, capital_expenditure=None, source="test"
    )]

    flags = check_fcf_approximation(cashflow)
    assert len(flags) == 1
    assert flags[0].flag == "fcf_approximation"
    assert flags[0].severity == "info"


# ── Rule 8: EPS Consistency Tests ─────────────────────────────────────────


def test_eps_consistency_ok():
    """EPS matching calculated value should not flag"""
    from datetime import timedelta

    from src.data.models import IncomeStatement
    from src.data.quality import check_eps_consistency

    income = [IncomeStatement(
        ticker="TEST", period_end_date=date.today() - timedelta(days=180),
        period_type="annual", revenue=1e10, net_income=1e9,
        eps=1.0, shares_outstanding=1e9, source="test"
    )]

    flags = check_eps_consistency(income)
    assert len(flags) == 0


def test_eps_consistency_flagged():
    """EPS differing > 10% from calculated should flag"""
    from datetime import timedelta

    from src.data.models import IncomeStatement
    from src.data.quality import check_eps_consistency

    income = [IncomeStatement(
        ticker="TEST", period_end_date=date.today() - timedelta(days=180),
        period_type="annual", revenue=1e10, net_income=1e9,
        eps=1.5, shares_outstanding=1e9, source="test"  # Calculated: 1.0
    )]

    flags = check_eps_consistency(income)
    assert len(flags) == 1
    assert flags[0].flag == "eps_inconsistency"
    assert flags[0].severity == "warning"


# ── Rule 9: Duplicate Periods Tests ───────────────────────────────────────


def test_duplicate_periods_none():
    """Unique periods should not flag"""
    from src.data.models import IncomeStatement
    from src.data.quality import check_duplicate_periods

    raw_data = {
        'income': [
            IncomeStatement(ticker="TEST", period_end_date=date(2024, 12, 31),
                          period_type="annual", revenue=1e10, source="test"),
            IncomeStatement(ticker="TEST", period_end_date=date(2023, 12, 31),
                          period_type="annual", revenue=9e9, source="test"),
        ]
    }

    flags = check_duplicate_periods(raw_data)
    assert len(flags) == 0


# ── Rule 11: Source Changes Tests ─────────────────────────────────────────


def test_source_changes_flagged():
    """Different sources across periods should flag"""
    from src.data.models import IncomeStatement
    from src.data.quality import check_source_changes

    raw_data = {
        'income': [
            IncomeStatement(ticker="TEST", period_end_date=date(2024, 12, 31),
                          period_type="annual", revenue=1e10, source="akshare"),
            IncomeStatement(ticker="TEST", period_end_date=date(2023, 12, 31),
                          period_type="annual", revenue=9e9, source="baostock"),
        ]
    }

    flags = check_source_changes(raw_data)
    assert len(flags) >= 1
    assert any(f.flag == "source_change" for f in flags)
    assert all(f.severity == "info" for f in flags)
