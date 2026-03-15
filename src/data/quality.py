"""Data quality validation layer (P0-①).

Provides infrastructure for validating data completeness, freshness,
and logical consistency across all data sources.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal, cast

from src.data.models import BalanceSheet, CashFlow, DailyPrice, IncomeStatement, MarketType, QualityFlag, QualityReport
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Task 3: Disclosure-Cycle-Aware Staleness ──────────────────────────────


@dataclass
class ExpectedReport:
    period: str  # "年报", "半年报", "一季报", "三季报"
    deadline: date


@dataclass
class StalenessResult:
    level: Literal["OK", "WARNING", "CRITICAL"]
    reason: str = ""
    expected_report: ExpectedReport | None = None


def get_next_expected_report(last_report_date: date) -> ExpectedReport:
    """Determine the next expected report based on A-share disclosure schedule.

    A-share disclosure deadlines:
    - Q1 (一季报): April 30
    - H1 (半年报): August 31
    - Q3 (三季报): October 31
    - Annual (年报): April 30 (next year)

    Period end dates:
    - Annual: December 31
    - Q1: March 31
    - H1: June 30
    - Q3: September 30

    Args:
        last_report_date: Period end date of the most recent report

    Returns:
        ExpectedReport with period name and deadline date
    """
    month = last_report_date.month
    year = last_report_date.year

    # Determine which report period this is based on period end date
    if month == 3:  # Q1 report (period ends March 31)
        # Next is H1 (半年报)
        return ExpectedReport(
            period="半年报",
            deadline=date(year, 8, 31)
        )
    elif month == 6:  # H1 report (period ends June 30)
        # Next is Q3 (三季报)
        return ExpectedReport(
            period="三季报",
            deadline=date(year, 10, 31)
        )
    elif month == 9:  # Q3 report (period ends September 30)
        # Next is Annual (年报)
        return ExpectedReport(
            period="年报",
            deadline=date(year + 1, 4, 30)
        )
    elif month == 12:  # Annual report (period ends December 31)
        # Next is Q1 (一季报)
        return ExpectedReport(
            period="一季报",
            deadline=date(year + 1, 4, 30)
        )
    else:
        # Fallback for non-standard dates - treat based on quarter
        if month <= 3:
            # Treat as annual from previous year, next is Q1
            return ExpectedReport(
                period="一季报",
                deadline=date(year, 4, 30)
            )
        elif month <= 6:
            # Treat as Q1, next is H1
            return ExpectedReport(
                period="半年报",
                deadline=date(year, 8, 31)
            )
        elif month <= 9:
            # Treat as H1, next is Q3
            return ExpectedReport(
                period="三季报",
                deadline=date(year, 10, 31)
            )
        else:
            # Treat as Q3, next is Annual
            return ExpectedReport(
                period="年报",
                deadline=date(year + 1, 4, 30)
            )


def check_data_staleness(
    last_report_date: date,
    reference_date: date | None = None
) -> StalenessResult:
    """Check if financial data is stale based on A-share disclosure cycle.

    This function distinguishes between:
    - CRITICAL: Expected report deadline has passed (missing report)
    - WARNING: Data > 180 days old but within normal disclosure cycle
    - OK: Data is current and within normal cycle

    Args:
        last_report_date: Period end date of most recent financial report
        reference_date: Date to check against (default: today)

    Returns:
        StalenessResult with severity level, reason, and expected report info
    """
    if reference_date is None:
        reference_date = date.today()

    # Calculate next expected report
    expected = get_next_expected_report(last_report_date)

    # Calculate days since last report
    days_old = (reference_date - last_report_date).days

    # Check if deadline has passed (CRITICAL)
    if reference_date > expected.deadline:
        return StalenessResult(
            level="CRITICAL",
            reason=f"Missing expected {expected.period} (deadline {expected.deadline} has passed)",
            expected_report=expected
        )

    # Check if data is old but within normal cycle (WARNING)
    if days_old > 180:
        return StalenessResult(
            level="WARNING",
            reason=f"Data is {days_old} days old but within normal disclosure cycle (next {expected.period} due {expected.deadline})",
            expected_report=expected
        )

    # Data is current (OK)
    return StalenessResult(
        level="OK",
        reason=f"Data is current ({days_old} days old, next {expected.period} due {expected.deadline})",
        expected_report=expected
    )


# Quality score threshold for low quality warning
LOW_QUALITY_THRESHOLD = 0.5


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

# Financial institution tickers (banks, insurance, brokers)
# These don't use standard current_assets/current_liabilities concepts
FINANCIAL_INSTITUTION_CODES = {
    "601318", "601628", "601398", "601939",  # Insurance & Big banks
    "601166", "600036", "601288", "601229",  # Joint-stock banks
    "601988", "601328", "601818", "600016",  # More banks
    "601601", "600000", "601169", "002142",  # More banks
    "601128", "601838", "600030", "601688",  # Banks & brokers
    "601211", "600837", "002736", "601881",  # Insurance & brokers
}


def _is_financial_institution(ticker: str) -> bool:
    """Check if ticker is a financial institution (bank/insurance/broker)."""
    code = ticker.split(".")[0] if "." in ticker else ticker
    return code in FINANCIAL_INSTITUTION_CODES


def _get_core_fields_for_ticker(ticker: str | None = None) -> dict[str, list[str]]:
    """Get core fields map, excluding current_assets/current_liabilities for financial institutions."""
    if ticker and _is_financial_institution(ticker):
        return {
            'income': ['revenue', 'net_income', 'eps', 'shares_outstanding'],
            'balance': ['total_assets', 'total_equity', 'total_debt'],  # No current_assets/liabilities
            'cashflow': ['operating_cash_flow', 'free_cash_flow'],
        }
    return CORE_FIELDS_MAP


def _calculate_completeness(raw_data: dict[str, list[Any]], ticker: str | None = None) -> float:
    """
    Calculate data completeness = available_core_fields / total_core_fields.

    Checks latest record from each statement type for presence of core fields.
    For financial institutions, excludes current_assets/current_liabilities.

    Returns:
        Completeness ratio between 0.0 and 1.0
    """
    core_fields_map = _get_core_fields_for_ticker(ticker)
    available = 0
    total_fields = sum(len(fields) for fields in core_fields_map.values()) + 1  # +1 for price

    # Check financial data fields
    for data_type, fields in core_fields_map.items():
        data = raw_data.get(data_type, [])
        if not data:
            continue

        latest = max(data, key=lambda x: x.period_end_date)

        for field in fields:
            if getattr(latest, field, None) is not None:
                available += 1

    # Check price data
    price_data = raw_data.get('prices', [])
    if price_data:
        latest_price = max(price_data, key=lambda x: x.date)
        if latest_price.close is not None:
            available += 1

    return available / total_fields if total_fields > 0 else 0.0


def check_missing_fields(raw_data: dict[str, list[Any]], ticker: str | None = None) -> list[QualityFlag]:
    """
    Check for missing core fields in latest reports.

    For financial institutions (banks/insurance), excludes current_assets/current_liabilities
    since these concepts don't apply to their balance sheet structure.

    Severity:
    - critical: >= 4 core fields missing
    - warning: 1-3 fields missing
    """
    missing = []
    core_fields_map = _get_core_fields_for_ticker(ticker)

    for data_type, fields in core_fields_map.items():
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


def check_source_availability() -> list[QualityFlag]:
    """
    Check if external data sources (like QVeris) are unavailable due to credits or config.

    Severity: info (just for user awareness)
    """
    from src.data.qveris_source import _CREDITS_EXHAUSTED, _get_api_key

    flags: list[QualityFlag] = []

    if _CREDITS_EXHAUSTED:
        flags.append(QualityFlag(
            flag="source_unavailable",
            field="QVeris iFinD",
            detail="QVeris API 余额不足 (Insufficient credits), 部分A股深度财务数据抓取受限。",
            severity="warning"
        ))
    elif not _get_api_key():
        flags.append(QualityFlag(
            flag="source_config_missing",
            field="QVeris iFinD",
            detail="未配置 QVERIS_API_KEY, 无法获取高精度A股财务数据。",
            severity="info"
        ))

    return flags


# ── Rule 12: Median Deviation Detection ───────────────────────────────────


def _is_growth_company(revenues: list[float]) -> tuple[bool, float | None]:
    """
    Check if company is a growth company based on revenue growth.

    BUG-07 FIX: For growth companies, historical median underestimates current scale.
    A company is considered "growth" if recent YoY revenue growth > 20%.

    Args:
        revenues: List of annual revenues sorted newest first

    Returns:
        (is_growth, growth_rate): Whether company is growth and YoY growth rate
    """
    if len(revenues) < 2:
        return False, None

    current = revenues[0]
    prior = revenues[1]

    if prior <= 0:
        return False, None

    growth_rate = (current - prior) / prior
    return growth_rate > 0.20, growth_rate


def check_median_deviation(income_data: list[IncomeStatement],
                           balance_data: list[BalanceSheet]) -> list[QualityFlag]:
    """
    Check if key financial metrics deviate >50% from historical median.

    BUG-07 FIX: For growth companies (revenue growth >20%), use only recent 2 years
    of data instead of full historical median. This prevents misleading warnings
    like "汇川营收偏离中位数292%" for high-growth stocks.

    Detects outliers in P/E-relevant metrics (revenue, net_income, equity)
    that could indicate data quality issues or structural changes.

    Severity: warning (may indicate data quality issue or major business change)
             info (for growth companies - deviation is expected behavior)
    """
    import statistics

    flags: list[QualityFlag] = []

    # Check revenue deviation from median
    annual_income = [r for r in income_data if r.period_type == "annual"]
    if len(annual_income) >= 3:
        revenues = [r.revenue for r in annual_income if r.revenue and r.revenue > 0]
        if len(revenues) >= 3:
            # BUG-07 FIX: Check if this is a growth company
            is_growth, growth_rate = _is_growth_company(revenues)

            if is_growth and len(revenues) >= 2:
                # For growth companies, use only recent 2 years as baseline
                median_rev = statistics.median(revenues[:2])
                latest_rev = revenues[0]
                deviation = abs(latest_rev - median_rev) / median_rev if median_rev > 0 else 0

                # For growth companies, only flag if deviation is still excessive
                # relative to recent history (>50% deviation even from 2-year median)
                if deviation > 0.50:
                    flags.append(QualityFlag(
                        flag="median_deviation",
                        field="revenue",
                        detail=f"成长股营收偏离近2年中位数 {deviation:.1%} (增速: {growth_rate:.0%}, 近2年中位数: {median_rev/1e8:.1f}亿, 最新: {latest_rev/1e8:.1f}亿)",
                        severity="info"  # Info for growth stocks, not warning
                    ))
            else:
                # Standard check for non-growth companies
                median_rev = statistics.median(revenues)
                latest_rev = revenues[0]
                deviation = abs(latest_rev - median_rev) / median_rev
                if deviation > 0.50:
                    flags.append(QualityFlag(
                        flag="median_deviation",
                        field="revenue",
                        detail=f"最新营收偏离中位数 {deviation:.1%} (中位数: {median_rev/1e8:.1f}亿, 最新: {latest_rev/1e8:.1f}亿)",
                        severity="warning"
                    ))

    # Check net income deviation from median
    if len(annual_income) >= 3:
        net_incomes = [r.net_income for r in annual_income if r.net_income and abs(r.net_income) > 1e6]
        if len(net_incomes) >= 3:
            median_ni = statistics.median(net_incomes)
            latest_ni = net_incomes[0]
            if abs(median_ni) > 1e6:  # Avoid division by near-zero
                deviation = abs(latest_ni - median_ni) / abs(median_ni)
                if deviation > 0.50:
                    flags.append(QualityFlag(
                        flag="median_deviation",
                        field="net_income",
                        detail=f"最新净利润偏离中位数 {deviation:.1%} (中位数: {median_ni/1e8:.1f}亿, 最新: {latest_ni/1e8:.1f}亿)",
                        severity="warning"
                    ))

    # Check equity deviation from median
    annual_balance = [b for b in balance_data if b.period_type == "annual"]
    if len(annual_balance) >= 3:
        equities = [b.total_equity for b in annual_balance if b.total_equity and b.total_equity > 0]
        if len(equities) >= 3:
            median_eq = statistics.median(equities)
            latest_eq = equities[0]
            deviation = abs(latest_eq - median_eq) / median_eq
            if deviation > 0.50:
                flags.append(QualityFlag(
                    flag="median_deviation",
                    field="total_equity",
                    detail=f"最新权益偏离中位数 {deviation:.1%} (中位数: {median_eq/1e8:.1f}亿, 最新: {latest_eq/1e8:.1f}亿)",
                    severity="warning"
                ))

    return flags


# ── Rule 13: Price Volatility Detection ───────────────────────────────────


def check_price_volatility(price_data: list[DailyPrice]) -> list[QualityFlag]:
    """
    Check if stock price exhibits >80% volatility (max-min range).

    High volatility may indicate:
    - Data quality issues (erroneous extreme values)
    - Speculative trading / manipulation
    - Major corporate events (restructuring, M&A)

    Severity: warning (requires manual review of context)
    """
    flags: list[QualityFlag] = []

    if len(price_data) < 30:  # Need reasonable sample size
        return []

    # Sort by date descending (most recent first)
    sorted_prices = sorted(price_data, key=lambda x: x.date, reverse=True)

    # Check last 90 days of trading (roughly 1 quarter)
    recent_prices = sorted_prices[:90]
    closes = [p.close for p in recent_prices if p.close and p.close > 0]

    if len(closes) < 20:
        return []

    max_price = max(closes)
    min_price = min(closes)
    volatility = (max_price - min_price) / min_price

    if volatility > 0.80:
        flags.append(QualityFlag(
            flag="high_volatility",
            field="price",
            detail=f"近90日价格波幅 {volatility:.1%} (最高: ¥{max_price:.2f}, 最低: ¥{min_price:.2f})",
            severity="warning"
        ))

    return flags


# ── Rule 14: Probe Data Completeness ───────────────────────────────────────


def check_probe_data_completeness(
    income_data: list[IncomeStatement],
    balance_data: list[BalanceSheet],
    cashflow_data: list[CashFlow],
    metric_data: list[dict],
) -> list[QualityFlag]:
    """
    Check if data is sufficient for intelligent probes (brand_moat, cyclical, etc.).

    Probes require multi-year metrics like:
    - ROE 5-year average (for brand_moat detection)
    - FCF history (for brand_moat detection)
    - Gross margin history (for brand_moat tier classification)

    When data is insufficient, probes silently fall back to keyword matching,
    which can lead to framework misclassification. This check warns users.

    Severity: warning (system still runs but probe accuracy degraded)
    """
    flags: list[QualityFlag] = []

    # Minimum years of data for reliable probe detection
    MIN_YEARS_FOR_PROBES = 3
    MIN_FCF_RECORDS = 3

    # Check income statement history
    valid_income_years = len([
        i for i in income_data
        if i.get("net_income") is not None or i.get("revenue") is not None
    ])

    # Check balance sheet history
    valid_balance_years = len([
        b for b in balance_data
        if b.get("total_assets") is not None
    ])

    # Check cashflow history (for FCF)
    valid_cashflow_years = len([
        c for c in cashflow_data
        if c.get("operating_cashflow") is not None
    ])

    # Check metric history (for ROE, gross_margin)
    valid_metric_years = 0
    if metric_data:
        valid_metric_years = len([
            m for m in metric_data
            if m.get("roe") is not None or m.get("gross_margin") is not None
        ])

    # Aggregate insufficient data categories
    missing_probes = []

    if valid_metric_years < MIN_YEARS_FOR_PROBES:
        missing_probes.append(f"ROE/毛利率历史不足({valid_metric_years}年<{MIN_YEARS_FOR_PROBES}年)")

    if valid_cashflow_years < MIN_FCF_RECORDS:
        missing_probes.append(f"FCF历史不足({valid_cashflow_years}条<{MIN_FCF_RECORDS}条)")

    if valid_income_years < MIN_YEARS_FOR_PROBES:
        missing_probes.append(f"利润表历史不足({valid_income_years}年<{MIN_YEARS_FOR_PROBES}年)")

    if missing_probes:
        flags.append(QualityFlag(
            flag="probe_degraded",
            field="multi_year_metrics",
            detail=f"⚠️ 智能探针可能失效: {'; '.join(missing_probes)}。系统将使用关键词匹配代替，估值框架可能不准确。",
            severity="warning"
        ))

    return flags


# ── Orchestration ─────────────────────────────────────────────────────────

def run_quality_checks(ticker: str, market: MarketType, raw_data: dict[str, list[Any]]) -> QualityReport:
    """
    Main orchestration function - runs all 14 quality checks.

    Guaranteed to return a QualityReport. Individual rule failures are logged
    but don't crash the pipeline.

    Args:
        ticker: Stock ticker (e.g., "601808.SH")
        market: Market type ("a_share", "hk", "us")
        raw_data: Dict with keys ['income', 'balance', 'cashflow', 'prices', 'metrics']

    Returns:
        QualityReport with flags, scores, and metadata
    """
    logger.info(f"[Quality] Running checks for {ticker} ({market})")

    flags: list[QualityFlag] = []

    # Execute all 14 rules with error isolation
    # IMPORTANT: Use the correct function signatures!
    rules: list[tuple[str, Callable[[], list[QualityFlag]]]] = [
        ("financial_freshness", lambda: check_financial_freshness(
            cast(list[IncomeStatement], raw_data.get('income', [])),
            cast(list[BalanceSheet], raw_data.get('balance', [])),
            cast(list[CashFlow], raw_data.get('cashflow', []))
        )),
        ("price_freshness", lambda: check_price_freshness(cast(list[DailyPrice], raw_data.get('prices', [])))),
        ("revenue_profit_anomaly", lambda: check_revenue_profit_anomaly(cast(list[IncomeStatement], raw_data.get('income', [])))),
        ("ni_ocf_divergence", lambda: check_ni_ocf_divergence(
            cast(list[IncomeStatement], raw_data.get('income', [])),
            cast(list[CashFlow], raw_data.get('cashflow', []))
        )),
        ("negative_equity", lambda: check_negative_equity(cast(list[BalanceSheet], raw_data.get('balance', [])))),
        ("missing_fields", lambda: check_missing_fields(raw_data, ticker)),
        ("fcf_approximation", lambda: check_fcf_approximation(cast(list[CashFlow], raw_data.get('cashflow', [])))),
        ("eps_consistency", lambda: check_eps_consistency(cast(list[IncomeStatement], raw_data.get('income', [])))),
        ("duplicate_periods", lambda: check_duplicate_periods(raw_data)),
        ("magnitude", lambda: check_magnitude(cast(list[IncomeStatement], raw_data.get('income', [])))),
        ("source_changes", lambda: check_source_changes(raw_data)),
        ("source_availability", check_source_availability),
        ("median_deviation", lambda: check_median_deviation(
            cast(list[IncomeStatement], raw_data.get('income', [])),
            cast(list[BalanceSheet], raw_data.get('balance', []))
        )),
        ("price_volatility", lambda: check_price_volatility(cast(list[DailyPrice], raw_data.get('prices', [])))),
        # P0-2: Probe data completeness check - warns if intelligent probes may fail
        ("probe_data_completeness", lambda: check_probe_data_completeness(
            cast(list[IncomeStatement], raw_data.get('income', [])),
            cast(list[BalanceSheet], raw_data.get('balance', [])),
            cast(list[CashFlow], raw_data.get('cashflow', [])),
            raw_data.get('metrics', [])  # Raw dicts
        )),
    ]

    for rule_name, rule_func in rules:
        try:
            flags.extend(rule_func())
        except Exception as e:
            logger.warning(f"[Quality] Rule '{rule_name}' failed: {e}")
            flags.append(QualityFlag(
                flag="check_error",
                field=rule_name,
                detail=f"Quality check failed: {str(e)[:100]}",
                severity="warning"
            ))

    # Calculate scores
    try:
        score = _calculate_quality_score(flags)
        completeness = _calculate_completeness(raw_data, ticker)
    except Exception as e:
        logger.error(f"[Quality] Scoring failed: {e}")
        score = 0.5
        completeness = 0.0

    # Extract stale fields
    stale_fields = [f.field for f in flags if f.flag in ("stale_financials", "stale_prices")]

    # Count flags by severity for logging
    critical_count = sum(1 for f in flags if f.severity == "critical")
    warning_count = sum(1 for f in flags if f.severity == "warning")

    logger.info(f"[Quality] {ticker}: score={score:.2f}, "
                f"completeness={completeness:.2%}, "
                f"flags={len(flags)} (critical={critical_count}, warning={warning_count})")

    if score < LOW_QUALITY_THRESHOLD:
        logger.warning(f"[Quality] {ticker} has low quality score: {score:.2f}")

    return QualityReport(
        ticker=ticker,
        market=market,
        flags=flags,
        overall_quality_score=score,
        data_completeness=completeness,
        stale_fields=stale_fields,
        records_checked={k: len(v) for k, v in raw_data.items()}
    )
