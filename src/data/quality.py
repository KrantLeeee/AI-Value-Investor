"""Data quality validation layer (P0-①).

Provides infrastructure for validating data completeness, freshness,
and logical consistency across all data sources.
"""

from datetime import date
from typing import Any, Literal

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
    flags: list[QualityFlag] = []
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
    flags: list[QualityFlag] = []
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
    flags: list[QualityFlag] = []

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


def check_magnitude(
    income_statements: list[IncomeStatement],
) -> list[QualityFlag]:
    """Check if revenue < net_income (likely unit conversion error).

    Rule: Revenue should always be >= net_income. If net_income > revenue,
    this indicates a data error, typically unit conversion (万 vs 亿).

    Severity:
    - critical: net_income > revenue (data corruption)

    Args:
        income_statements: Recent income statements

    Returns:
        List of quality flags (empty if revenue >= net_income)
    """
    flags: list[QualityFlag] = []

    if not income_statements:
        return flags

    # Check most recent income statement
    most_recent = max(income_statements, key=lambda stmt: stmt.period_end_date)

    # Handle None values
    if most_recent.revenue is None or most_recent.net_income is None:
        return flags

    # Check for magnitude error
    if most_recent.revenue > 0 and most_recent.net_income > most_recent.revenue:
        flags.append(QualityFlag(
            flag="magnitude_error",
            field="revenue",
            detail=f"Revenue ({most_recent.revenue/1e8:.2f}亿) < Net Income ({most_recent.net_income/1e8:.2f}亿), likely unit conversion error",
            severity="critical"
        ))

    return flags


# ── Rule 3: Revenue/Profit Anomaly ────────────────────────────────────────


def check_revenue_profit_anomaly(income_data: list[IncomeStatement]) -> list[QualityFlag]:
    """
    Check for YoY changes > ±80% with absolute value > 500M CNY.

    Severity: warning (possible one-time events; requires manual judgment)
    """
    flags: list[QualityFlag] = []
    annual_reports = [r for r in income_data if r.period_type == "annual"]
    if len(annual_reports) < 2:
        return []

    annual_reports.sort(key=lambda x: x.period_end_date, reverse=True)
    current = annual_reports[0]
    previous = annual_reports[1]

    # Check revenue
    if current.revenue and previous.revenue and abs(previous.revenue) > 1e6:
        yoy_change = (current.revenue - previous.revenue) / abs(previous.revenue)
        if abs(yoy_change) > 0.80 and abs(current.revenue - previous.revenue) > 5e8:
            flags.append(QualityFlag(
                flag="revenue_anomaly",
                field="revenue",
                detail=f"Revenue YoY: {yoy_change:+.1%} (Δ: {(current.revenue - previous.revenue)/1e8:.1f}亿)",
                severity="warning"
            ))

    # Check net_income
    if current.net_income and previous.net_income and abs(previous.net_income) > 1e6:
        yoy_change = (current.net_income - previous.net_income) / abs(previous.net_income)
        if abs(yoy_change) > 0.80 and abs(current.net_income - previous.net_income) > 5e8:
            flags.append(QualityFlag(
                flag="net_income_anomaly",
                field="net_income",
                detail=f"Net income YoY: {yoy_change:+.1%} (Δ: {(current.net_income - previous.net_income)/1e8:.1f}亿)",
                severity="warning"
            ))

    return flags


# ── Rule 4: NI vs OCF Divergence ──────────────────────────────────────────


def check_ni_ocf_divergence(income_data: list[IncomeStatement],
                           cashflow_data: list[CashFlow]) -> list[QualityFlag]:
    """
    Check if NI > 0 but OCF < 0 for 2 consecutive years.

    Severity: warning (low earnings quality signal)
    """
    # Match by period_end_date
    annual_income = {r.period_end_date: r for r in income_data if r.period_type == "annual"}
    annual_cashflow = {r.period_end_date: r for r in cashflow_data if r.period_type == "annual"}

    common_periods = sorted(set(annual_income.keys()) & set(annual_cashflow.keys()), reverse=True)

    if len(common_periods) < 2:
        return []

    divergence_years = []
    for period in common_periods[:2]:
        ni = annual_income[period].net_income
        ocf = annual_cashflow[period].operating_cash_flow

        if ni and ocf and ni > 0 and ocf < 0:
            divergence_years.append(period)

    if len(divergence_years) >= 2:
        return [QualityFlag(
            flag="ni_ocf_divergence",
            field="operating_cash_flow",
            detail=f"Positive NI but negative OCF in {divergence_years[0].year} and {divergence_years[1].year}",
            severity="warning"
        )]

    return []


# ── Rule 6: Missing Core Fields ───────────────────────────────────────────


CORE_FIELDS_MAP = {
    'income': ['revenue', 'net_income', 'eps', 'shares_outstanding'],
    'balance': ['total_assets', 'total_equity', 'total_debt',
                'current_assets', 'current_liabilities'],
    'cashflow': ['operating_cash_flow', 'free_cash_flow'],
}


