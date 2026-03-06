"""Data quality validation layer (P0-①).

Provides infrastructure for validating data completeness, freshness,
and logical consistency across all data sources.
"""

from datetime import date

from src.data.models import BalanceSheet, CashFlow, IncomeStatement, QualityFlag


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
