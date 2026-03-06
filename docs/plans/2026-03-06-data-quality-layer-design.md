# Data Quality Layer Design (P0-①)

**Date**: 2026-03-06  
**Status**: Approved  
**Implementation Priority**: P0 (Week 1-2)

---

## Overview

Implement a data quality validation layer that checks financial data for 11 types of issues before agent analysis. The quality layer produces a `QualityReport` with flags and a quality score (0.0-1.0) that will later be used by the confidence engine (P1-④) to adjust agent confidence levels.

**Key Design Decisions**:
- ✅ Non-blocking: Quality checks flag issues but don't stop execution
- ✅ Multiplicative scoring: Multiple risks compound (e.g., 2 critical flags = 0.70 × 0.70 = 0.49)
- ✅ Not persisted: QualityReport is a runtime object, passed to report generator
- ✅ Modular rules: Each of 11 rules is an independent, testable function

---

## Architecture & Integration

### File Structure

```
src/data/quality.py          # New file: all quality check logic (~400 lines)
src/data/models.py           # Add QualityReport and QualityFlag models
src/agents/registry.py       # Modified: integrate quality checks at Phase 0
tests/test_quality.py        # New file: unit tests for 11 rules
```

### Integration Flow in `registry.py`

**Before (Current)**:
```python
def run_all_agents(ticker, market, ...):
    signals = {}
    # Phase 1: Fundamentals, Valuation
    # Phase 2: Buffett, Graham, Sentiment
    # Phase 3: Report Generator
```

**After (P0-①)**:
```python
def run_all_agents(ticker, market, ...):
    # === NEW: Phase 0 - Data Quality ===
    from src.data import database
    from src.data.quality import run_quality_checks
    
    raw_data = {
        'income': database.get_income_statements(ticker, limit=10),
        'balance': database.get_balance_sheets(ticker, limit=10),
        'cashflow': database.get_cash_flows(ticker, limit=10),
        'metrics': database.get_financial_metrics(ticker, limit=10),
        'prices': database.get_latest_prices(ticker, limit=10),
    }
    
    quality_report = run_quality_checks(ticker, market, raw_data)
    logger.info(f"Quality score: {quality_report.overall_quality_score:.2f}")
    
    # === Existing code continues ===
    signals = {}
    # Phase 1: Fundamentals, Valuation (unchanged)
    # Phase 2: Buffett, Graham, Sentiment (unchanged)
    # Phase 3: Report Generator (receives quality_report parameter)
```

**Data Retrieval Strategy**:
- Use existing `database.py` functions
- Fetch latest 10 records per data type (sufficient for trend detection and duplicate checks)
- Agents continue to query DB independently (no interface changes in P0-①)
- Future optimization (post-P0): Pass `raw_data` to agents to avoid redundant queries

---

## Data Structures

### QualityFlag Model (add to `src/data/models.py`)

```python
class QualityFlag(BaseModel):
    """Individual data quality issue"""
    flag: str           # e.g., "stale_financials", "negative_equity"
    field: str          # e.g., "income_statements", "total_equity"
    detail: str         # Human-readable explanation
    severity: Literal["critical", "warning", "info"]
```

### QualityReport Model (add to `src/data/models.py`)

```python
class QualityReport(BaseModel):
    """Data quality assessment for a ticker"""
    ticker: str
    market: MarketType
    check_date: date = Field(default_factory=date.today)
    
    flags: list[QualityFlag] = Field(default_factory=list)
    
    overall_quality_score: float = Field(..., ge=0.0, le=1.0)
    data_completeness: float = Field(..., ge=0.0, le=1.0)
    
    stale_fields: list[str] = Field(default_factory=list)
    
    # Metadata for debugging
    records_checked: dict[str, int] = Field(default_factory=dict)
    # e.g., {"income": 10, "balance": 10, "prices": 5}
```

### Scoring System

**Multiplicative Risk Compounding**:

```python
# In src/data/quality.py

SEVERITY_MULTIPLIERS = {
    "critical": 0.70,  # Each critical flag: score *= 0.70
    "warning": 0.90,   # Each warning flag: score *= 0.90
    "info": 1.00,      # Info flags don't affect score
}

def _calculate_quality_score(flags: list[QualityFlag]) -> float:
    """
    Independent risk compounding model.
    
    Examples:
    - 1 critical: 1.0 × 0.70 = 0.70
    - 2 critical: 1.0 × 0.70 × 0.70 = 0.49
    - 1 critical + 2 warning: 1.0 × 0.70 × 0.90 × 0.90 = 0.567
    
    Rationale: Multiple risks compound independently rather than linearly stacking.
    """
    score = 1.0
    for flag in flags:
        score *= SEVERITY_MULTIPLIERS[flag.severity]
    return max(0.0, min(1.0, score))
```

**Data Completeness Calculation**:

```python
# 12 core fields from roadmap Rule #6
CORE_FIELDS_MAP = {
    'income': ['revenue', 'net_income', 'eps', 'shares_outstanding'],
    'balance': ['total_assets', 'total_equity', 'total_debt', 
                'current_assets', 'current_liabilities'],
    'cashflow': ['operating_cash_flow', 'free_cash_flow'],
    'prices': ['close'],  # (derived from price_data)
}

def _calculate_completeness(raw_data: dict) -> float:
    """
    Completeness = available_core_fields / total_core_fields (12 fields)
    
    Checks latest record from each statement type.
    """
    available = 0
    total = sum(len(fields) for fields in CORE_FIELDS_MAP.values())
    
    for data_type, fields in CORE_FIELDS_MAP.items():
        data = raw_data.get(data_type, [])
        if not data:
            continue
        
        latest = max(data, key=lambda x: x.period_end_date 
                     if hasattr(x, 'period_end_date') else x.date)
        
        for field in fields:
            if getattr(latest, field, None) is not None:
                available += 1
    
    return available / total if total > 0 else 0.0
```

---

## 11 Validation Rules

### Rule 1: Financial Report Freshness (财报新鲜度)

**Logic**: Latest `period_end_date` > 15 months from today  
**Severity**: `critical`  
**Rationale**: Annual reports due by April 30. 15-month tolerance = prior year + 4-month delay.

```python
def check_financial_freshness(ticker: str, income_data: list[IncomeStatement]) -> list[QualityFlag]:
    if not income_data:
        return [QualityFlag(
            flag="missing_financials",
            field="income_statements",
            detail="No financial data available",
            severity="critical"
        )]
    
    latest = max(income_data, key=lambda x: x.period_end_date)
    months_old = (date.today() - latest.period_end_date).days / 30.4
    
    if months_old > 15:
        return [QualityFlag(
            flag="stale_financials",
            field="income_statements",
            detail=f"Latest report from {latest.period_end_date} ({months_old:.1f} months old)",
            severity="critical"
        )]
    
    return []
```

---

### Rule 2: Price Freshness (价格新鲜度)

**Logic**: Latest price > 3 trading days (5 calendar days) old  
**Severity**: `warning`  
**Rationale**: May indicate suspension/delisting; doesn't affect financial analysis.

```python
def check_price_freshness(ticker: str, price_data: list[DailyPrice]) -> list[QualityFlag]:
    if not price_data:
        return [QualityFlag(
            flag="missing_prices",
            field="daily_prices",
            detail="No price data available",
            severity="critical"
        )]
    
    latest = max(price_data, key=lambda x: x.date)
    days_old = (date.today() - latest.date).days
    
    if days_old > 5:  # 3 trading days ≈ 5 calendar days
        return [QualityFlag(
            flag="stale_prices",
            field="daily_prices",
            detail=f"Latest price from {latest.date} ({days_old} days old)",
            severity="warning"
        )]
    
    return []
```

---

### Rule 3: Revenue/Profit Anomaly (收入/利润异常波动)

**Logic**: YoY change > ±80% AND absolute change > 500M CNY  
**Severity**: `warning`  
**Rationale**: Possible one-time events; requires manual judgment.