def check_missing_fields(raw_data: dict[str, list[Any]]) -> list[QualityFlag]:
    """
    Check for missing core fields in latest reports.

    Severity:
    - critical: >= 4 core fields missing
    - warning: 1-3 fields missing
    """
    missing = []

    for data_type, fields in CORE_FIELDS_MAP.items():
        data = raw_data.get(data_type, [])
        if not data:
            missing.extend(fields)
            continue

        latest = max(data, key=lambda x: x.period_end_date)
        for field in fields:
            if getattr(latest, field, None) is None:
                missing.append(field)

    if not missing:
        return []

    severity: Literal["critical", "warning"] = "critical" if len(missing) >= 4 else "warning"

    return [QualityFlag(
        flag="missing_core_fields",
        field="multiple",
        detail=f"Missing {len(missing)} fields: {', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}",
        severity=severity
    )]


# ── Rule 7: FCF Approximation Flag ────────────────────────────────────────


def check_fcf_approximation(cashflow_data: list[CashFlow]) -> list[QualityFlag]:
    """
    Detect if FCF uses OCF + 投资活动净额 instead of strict OCF - CapEx.

    Severity: info (inform downstream that FCF is approximate)
    """
    if not cashflow_data:
        return []

    latest = max(cashflow_data, key=lambda x: x.period_end_date)

    # If FCF exists but capex is missing, likely using approximation
    if (latest.free_cash_flow is not None and
        latest.operating_cash_flow is not None and
        latest.capital_expenditure is None):

        return [QualityFlag(
            flag="fcf_approximation",
            field="free_cash_flow",
            detail="FCF computed using OCF + 投资活动净额, not strict capex-based formula",
            severity="info"
        )]

    return []


# ── Rule 8: EPS Cross-validation ──────────────────────────────────────────


def check_eps_consistency(income_data: list[IncomeStatement]) -> list[QualityFlag]:
    """
    Compare reported EPS vs calculated (net_income / shares_outstanding).

    Flag if |difference| / eps > 0.1
    Severity: warning (internal data source inconsistency)
    """
    if not income_data:
        return []

    latest = max(income_data, key=lambda x: x.period_end_date)

    # Explicit None checks BEFORE any comparisons
    if (latest.eps is None or latest.net_income is None or
        latest.shares_outstanding is None):
        return []

    # Now safe to use values
    if latest.shares_outstanding <= 0 or abs(latest.eps) < 1e-6:
        return []

    calculated_eps = latest.net_income / latest.shares_outstanding
    diff_ratio = abs(latest.eps - calculated_eps) / abs(latest.eps)

    if diff_ratio > 0.1:
        return [QualityFlag(
            flag="eps_inconsistency",
            field="eps",
            detail=f"Reported EPS: {latest.eps:.3f}, Calculated: {calculated_eps:.3f} (diff: {diff_ratio:.1%})",
            severity="warning"
        )]

    return []


# ── Rule 9: Duplicate Periods ─────────────────────────────────────────────


def check_duplicate_periods(raw_data: dict[str, list[Any]]) -> list[QualityFlag]:
    """
    Check for duplicate (ticker, period_end_date, period_type) entries.

    Severity: warning (SQLite upsert may have conflicting data)
    """
    from collections import Counter

    flags: list[QualityFlag] = []

    for data_type in ['income', 'balance', 'cashflow']:
        data = raw_data.get(data_type, [])
        if len(data) < 2:
            continue

        # Group by (period_end_date, period_type)
        periods = Counter((r.period_end_date, r.period_type) for r in data)

        duplicates = [(period, count) for period, count in periods.items() if count > 1]

        if duplicates:
            flags.append(QualityFlag(
                flag="duplicate_periods",
                field=data_type,
                detail=f"Found {len(duplicates)} duplicate periods: {duplicates[0][0]} (×{duplicates[0][1]})",
                severity="warning"
            ))

    return flags


# ── Rule 11: Source Change Detection ──────────────────────────────────────


def check_source_changes(raw_data: dict[str, list[Any]]) -> list[QualityFlag]:
    """
    Detect if data source changed across recent periods.

    Severity: info (flag which source is currently used)
    """
    flags: list[QualityFlag] = []

    for data_type in ['income', 'balance', 'cashflow']:
        data = raw_data.get(data_type, [])
        if len(data) < 2:
            continue

        sorted_data = sorted(data, key=lambda x: x.period_end_date, reverse=True)
        sources = [r.source for r in sorted_data[:3]]

        if len(set(sources)) > 1:
            flags.append(QualityFlag(
                flag="source_change",
                field=data_type,
                detail=f"Data sources vary: {', '.join(sources[:3])}. Latest: {sources[0]}",
                severity="info"
            ))

    return flags
