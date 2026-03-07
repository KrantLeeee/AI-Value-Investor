# Data Quality Layer Usage Guide

**Implemented**: P0-① (Week 1-2)
**Design Doc**: `docs/plans/2026-03-06-data-quality-layer-design.md`

---

## Overview

The data quality layer validates financial data for 11 types of issues before agent analysis. It produces a `QualityReport` with:
- **Quality score** (0.0-1.0): Multiplicative scoring based on severity
- **Data completeness** (0.0-1.0): Percentage of 12 core fields present
- **Flags**: List of specific issues found

## Automatic Integration

Quality checks run automatically in `registry.py` **Phase 0** before any agents execute.

No code changes needed - just run analysis normally:

```bash
poetry run invest report -t 601808.SH
poetry run invest scan
```

## Accessing Quality Report

### In Report Generator (P0-③)

```python
def run(ticker, market, *, signals, quality_report, analysis_date, use_llm):
    # quality_report is now passed to every report generation

    # Example: add quality section to appendix
    if quality_report:
        appendix += f"\n## Data Quality\n"
        appendix += f"Quality Score: {quality_report.overall_quality_score:.2f}\n"
        appendix += f"Completeness: {quality_report.data_completeness:.2%}\n"

        if quality_report.flags:
            appendix += f"\n### Issues Found:\n"
            for flag in quality_report.flags:
                appendix += f"- [{flag.severity.upper()}] {flag.detail}\n"
```

### In Confidence Engine (P1-④)

```python
from src.data.quality import run_quality_checks

# In agent code
quality_report = get_quality_report(ticker, market)  # Cached from Phase 0

confidence = calculate_confidence(
    signal_strength=0.7,
    indicator_agreement=0.8,
    quality_score=quality_report.overall_quality_score  # Multiplicative factor
)
```

## Understanding Quality Scores

**Multiplicative Scoring**:
- 1 critical flag: 1.0 × 0.70 = **0.70**
- 2 critical flags: 1.0 × 0.70 × 0.70 = **0.49**
- 1 critical + 2 warnings: 1.0 × 0.70 × 0.90 × 0.90 = **0.567**

**Severity Levels**:
- **Critical**: Major data issues that invalidate metrics (e.g., negative equity, stale financials)
- **Warning**: Potential issues requiring attention (e.g., NI/OCF divergence, anomalies)
- **Info**: Informational notes (e.g., FCF approximation, source changes)

## 11 Validation Rules

| # | Rule | Severity | Trigger Condition |
|---|------|----------|-------------------|
| 1 | Financial freshness | Critical | Latest report > 15 months old |
| 2 | Price freshness | Warning | Latest price > 5 days old |
| 3 | Revenue/profit anomaly | Warning | YoY > ±80% & Δ > 500M |
| 4 | NI vs OCF divergence | Warning | NI>0 but OCF<0 for 2 years |
| 5 | Negative equity | Critical | total_equity < 0 |
| 6 | Missing core fields | Critical/Warning | ≥4 fields missing (critical), 1-3 (warning) |
| 7 | FCF approximation | Info | FCF uses OCF + inv_cf estimate |
| 8 | EPS cross-validation | Warning | \|EPS - NI/shares\| / EPS > 10% |
| 9 | Duplicate periods | Warning | Same period appears multiple times |
| 10 | Magnitude errors | Critical | Revenue < Net Income |
| 11 | Source changes | Info | Data sources vary across periods |

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
    ],

    overall_quality_score=0.63,  # 1.0 × 0.70 × 0.90
    data_completeness=0.92,
    stale_fields=["income_statements"],
    records_checked={"income": 10, "balance": 10, "cashflow": 8, "prices": 10}
)
```

## Testing

**Run quality layer tests**:
```bash
poetry run pytest tests/test_quality.py -v
```

**Run integration test** (requires database):
```bash
poetry run pytest tests/test_quality.py::test_full_pipeline_with_quality_layer -v
```

**Test specific rules**:
```bash
poetry run pytest tests/test_quality.py -k "test_financial_freshness" -v
```

## Performance

- **Runtime**: <100ms for 11 rules on 10 records each
- **Database queries**: 5 queries (reusing existing functions)
- **Memory**: ~50KB per QualityReport object

## Future Enhancements (Post-P0)

- **P1-④**: Use quality score in confidence calculation
- **P0-③**: Display quality report in appendix
- **P3-⑨**: Track quality trends over time
- **Custom rules**: Add project-specific validations