```python
def check_revenue_profit_anomaly(income_data: list[IncomeStatement]) -> list[QualityFlag]:
    flags = []
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
    
    # Check net_income (similar logic)
    # ... (omitted for brevity)
    
    return flags
```

---

### Rule 4: NI vs OCF Divergence (盈利质量)

**Logic**: NI > 0 but OCF < 0 for 2 consecutive years  
**Severity**: `warning`  
**Rationale**: Low earnings quality signal.

```python
def check_ni_ocf_divergence(income_data: list[IncomeStatement], 
                           cashflow_data: list[CashFlow]) -> list[QualityFlag]:
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
```

---

### Rule 5: Negative Equity (负净资产)

**Logic**: `total_equity < 0` in latest balance sheet  
**Severity**: `critical`  
**Rationale**: ROE, BVPS, and other equity-based metrics are invalid.

```python
def check_negative_equity(balance_data: list[BalanceSheet]) -> list[QualityFlag]:
    if not balance_data:
        return []
    
    latest = max(balance_data, key=lambda x: x.period_end_date)
    
    if latest.total_equity and latest.total_equity < 0:
        return [QualityFlag(
            flag="negative_equity",
            field="total_equity",
            detail=f"Negative equity: {latest.total_equity/1e8:.2f}亿 (as of {latest.period_end_date})",
            severity="critical"
        )]
    
    return []
```

---

### Rule 6: Missing Core Fields (关键字段缺失)

**Logic**: Check for presence of 12 core fields in latest reports  
**Severity**: `critical` if ≥4 fields missing, `warning` if 1-3 missing

```python
CORE_FIELDS_MAP = {
    'income': ['revenue', 'net_income', 'eps', 'shares_outstanding'],
    'balance': ['total_assets', 'total_equity', 'total_debt', 
                'current_assets', 'current_liabilities'],
    'cashflow': ['operating_cash_flow', 'free_cash_flow'],
}

def check_missing_fields(raw_data: dict) -> list[QualityFlag]:
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
    
    severity = "critical" if len(missing) >= 4 else "warning"
    
    return [QualityFlag(
        flag="missing_core_fields",
        field="multiple",
        detail=f"Missing {len(missing)} fields: {', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}",
        severity=severity
    )]
```

---

### Rule 7: FCF Approximation Flag (FCF近似标记)

**Logic**: Detect if FCF uses `OCF + 投资活动净额` instead of strict `OCF - CapEx`  
**Severity**: `info`  
**Rationale**: Inform downstream agents that FCF is approximate.

**CORRECTION FROM DESIGN REVIEW**: Current code uses `fcf = ocf + inv_cf` (投资活动净额), not strict capex-based calculation.

```python
def check_fcf_approximation(cashflow_data: list[CashFlow]) -> list[QualityFlag]:
    if not cashflow_data:
        return []
    
    latest = max(cashflow_data, key=lambda x: x.period_end_date)
    
    # If FCF exists but capex is missing, likely using OCF + inv_cf approximation
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
```

---

### Rule 8: EPS Cross-validation (EPS交叉验证)

**Logic**: Compare `eps` vs `net_income / shares_outstanding`, flag if `|diff| / eps > 0.1`  
**Severity**: `warning`  
**Rationale**: Internal data source inconsistency.

```python
def check_eps_consistency(income_data: list[IncomeStatement]) -> list[QualityFlag]:
    if not income_data:
        return []
    
    latest = max(income_data, key=lambda x: x.period_end_date)
    
    if not all([latest.eps, latest.net_income, latest.shares_outstanding]):
        return []
    
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
```

---

### Rule 9: Duplicate Periods (重复报告期)

**Logic**: Check for duplicate `(ticker, period_end_date, period_type)` entries  
**Severity**: `warning`  
**Rationale**: SQLite upsert may have ingested conflicting data.

```python
def check_duplicate_periods(raw_data: dict) -> list[QualityFlag]:
    flags = []
    
    for data_type in ['income', 'balance', 'cashflow']:
        data = raw_data.get(data_type, [])
        if len(data) < 2:
            continue
        
        # Group by (period_end_date, period_type)
        from collections import Counter
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
```

