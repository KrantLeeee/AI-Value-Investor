"""Data quality validation layer (P0-①).

Provides infrastructure for validating data completeness, freshness,
and logical consistency across all data sources.
"""

from datetime import date

from src.data.models import BalanceSheet, CashFlow, DailyPrice, IncomeStatement, QualityFlag


def _calculate_quality_score(flags: list[QualityFlag]) -> float:
    """Calculate overall quality score using multiplicative risk model.

    Each flag reduces the score multiplicatively:
    - critical: × 0.70
    - warning:  × 0.90
    - info:     × 1.0 (no impact)

    This means multiple risks compound independently:
    - 1 critical = 0.70
    - 2 critical = 0.49
    - 1 critical + 1 warning = 0.63

    Args:
        flags: List of quality issues detected

    Returns:
        Quality score in [0.0, 1.0], where 1.0 = perfect quality
    """
    score = 1.0

    for flag in flags:
        if flag.severity == "critical":
            score *= 0.70
        elif flag.severity == "warning":
            score *= 0.90
        # info severity doesn't affect score (× 1.0)

    return score


def check_financial_freshness(
    income_statements: list[IncomeStatement],
    balance_sheets: list[BalanceSheet],
    cash_flow_statements: list[CashFlow],
) -> list[QualityFlag]:
    """Check if financial data is recent enough for reliable analysis.

    Rule: Financial statements should be < 120 days old for quarterly reporting.

    Severity:
    - critical: > 180 days old or completely missing
    - warning: 120-180 days old

    Args:
        income_statements: Recent income statements
        balance_sheets: Recent balance sheets
        cash_flow_statements: Recent cash flow statements

    Returns:
        List of quality flags (empty if all data is current)
    """
    flags = []
    today = date.today()

    # Collect all financial statement dates
    all_dates = []
    if income_statements:
        all_dates.extend([stmt.period_end_date for stmt in income_statements])
    if balance_sheets:
        all_dates.extend([stmt.period_end_date for stmt in balance_sheets])
    if cash_flow_statements:
        all_dates.extend([stmt.period_end_date for stmt in cash_flow_statements])

    # Check for missing data
    if not all_dates:
        flags.append(QualityFlag(
            flag="missing_financials",
            field="financial_statements",
            detail="No financial statements available",
            severity="critical"
        ))
        return flags

    # Find most recent date
    most_recent = max(all_dates)
    days_old = (today - most_recent).days

    # Check freshness thresholds
    if days_old > 180:
        flags.append(QualityFlag(
            flag="stale_financials",
            field="financial_statements",
            detail=f"Most recent financial data is {days_old} days old (> 180 day threshold)",
            severity="critical"
        ))
    elif days_old > 120:
        flags.append(QualityFlag(
            flag="aging_financials",
            field="financial_statements",
            detail=f"Most recent financial data is {days_old} days old (> 120 day threshold)",
            severity="warning"
        ))

    return flags


def check_price_freshness(
    prices: list[DailyPrice],
) -> list[QualityFlag]:
    """Check if price data is recent enough for reliable analysis.

    Rule: Price data should be < 5 calendar days old (approximates ~3 trading days).

    Note: Uses calendar days as approximation. Does not account for holidays/weekends.

    Severity:
    - critical: > 10 days old or completely missing
    - warning: 5-10 days old

    Args:
        prices: Recent price data points

    Returns:
        List of quality flags (empty if price data is current)
    """
    flags = []
    today = date.today()

    # Check for missing data
    if not prices:
        flags.append(QualityFlag(
            flag="missing_prices",
            field="price_data",
            detail="No price data available",
            severity="critical"
        ))
        return flags

    # Find most recent price date
    most_recent = max(p.date for p in prices)
    days_old = (today - most_recent).days

    # Check freshness thresholds
    if days_old > 10:
        flags.append(QualityFlag(
            flag="stale_prices",
            field="price_data",
            detail=f"Most recent price data is {days_old} days old (> 10 day threshold)",
            severity="critical"
        ))
    elif days_old > 5:
        flags.append(QualityFlag(
            flag="aging_prices",
            field="price_data",
            detail=f"Most recent price data is {days_old} days old (> 5 day threshold)",
            severity="warning"
        ))

    return flags


def check_negative_equity(
    balance_sheets: list[BalanceSheet],
) -> list[QualityFlag]:
    """Check for negative or zero equity (bankruptcy risk indicator).

    Rule: Companies with negative equity are insolvent (liabilities > assets).
    Zero equity indicates extreme financial distress.

    Severity:
    - critical: negative equity (liabilities > assets)
    - warning: zero equity (liabilities == assets)

    Args:
        balance_sheets: Recent balance sheet data

    Returns:
        List of quality flags (empty if equity is positive)
    """
    flags = []

    # No balance sheets means no data to check (freshness check handles this)
    if not balance_sheets:
        return flags

    # Check most recent balance sheet
    most_recent = max(balance_sheets, key=lambda bs: bs.period_end_date)
    equity = most_recent.total_equity

    # Handle missing equity data
    if equity is None:
        return flags

    if equity < 0:
        flags.append(QualityFlag(
            flag="negative_equity",
            field="total_equity",
            detail=(
                f"Company has negative equity ({equity}), "
                "indicating insolvency (liabilities > assets)"
            ),
            severity="critical"
        ))
    elif equity == 0.0:
        flags.append(QualityFlag(
            flag="zero_equity",
            field="total_equity",
            detail="Company has zero equity, indicating extreme financial distress",
            severity="warning"
        ))

    return flags