---

### Rule 10: Magnitude Check (量级校验)

**Logic**: Flag if `revenue < net_income`  
**Severity**: `critical`  
**Rationale**: Likely unit conversion error (e.g., 万 vs 亿).

```python
def check_magnitude_errors(income_data: list[IncomeStatement]) -> list[QualityFlag]:
    if not income_data:
        return []
    
    latest = max(income_data, key=lambda x: x.period_end_date)
    
    if not all([latest.revenue, latest.net_income]):
        return []
    
    if latest.revenue > 0 and latest.net_income > latest.revenue:
        return [QualityFlag(
            flag="magnitude_error",
            field="revenue",
            detail=f"Revenue ({latest.revenue/1e8:.2f}亿) < Net Income ({latest.net_income/1e8:.2f}亿)",
            severity="critical"
        )]
    
    return []
```

---

### Rule 11: Source Change Detection (数据源一致性)

**Logic**: Detect if data source changed across recent periods  
**Severity**: `info`  
**Rationale**: Flag which source is currently used (SQLite upsert prevents multi-source comparison).

**CORRECTION FROM DESIGN REVIEW**: Current SQLite architecture with upsert can't retain multi-source data for same period. Changed to info-level source change detection.

```python
def check_source_changes(raw_data: dict) -> list[QualityFlag]:
    flags = []
    
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
```

---

## Orchestration Function

```python
def run_quality_checks(ticker: str, market: str, raw_data: dict) -> QualityReport:
    """
    Main orchestration function - guaranteed to return a QualityReport.
    
    Error handling: Individual rule failures are logged but don't crash the pipeline.
    """
    from src.utils.logger import get_logger
    logger = get_logger(__name__)
    
    logger.info(f"[Quality] Running checks for {ticker} ({market})")
    
    flags = []
    
    # Execute all 11 rules with error isolation
    rules = [
        ("financial_freshness", lambda: check_financial_freshness(ticker, raw_data.get('income', []))),
        ("price_freshness", lambda: check_price_freshness(ticker, raw_data.get('prices', []))),
        ("revenue_profit_anomaly", lambda: check_revenue_profit_anomaly(raw_data.get('income', []))),
        ("ni_ocf_divergence", lambda: check_ni_ocf_divergence(
            raw_data.get('income', []), raw_data.get('cashflow', []))),
        ("negative_equity", lambda: check_negative_equity(raw_data.get('balance', []))),
        ("missing_fields", lambda: check_missing_fields(raw_data)),
        ("fcf_approximation", lambda: check_fcf_approximation(raw_data.get('cashflow', []))),
        ("eps_consistency", lambda: check_eps_consistency(raw_data.get('income', []))),
        ("duplicate_periods", lambda: check_duplicate_periods(raw_data)),
        ("magnitude_errors", lambda: check_magnitude_errors(raw_data.get('income', []))),
        ("source_changes", lambda: check_source_changes(raw_data)),
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
                severity="info"
            ))
    
    # Calculate scores
    try:
        score = _calculate_quality_score(flags)
        completeness = _calculate_completeness(raw_data)
    except Exception as e:
        logger.error(f"[Quality] Scoring failed: {e}")
        score = 0.5
        completeness = 0.0
    
    # Extract stale fields
    stale_fields = [f.field for f in flags if f.flag in ("stale_financials", "stale_prices")]
    
    # Count flags by severity
    critical_count = sum(1 for f in flags if f.severity == "critical")
    warning_count = sum(1 for f in flags if f.severity == "warning")
    
    logger.info(f"[Quality] {ticker}: score={score:.2f}, "
                f"completeness={completeness:.2%}, "
                f"flags={len(flags)} (critical={critical_count}, warning={warning_count})")
    
    if score < 0.5:
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
```

---

## Error Handling & Edge Cases

### Error Handling Philosophy

**Principle**: Quality checks should **never crash** the analysis pipeline. Missing data or unexpected states return empty flags or degraded scores, not exceptions.

### Edge Cases

| Edge Case | Handling Strategy |
|-----------|-------------------|
| **Empty database** (新股票) | No critical flags; `completeness=0.0`; `score=1.0` (无数据≠坏数据) |
| **Partial data** (只有价格无财报) | Flag missing categories as `info`; agents decide if they can run |
| **Corrupted values** (None, NaN, inf) | Skip comparison; treat as missing field |
| **Date parsing errors** | Use string comparison fallback; log warning |
| **Rule execution failure** | Log warning; add `check_error` flag with `info` severity; continue |

### Logging Strategy

```python
from src.utils.logger import get_logger

logger = get_logger(__name__)

# At start
logger.info(f"[Quality] Running checks for {ticker} ({market})")
logger.debug(f"[Quality] Records: {records_checked}")

# After scoring
logger.info(f"[Quality] {ticker}: score={score:.2f}, completeness={completeness:.2%}, flags={len(flags)}")

# If low quality
if score < 0.5:
    logger.warning(f"[Quality] {ticker} has low quality score: {score:.2f}")
```

---

## Testing Strategy

### Unit Tests (`tests/test_quality.py`)

**Test coverage targets**:
- ✅ Each of 11 rules individually
- ✅ Multiplicative scoring logic
- ✅ Completeness calculation
- ✅ Error handling (empty data, missing fields)
- ✅ Edge cases (negative values, duplicates)

**Example Tests**:

```python
from datetime import date, timedelta
from src.data.models import IncomeStatement, QualityFlag
from src.data.quality import (
    check_financial_freshness,
    check_ni_ocf_divergence,
    _calculate_quality_score,
    run_quality_checks,
)

def test_financial_freshness_critical():
    """Test Rule 1: 15+ month old financials trigger critical flag"""
    old_income = [
        IncomeStatement(
            ticker="TEST",
            period_end_date=date.today() - timedelta(days=500),  # ~16 months
            period_type="annual",
            revenue=1e9,
            source="test"
        )
    ]
    
    flags = check_financial_freshness("TEST", old_income)
    
    assert len(flags) == 1
    assert flags[0].severity == "critical"
    assert "stale_financials" in flags[0].flag


def test_quality_score_multiplicative():
    """Test scoring: 2 critical flags = 0.70 × 0.70 = 0.49"""
    flags = [
        QualityFlag(flag="test1", field="f1", detail="", severity="critical"),
        QualityFlag(flag="test2", field="f2", detail="", severity="critical"),
    ]
    
    score = _calculate_quality_score(flags)
    assert abs(score - 0.49) < 0.01


def test_critical_and_warning_combined():
    """Test: 1 critical + 2 warning = 0.70 × 0.90 × 0.90 = 0.567"""
    flags = [
        QualityFlag(flag="c", field="f", detail="", severity="critical"),
        QualityFlag(flag="w1", field="f", detail="", severity="warning"),
        QualityFlag(flag="w2", field="f", detail="", severity="warning"),
    ]
    
    score = _calculate_quality_score(flags)
    assert abs(score - 0.567) < 0.01


def test_run_quality_checks_no_crash_on_empty_data():
    """Quality checks should not crash with completely empty data"""
    report = run_quality_checks("EMPTY", "a_share", raw_data={})
    
    assert report.ticker == "EMPTY"
    assert report.overall_quality_score >= 0.0
    assert report.data_completeness == 0.0
    assert isinstance(report.flags, list)


def test_ni_ocf_divergence_two_years():
    """Test Rule 4: Detect 2 consecutive years of NI>0 but OCF<0"""
    income_data = [
        IncomeStatement(
            ticker="TEST", 
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            net_income=1e9,
            source="test"
        ),
        IncomeStatement(
            ticker="TEST",
            period_end_date=date(2023, 12, 31),
            period_type="annual",
            net_income=1e9,
            source="test"
        ),
    ]
    
    cashflow_data = [
        CashFlow(
            ticker="TEST",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            operating_cash_flow=-5e8,
            source="test"
        ),
        CashFlow(
            ticker="TEST",
            period_end_date=date(2023, 12, 31),
            period_type="annual",
            operating_cash_flow=-3e8,
            source="test"
        ),
    ]
    
    flags = check_ni_ocf_divergence(income_data, cashflow_data)
    
    assert len(flags) == 1
    assert flags[0].flag == "ni_ocf_divergence"
    assert flags[0].severity == "warning"
```

### Integration Test

```python
def test_registry_integration():
    """Test that registry.py correctly integrates quality checks"""
    from src.agents.registry import run_all_agents
    
    # Requires test database with known ticker data
    signals, report_path = run_all_agents("601808.SH", "a_share", quick=True)
    
    # Verify quality report was generated (check logs or report content)
    assert report_path.exists()
    
    # Report should contain quality section in appendix
    report_text = report_path.read_text()
    assert "数据质量" in report_text or "Quality" in report_text
```

---

## Performance Considerations

| Metric | Expected Value |
|--------|----------------|
| **Runtime** | <100ms for 11 rules on 10 records each |
| **Database queries** | 5 queries (reusing existing `get_*` functions) |
| **Memory footprint** | ~50KB per QualityReport object |
| **Caching** | Not needed in P0-①; consider in P1-④ if performance issues |

---

## Example Output

```python
QualityReport(
    ticker="601808.SH",
    market="a_share",
    check_date=date(2026, 3, 6),
    
    flags=[
        QualityFlag(
            flag="stale_financials",
            field="income_statements",
            detail="Latest report from 2024-12-31 (15.2 months old)",
            severity="critical"
        ),
        QualityFlag(
            flag="ni_ocf_divergence",
            field="operating_cash_flow",
            detail="Positive NI but negative OCF in 2023 and 2024",
            severity="warning"
        ),
        QualityFlag(
            flag="source_change",
            field="balance",
            detail="Data sources vary: akshare, baostock, akshare. Latest: akshare",
            severity="info"
        ),
    ],
    
    overall_quality_score=0.63,  # 1.0 × 0.70 (critical) × 0.90 (warning)
    data_completeness=0.92,      # 11/12 core fields present
    stale_fields=["income_statements"],
    records_checked={"income": 10, "balance": 10, "cashflow": 8, "prices": 10}
)
```

---

## Implementation Checklist

- [ ] Add `QualityFlag` and `QualityReport` to `src/data/models.py`
- [ ] Create `src/data/quality.py` with:
  - [ ] 11 check functions (Rules 1-11)
  - [ ] `_calculate_quality_score()` with multiplicative logic
  - [ ] `_calculate_completeness()`
  - [ ] `run_quality_checks()` orchestration
- [ ] Modify `src/agents/registry.py`:
  - [ ] Add Phase 0 data fetching
  - [ ] Call `run_quality_checks()`
  - [ ] Pass `quality_report` to report generator
- [ ] Create `tests/test_quality.py`:
  - [ ] Unit tests for each rule
  - [ ] Scoring logic tests
  - [ ] Edge case tests
  - [ ] Integration test
- [ ] Update `src/agents/report_generator.py` (P0-③ dependency):
  - [ ] Accept `quality_report` parameter
  - [ ] Add quality section to report appendix

---

## Future Enhancements (Post-P0)

1. **P1-④ Integration**: Use `quality_report.overall_quality_score` as multiplicative factor in confidence calculation
2. **Performance optimization**: Pass `raw_data` to agents to avoid redundant DB queries
3. **Historical tracking**: Store quality reports in database for trend analysis
4. **Configurable thresholds**: Move severity thresholds to `config/quality_rules.yaml`
5. **Custom rules**: Allow users to add project-specific validation rules

---

## References

- **Roadmap**: `References/Docs/Tech Design/PROJECT_ROADMAP.md` (Lines 65-103)
- **Agent Design Skill**: `.claude/skills/agent-design/SKILL.md`
- **Data Models**: `src/data/models.py`
- **Database Layer**: `src/data/database.py`
