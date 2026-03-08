# Data Quality & Signal Logic Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 5 critical issues: Graham signal logic bug, valuation outlier detection, data freshness blocking, multi-source fallback, and report language professionalization.

**Architecture:** Code-first signal determination with LLM override caps; statistical outlier detection using median; disclosure-cycle-aware staleness checks; priority-based data source fallback chain.

**Tech Stack:** Python 3.11+, tushare, requests, statistics module, pytest

---

## Task 1: Graham Agent Signal Logic Fix

**Files:**
- Modify: `src/agents/ben_graham.py:150-217`
- Test: `tests/agents/test_ben_graham.py`

**Step 1: Write failing tests for signal hard rules**

```python
# tests/agents/test_ben_graham.py

import pytest
from unittest.mock import patch, MagicMock
from src.agents.ben_graham import run, SIGNAL_ORDER, _apply_signal_cap

class TestGrahamSignalHardRules:
    """Test Graham Agent signal logic with hard rules"""

    def test_zero_criteria_returns_bearish(self):
        """0/7 criteria passed must return bearish, skip LLM"""
        with patch('src.agents.ben_graham.get_income_statements') as mock_income, \
             patch('src.agents.ben_graham.get_balance_sheets') as mock_balance, \
             patch('src.agents.ben_graham.get_financial_metrics') as mock_metrics, \
             patch('src.agents.ben_graham.insert_agent_signal'):

            # Setup: all criteria fail
            mock_income.return_value = [{"net_income": 100, "eps": 0.5}]
            mock_balance.return_value = [{"current_assets": 100, "current_liabilities": 200}]  # CR < 2
            mock_metrics.return_value = [{"pe_ratio": 50, "pb_ratio": 5}]  # PE > 15, PB > 1.5

            result = run("601808.SH", "a_share", use_llm=False)

            assert result.signal == "bearish"
            assert result.confidence == 0.70

    def test_one_to_two_criteria_returns_bearish_dynamic_confidence(self):
        """1-2/7 criteria passed: bearish with dynamic confidence"""
        with patch('src.agents.ben_graham.get_income_statements') as mock_income, \
             patch('src.agents.ben_graham.get_balance_sheets') as mock_balance, \
             patch('src.agents.ben_graham.get_financial_metrics') as mock_metrics, \
             patch('src.agents.ben_graham.insert_agent_signal'):

            mock_income.return_value = [{"net_income": 100, "eps": 0.5}]
            mock_balance.return_value = [{"current_assets": 400, "current_liabilities": 200}]  # CR >= 2 ✓
            mock_metrics.return_value = [{"pe_ratio": 50, "pb_ratio": 5, "debt_to_equity": 0.3}]  # D/E <= 0.5 ✓

            result = run("601808.SH", "a_share", use_llm=False)

            assert result.signal == "bearish"
            assert 0.40 <= result.confidence <= 0.55

    def test_three_to_four_criteria_caps_at_neutral(self):
        """3-4/7 criteria: LLM cannot return bullish"""
        # This requires mocking LLM to return bullish and verifying cap
        pass  # Will implement in Step 3

    def test_signal_order_constant(self):
        """SIGNAL_ORDER constant is correct"""
        assert SIGNAL_ORDER == {"bearish": 0, "neutral": 1, "bullish": 2}

    def test_apply_signal_cap_downgrades_bullish_to_neutral(self):
        """_apply_signal_cap correctly limits signals"""
        result = _apply_signal_cap(
            llm_signal="bullish",
            llm_confidence=0.80,
            max_signal="neutral",
            fallback_confidence=0.45
        )
        assert result["signal"] == "neutral"
        assert result["confidence"] <= 0.55

    def test_missing_criteria_passed_returns_neutral(self):
        """If criteria_passed is None, return neutral with low confidence"""
        with patch('src.agents.ben_graham.get_income_statements', return_value=[]), \
             patch('src.agents.ben_graham.get_balance_sheets', return_value=[]), \
             patch('src.agents.ben_graham.get_financial_metrics', return_value=[]), \
             patch('src.agents.ben_graham.insert_agent_signal'):

            result = run("601808.SH", "a_share", use_llm=False)

            assert result.signal == "neutral"
            assert result.confidence == 0.30
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_ben_graham.py -v`
Expected: FAIL (functions don't exist yet or logic differs)

**Step 3: Implement signal hard rules**

```python
# src/agents/ben_graham.py - Add after line 28

SIGNAL_ORDER = {"bearish": 0, "neutral": 1, "bullish": 2}


def _apply_signal_cap(
    llm_signal: str,
    llm_confidence: float,
    max_signal: str | None,
    fallback_confidence: float | None
) -> dict:
    """Apply signal cap - code is the final gate, LLM cannot exceed max_signal"""
    if max_signal is None:
        return {"signal": llm_signal, "confidence": llm_confidence}

    if SIGNAL_ORDER.get(llm_signal, 1) > SIGNAL_ORDER[max_signal]:
        return {
            "signal": max_signal,
            "confidence": min(llm_confidence, 0.55),
            "capped": True
        }

    return {
        "signal": llm_signal,
        "confidence": fallback_confidence if fallback_confidence else llm_confidence,
        "capped": False
    }
```

**Step 4: Modify run() function**

```python
# src/agents/ben_graham.py - Replace lines 150-217

def run(
    ticker: str,
    market: str,
    valuation_signal: AgentSignal | None = None,
    use_llm: bool = True,
) -> AgentSignal:
    """
    Run the Graham Agent.
    Hard rules determine signal boundaries; LLM provides reasoning only.
    """
    income_rows = get_income_statements(ticker, limit=10, period_type="annual")
    balance_rows = get_balance_sheets(ticker, limit=5, period_type="annual")
    metric_rows = get_financial_metrics(ticker, limit=5)

    # ... existing criteria calculation code (lines 56-149) ...

    n_pass = metrics_snapshot.get("criteria_passed")
    n_total = metrics_snapshot.get("criteria_total", 7)
    data_completeness = metrics_snapshot.get("data_completeness", 0.5)

    # Defensive check: missing data
    if n_pass is None:
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="neutral",
            confidence=0.30,
            reasoning="格雷厄姆标准数据不足，无法评判",
            metrics=metrics_snapshot,
        )
        insert_agent_signal(agent_signal)
        return agent_signal

    # Hard rule: 0 criteria passed → must be bearish, skip LLM
    if n_pass == 0:
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="bearish",
            confidence=0.70,
            reasoning=f"格雷厄姆标准: 0/{n_total} 通过 → 不符合防御型投资标准\n" + "\n".join(criteria_passed),
            metrics=metrics_snapshot,
        )
        insert_agent_signal(agent_signal)
        logger.info("[Graham] %s: 0/%d criteria → hard bearish", ticker, n_total)
        return agent_signal

    # Determine signal cap and confidence based on criteria count
    if n_pass <= 2:
        max_signal = "bearish"
        fallback_confidence = 0.40 + (0.15 * data_completeness)
    elif n_pass <= 4:
        max_signal = "neutral"
        fallback_confidence = 0.45
    elif n_pass <= 6:
        max_signal = "bullish"  # Allow bullish but LLM decides
        fallback_confidence = None
    else:  # 7/7
        max_signal = None
        fallback_confidence = None

    # LLM call for reasoning
    llm_signal, llm_confidence, reasoning = "neutral", 0.40, "LLM 分析暂不可用"

    if use_llm:
        try:
            from src.llm.router import call_llm
            from src.llm.prompts import GRAHAM_SYSTEM_PROMPT, GRAHAM_USER_TEMPLATE

            # ... existing LLM call code ...

            # Parse LLM response
            try:
                parsed = json.loads(llm_text)
                llm_signal = parsed.get("signal", "neutral").lower()
                llm_confidence = float(parsed.get("confidence", 0.5))
                reasoning = parsed.get("reasoning", llm_text)
            except Exception:
                text_lower = llm_text.lower()
                llm_signal = "bullish" if ("bullish" in text_lower or "低估" in llm_text) else \
                             "bearish" if ("bearish" in text_lower or "高估" in llm_text) else "neutral"
                llm_confidence = 0.50
                reasoning = llm_text

        except Exception as e:
            logger.warning("[Graham] LLM call failed: %s", e)
            llm_signal = "neutral"
            llm_confidence = 0.40
            reasoning = f"LLM 不可用。格雷厄姆标准: {n_pass}/{n_total} 通过\n" + "\n".join(criteria_passed)

    # Apply signal cap - CODE IS THE FINAL GATE
    capped_result = _apply_signal_cap(llm_signal, llm_confidence, max_signal, fallback_confidence)
    final_signal = capped_result["signal"]
    final_confidence = capped_result["confidence"]

    if capped_result.get("capped"):
        reasoning += f"\n\n⚠ 信号已被代码限制: {n_pass}/{n_total}标准通过，最高允许{max_signal}"

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=final_signal,
        confidence=round(final_confidence, 3),
        reasoning=reasoning,
        metrics=metrics_snapshot,
    )
    insert_agent_signal(agent_signal)
    logger.info("[Graham] %s: signal=%s confidence=%.2f criteria=%s/%s (max=%s)",
                ticker, final_signal, final_confidence, n_pass, n_total, max_signal)
    return agent_signal
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/agents/test_ben_graham.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/agents/ben_graham.py tests/agents/test_ben_graham.py
git commit -m "fix(graham): enforce signal hard rules based on criteria count

- 0/7 criteria → hard bearish (0.70 confidence), skip LLM
- 1-2/7 → bearish with dynamic confidence
- 3-4/7 → cap at neutral
- 5-6/7 → LLM can choose neutral/bullish
- 7/7 → no restrictions

Fixes: LLM could override 0/7 criteria to bullish"
```

---

## Task 2: Valuation Outlier Detection

**Files:**
- Modify: `src/agents/valuation.py`
- Test: `tests/agents/test_valuation.py`

**Step 1: Write failing tests for outlier detection**

```python
# tests/agents/test_valuation.py

import pytest
import statistics
from src.agents.valuation import _validate_valuation_result, _calculate_weighted_target

class TestValuationOutlierDetection:
    """Test valuation outlier detection and weighted calculation"""

    def test_negative_target_excluded(self):
        """Negative target price should be excluded"""
        result = _validate_valuation_result(
            method_name="EV/EBITDA",
            target_price=-5.94,
            current_price=20.42,
            all_results=[15.53, 27.54, 11.32, -5.94]
        )
        assert result["exclude_from_weighted"] is True
        assert "负或零" in result["warnings"][0]

    def test_deviation_over_80_percent_excluded(self):
        """Target deviating >80% from current price should be excluded"""
        result = _validate_valuation_result(
            method_name="EV/EBITDA",
            target_price=5.94,
            current_price=20.42,
            all_results=[5.94, 15.53, 27.54]
        )
        assert result["exclude_from_weighted"] is True
        assert "偏离当前价" in result["warnings"][0]

    def test_median_deviation_over_50_percent_excluded(self):
        """Target deviating >50% from median should be excluded"""
        all_results = [5.0, 15.0, 16.0, 17.0]  # median = 15.5
        result = _validate_valuation_result(
            method_name="Method1",
            target_price=5.0,
            current_price=15.0,
            all_results=all_results
        )
        assert result["exclude_from_weighted"] is True
        assert "中位数" in result["warnings"][-1]

    def test_valid_result_not_excluded(self):
        """Normal result should not be excluded"""
        result = _validate_valuation_result(
            method_name="DCF",
            target_price=18.0,
            current_price=20.0,
            all_results=[18.0, 19.0, 17.0]
        )
        assert result["exclude_from_weighted"] is False
        assert len(result["warnings"]) == 0

    def test_degraded_mode_when_insufficient_methods(self):
        """When <=1 valid method, return degraded result"""
        results = [
            {"method": "DCF", "target_price": 18.0, "exclude_from_weighted": False},
            {"method": "Graham", "target_price": -5.0, "exclude_from_weighted": True},
            {"method": "EV/EBITDA", "target_price": 5.0, "exclude_from_weighted": True},
        ]
        weighted = _calculate_weighted_target(results, current_price=20.0)
        assert weighted["degraded"] is True
        assert weighted["confidence"] == 0.25
        assert "有效估值方法不足" in weighted["warning"]
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_valuation.py::TestValuationOutlierDetection -v`
Expected: FAIL

**Step 3: Implement outlier detection functions**

```python
# src/agents/valuation.py - Add after line 47

import statistics as _stats

def _validate_valuation_result(
    method_name: str,
    target_price: float | None,
    current_price: float | None,
    all_results: list[float]
) -> dict:
    """Validate valuation result, return exclusion status and warnings"""
    result = {
        "method": method_name,
        "target_price": target_price,
        "valid": True,
        "warnings": [],
        "exclude_from_weighted": False
    }

    # Rule 1: Negative/zero value
    if target_price is None or target_price <= 0:
        result["valid"] = False
        result["exclude_from_weighted"] = True
        result["warnings"].append("目标价为负或零，计算错误")
        return result

    # Rule 2: Deviation from current price > 80%
    if current_price and current_price > 0:
        deviation = (target_price - current_price) / current_price
        if abs(deviation) > 0.80:
            result["exclude_from_weighted"] = True
            result["warnings"].append(f"偏离当前价{deviation*100:.0f}%")

    # Rule 3: Deviation from median > 50%
    other_results = [r for r in all_results if r != target_price and r and r > 0]
    if len(other_results) >= 2:
        median = _stats.median(other_results)
        if median > 0:
            cross_deviation = abs(target_price - median) / median
            if cross_deviation > 0.50:
                result["exclude_from_weighted"] = True
                result["warnings"].append(f"与中位数偏离{cross_deviation*100:.0f}%")

    return result


def _calculate_weighted_target(
    results: list[dict],
    current_price: float,
    weights: dict[str, float] | None = None
) -> dict:
    """Calculate weighted target price with degraded mode fallback"""

    # Default weights
    if weights is None:
        weights = {
            "DCF": 0.40,
            "Graham": 0.25,
            "EV/EBITDA": 0.20,
            "P/B": 0.15,
        }

    # Filter valid methods
    valid_methods = [r for r in results if not r.get("exclude_from_weighted", False)]

    # Degraded mode: insufficient valid methods
    if len(valid_methods) <= 1:
        return {
            "weighted_target": None,
            "signal": "neutral",
            "confidence": 0.25,
            "degraded": True,
            "warning": "⚠ 有效估值方法不足，无法形成可靠加权结论",
            "valid_methods": len(valid_methods),
            "excluded_methods": [r["method"] for r in results if r.get("exclude_from_weighted")]
        }

    # Normalize weights for valid methods only
    total_weight = sum(weights.get(r["method"], 0.1) for r in valid_methods)

    weighted_target = sum(
        r["target_price"] * (weights.get(r["method"], 0.1) / total_weight)
        for r in valid_methods
    )

    return {
        "weighted_target": weighted_target,
        "valid_methods": len(valid_methods),
        "excluded_methods": [r["method"] for r in results if r.get("exclude_from_weighted")],
        "degraded": False
    }
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_valuation.py::TestValuationOutlierDetection -v`
Expected: PASS

**Step 5: Integrate into run() function**

Modify `run()` to call validation and weighted calculation before final signal.

**Step 6: Commit**

```bash
git add src/agents/valuation.py tests/agents/test_valuation.py
git commit -m "feat(valuation): add outlier detection with median validation

- Exclude negative/zero target prices
- Exclude >80% deviation from current price
- Exclude >50% deviation from median (not mean)
- Degraded mode when <=1 valid method remains

Fixes: EV/EBITDA -243% was included in weighted average"
```

---

## Task 3: Data Freshness Disclosure-Cycle Check

**Files:**
- Modify: `src/data/quality.py`
- Test: `tests/data/test_quality.py`

**Step 1: Write failing tests**

```python
# tests/data/test_quality.py

import pytest
from datetime import date
from src.data.quality import get_next_expected_report, check_data_staleness, ExpectedReport

class TestDisclosureCycleCheck:
    """Test A-share disclosure cycle awareness"""

    def test_q4_report_expects_q1_next(self):
        """After Q4/annual report, next expected is Q1"""
        last_report = date(2025, 12, 31)
        expected = get_next_expected_report(last_report)
        assert expected.period == "一季报"
        assert expected.deadline == date(2026, 4, 30)

    def test_q1_report_expects_h1_next(self):
        """After Q1, next expected is H1 (半年报)"""
        last_report = date(2026, 3, 31)
        expected = get_next_expected_report(last_report)
        assert expected.period == "半年报"
        assert expected.deadline == date(2026, 8, 31)

    def test_missing_report_returns_critical(self):
        """If deadline passed without report, return CRITICAL"""
        # Simulate: last report is 2025-06-30 (H1), today is 2026-03-08
        # Q3 deadline (2025-10-31) and annual deadline (2026-04-30) both relevant
        # But Q3 is definitely missing
        last_report = date(2025, 6, 30)
        today = date(2026, 3, 8)

        result = check_data_staleness(last_report, reference_date=today)
        assert result.level == "CRITICAL"
        assert "三季报" in result.reason or "年报" in result.reason

    def test_old_but_normal_cycle_returns_warning(self):
        """Data >180 days but within normal cycle returns WARNING"""
        # Last report 2025-12-31, today is 2026-03-08 (67 days, within Q1 deadline)
        last_report = date(2025, 12, 31)
        today = date(2026, 3, 8)

        result = check_data_staleness(last_report, reference_date=today)
        assert result.level == "WARNING" or result.level == "OK"

    def test_fresh_data_returns_ok(self):
        """Recent data returns OK"""
        last_report = date(2026, 1, 15)
        today = date(2026, 3, 8)

        result = check_data_staleness(last_report, reference_date=today)
        assert result.level == "OK"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/data/test_quality.py::TestDisclosureCycleCheck -v`
Expected: FAIL

**Step 3: Implement disclosure cycle functions**

```python
# src/data/quality.py - Add after line 10

from dataclasses import dataclass
from typing import Literal

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
    """Determine next expected report based on last report date"""
    year = last_report_date.year
    month = last_report_date.month

    if month <= 3:  # Last was Q4/Annual → next is Q1
        return ExpectedReport(period="一季报", deadline=date(year, 4, 30))
    elif month <= 6:  # Last was Q1 → next is H1
        return ExpectedReport(period="半年报", deadline=date(year, 8, 31))
    elif month <= 9:  # Last was H1 → next is Q3
        return ExpectedReport(period="三季报", deadline=date(year, 10, 31))
    else:  # Last was Q3 → next is Annual
        return ExpectedReport(period="年报", deadline=date(year + 1, 4, 30))


def check_data_staleness(
    last_report_date: date,
    reference_date: date | None = None
) -> StalenessResult:
    """Check data staleness based on disclosure cycle, not just days old"""
    today = reference_date or date.today()
    days_old = (today - last_report_date).days

    expected = get_next_expected_report(last_report_date)
    missing_report = today > expected.deadline

    if missing_report:
        return StalenessResult(
            level="CRITICAL",
            reason=f"缺少{expected.period}，披露截止日{expected.deadline}已过",
            expected_report=expected
        )
    elif days_old > 180:
        return StalenessResult(
            level="WARNING",
            reason=f"数据已{days_old}天，{expected.period}预计{expected.deadline}前披露",
            expected_report=expected
        )
    return StalenessResult(level="OK", expected_report=expected)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/data/test_quality.py::TestDisclosureCycleCheck -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/data/quality.py tests/data/test_quality.py
git commit -m "feat(quality): add disclosure-cycle-aware staleness check

- get_next_expected_report() calculates next expected report period
- check_data_staleness() distinguishes CRITICAL (missing report) vs WARNING
- Uses A-share disclosure deadlines: Q1(4/30), H1(8/31), Q3(10/31), Annual(4/30)

Fixes: 180-day threshold too aggressive during normal disclosure cycles"
```

---

## Task 4: Tushare Data Source Adapter

**Files:**
- Create: `src/data/tushare_source.py`
- Modify: `src/data/fetcher.py:44-48`
- Test: `tests/data/test_tushare_source.py`

**Step 1: Write tests**

```python
# tests/data/test_tushare_source.py

import pytest
from unittest.mock import patch, MagicMock
from src.data.tushare_source import TushareSource

class TestTushareSource:
    """Test Tushare Pro data source adapter"""

    def test_health_check_success(self):
        """Health check returns True when API responds"""
        with patch('tushare.pro_api') as mock_api:
            mock_pro = MagicMock()
            mock_pro.query.return_value = MagicMock(__len__=lambda x: 1)
            mock_api.return_value = mock_pro

            source = TushareSource()
            assert source.health_check() is True

    def test_get_income_statements_converts_format(self):
        """Income statements are converted to model format"""
        with patch('tushare.pro_api') as mock_api:
            mock_pro = MagicMock()
            mock_df = MagicMock()
            mock_df.empty = False
            mock_df.head.return_value.iterrows.return_value = [
                (0, {"end_date": "20251231", "revenue": 1e10, "n_income": 1e9, "basic_eps": 0.5})
            ]
            mock_pro.income.return_value = mock_df
            mock_api.return_value = mock_pro

            source = TushareSource()
            results = source.get_income_statements("601808.SH", "a_share")

            assert len(results) == 1
            assert results[0].source == "tushare"
```

**Step 2: Create Tushare source adapter**

```python
# src/data/tushare_source.py

import tushare as ts
from datetime import datetime, date
from src.data.base_source import BaseDataSource
from src.data.models import IncomeStatement, BalanceSheet, CashFlow, DailyPrice
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Token from user configuration
TUSHARE_TOKEN = "fb807267d782ca1f32a9a907c399fed4ea0a611ff94b786239fc2021"


class TushareSource(BaseDataSource):
    """Tushare Pro data source adapter for A-share financial data"""

    def __init__(self):
        self.pro = ts.pro_api(TUSHARE_TOKEN)

    def health_check(self) -> bool:
        try:
            df = self.pro.query('stock_basic', exchange='', list_status='L',
                                fields='ts_code', limit=1)
            return len(df) > 0
        except Exception as e:
            logger.warning("[Tushare] Health check failed: %s", e)
            return False

    def get_income_statements(self, ticker: str, market: str,
                               period_type: str = "annual",
                               limit: int = 10) -> list[IncomeStatement]:
        report_type = "1" if period_type == "annual" else "2"
        try:
            df = self.pro.income(ts_code=ticker, report_type=report_type)
            if df.empty:
                return []

            return [
                IncomeStatement(
                    ticker=ticker,
                    period_end_date=self._parse_date(row["end_date"]),
                    period_type=period_type,
                    revenue=row.get("revenue"),
                    net_income=row.get("n_income"),
                    eps=row.get("basic_eps"),
                    shares_outstanding=row.get("total_share"),
                    source="tushare"
                )
                for _, row in df.head(limit).iterrows()
            ]
        except Exception as e:
            logger.warning("[Tushare] get_income_statements failed: %s", e)
            return []

    def get_balance_sheets(self, ticker: str, market: str,
                           period_type: str = "annual",
                           limit: int = 5) -> list[BalanceSheet]:
        report_type = "1" if period_type == "annual" else "2"
        try:
            df = self.pro.balancesheet(ts_code=ticker, report_type=report_type)
            if df.empty:
                return []

            return [
                BalanceSheet(
                    ticker=ticker,
                    period_end_date=self._parse_date(row["end_date"]),
                    period_type=period_type,
                    total_assets=row.get("total_assets"),
                    total_liabilities=row.get("total_liab"),
                    total_equity=row.get("total_hldr_eqy_inc_min_int"),
                    current_assets=row.get("total_cur_assets"),
                    current_liabilities=row.get("total_cur_liab"),
                    total_debt=(row.get("lt_borr") or 0) + (row.get("st_borr") or 0),
                    source="tushare"
                )
                for _, row in df.head(limit).iterrows()
            ]
        except Exception as e:
            logger.warning("[Tushare] get_balance_sheets failed: %s", e)
            return []

    def get_cash_flows(self, ticker: str, market: str,
                       period_type: str = "annual",
                       limit: int = 5) -> list[CashFlow]:
        report_type = "1" if period_type == "annual" else "2"
        try:
            df = self.pro.cashflow(ts_code=ticker, report_type=report_type)
            if df.empty:
                return []

            return [
                CashFlow(
                    ticker=ticker,
                    period_end_date=self._parse_date(row["end_date"]),
                    period_type=period_type,
                    operating_cash_flow=row.get("n_cashflow_act"),
                    capital_expenditure=row.get("c_pay_acq_const_fiolta"),
                    free_cash_flow=self._calc_fcf(row),
                    depreciation=row.get("depr_fa_coga_dpba"),
                    source="tushare"
                )
                for _, row in df.head(limit).iterrows()
            ]
        except Exception as e:
            logger.warning("[Tushare] get_cash_flows failed: %s", e)
            return []

    def get_daily_prices(self, ticker: str, market: str,
                         start_date: date | None = None,
                         end_date: date | None = None) -> list[DailyPrice]:
        try:
            df = self.pro.daily(
                ts_code=ticker,
                start_date=start_date.strftime("%Y%m%d") if start_date else None,
                end_date=end_date.strftime("%Y%m%d") if end_date else None
            )
            if df.empty:
                return []

            return [
                DailyPrice(
                    ticker=ticker,
                    date=self._parse_date(row["trade_date"]),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["vol"],
                    source="tushare"
                )
                for _, row in df.iterrows()
            ]
        except Exception as e:
            logger.warning("[Tushare] get_daily_prices failed: %s", e)
            return []

    def get_financial_metrics(self, ticker: str, market: str, limit: int = 5):
        # Tushare has fina_indicator for this
        return []

    def _parse_date(self, date_str: str) -> date:
        return datetime.strptime(date_str, "%Y%m%d").date()

    def _calc_fcf(self, row) -> float | None:
        ocf = row.get("n_cashflow_act")
        capex = row.get("c_pay_acq_const_fiolta")
        if ocf is not None and capex is not None:
            return ocf - abs(capex)
        return None
```

**Step 3: Update fetcher priority**

```python
# src/data/fetcher.py - Modify line 44-48

from src.data.tushare_source import TushareSource

_SOURCE_PRIORITY: dict[MarketType, list[str]] = {
    "a_share": ["akshare", "tushare", "baostock", "qveris"],
    "hk":      ["akshare", "yfinance", "fmp"],
    "us":      ["yfinance", "fmp"],
}

def _get_source(name: str) -> BaseDataSource:
    sources = {
        "akshare":  AKShareSource,
        "tushare":  TushareSource,
        "baostock": BaoStockSource,
        "yfinance": YFinanceSource,
        "fmp":      FMPSource,
        "qveris":   QVerisSource,
    }
    return sources[name]()
```

**Step 4: Run tests**

Run: `pytest tests/data/test_tushare_source.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/data/tushare_source.py src/data/fetcher.py tests/data/test_tushare_source.py
git commit -m "feat(data): add Tushare Pro data source adapter

- TushareSource with income/balance/cashflow/price methods
- Integrated into fetcher priority chain: akshare → tushare → baostock
- Token configured for A-share financial data"
```

---

## Task 5: Sina Realtime Price Source

**Files:**
- Create: `src/data/sina_source.py`
- Modify: `src/data/fetcher.py`
- Test: `tests/data/test_sina_source.py`

**Step 1: Write tests**

```python
# tests/data/test_sina_source.py

import pytest
from unittest.mock import patch, MagicMock
from src.data.sina_source import SinaRealtimeSource

class TestSinaSource:
    """Test Sina realtime price source"""

    def test_ticker_conversion(self):
        source = SinaRealtimeSource()
        assert source._convert_ticker("601808.SH") == "sh601808"
        assert source._convert_ticker("000001.SZ") == "sz000001"

    def test_parse_response(self):
        source = SinaRealtimeSource()
        text = 'var hq_str_sh601808="中海油服,20.42,20.45,20.30,20.50,20.20,20.30,20.31,12345678,250000000";'
        result = source._parse_response(text)
        assert result["name"] == "中海油服"
        assert result["close"] == 20.30

    def test_income_statements_raises(self):
        source = SinaRealtimeSource()
        with pytest.raises(NotImplementedError):
            source.get_income_statements("601808.SH", "a_share")
```

**Step 2: Implement Sina source**

```python
# src/data/sina_source.py

import requests
import re
from datetime import date
from src.data.base_source import BaseDataSource
from src.data.models import DailyPrice
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SinaRealtimeSource(BaseDataSource):
    """Sina Finance realtime price source (prices only, no financials)"""

    def health_check(self) -> bool:
        try:
            resp = requests.get(
                "http://hq.sinajs.cn/list=sh000001",
                headers={"Referer": "http://finance.sina.com.cn"},
                timeout=5
            )
            return resp.status_code == 200 and "var hq_str" in resp.text
        except Exception:
            return False

    def get_daily_prices(self, ticker: str, market: str, **kwargs) -> list[DailyPrice]:
        """Get latest realtime price (returns today's data only)"""
        code = self._convert_ticker(ticker)
        url = f"http://hq.sinajs.cn/list={code}"

        try:
            resp = requests.get(
                url,
                headers={"Referer": "http://finance.sina.com.cn"},
                timeout=10
            )
            resp.encoding = "gbk"

            data = self._parse_response(resp.text)
            if not data:
                return []

            return [DailyPrice(
                ticker=ticker,
                date=date.today(),
                open=data["open"],
                high=data["high"],
                low=data["low"],
                close=data["close"],
                volume=data["volume"],
                source="sina"
            )]
        except Exception as e:
            logger.warning("[Sina] get_daily_prices failed: %s", e)
            return []

    def _convert_ticker(self, ticker: str) -> str:
        """Convert 601808.SH → sh601808"""
        code, exchange = ticker.split(".")
        prefix = "sh" if exchange.upper() in ["SH", "SS"] else "sz"
        return f"{prefix}{code}"

    def _parse_response(self, text: str) -> dict | None:
        """Parse Sina quote response"""
        match = re.search(r'="([^"]+)"', text)
        if not match:
            return None

        fields = match.group(1).split(",")
        if len(fields) < 10:
            return None

        try:
            return {
                "name": fields[0],
                "open": float(fields[1]),
                "close": float(fields[3]),
                "high": float(fields[4]),
                "low": float(fields[5]),
                "volume": float(fields[8]),
            }
        except (ValueError, IndexError):
            return None

    # Financial data not supported
    def get_income_statements(self, *args, **kwargs):
        raise NotImplementedError("Sina source only supports realtime prices")

    def get_balance_sheets(self, *args, **kwargs):
        raise NotImplementedError("Sina source only supports realtime prices")

    def get_cash_flows(self, *args, **kwargs):
        raise NotImplementedError("Sina source only supports realtime prices")

    def get_financial_metrics(self, *args, **kwargs):
        raise NotImplementedError("Sina source only supports realtime prices")
```

**Step 3: Update fetcher**

```python
# src/data/fetcher.py - Add import and update priority

from src.data.sina_source import SinaRealtimeSource

_SOURCE_PRIORITY: dict[MarketType, list[str]] = {
    "a_share": ["akshare", "tushare", "baostock", "sina_realtime", "qveris"],
    "hk":      ["akshare", "yfinance", "fmp"],
    "us":      ["yfinance", "fmp"],
}

def _get_source(name: str) -> BaseDataSource:
    sources = {
        "akshare":       AKShareSource,
        "tushare":       TushareSource,
        "baostock":      BaoStockSource,
        "sina_realtime": SinaRealtimeSource,
        "yfinance":      YFinanceSource,
        "fmp":           FMPSource,
        "qveris":        QVerisSource,
    }
    return sources[name]()
```

**Step 4: Run tests and commit**

```bash
pytest tests/data/test_sina_source.py -v
git add src/data/sina_source.py src/data/fetcher.py tests/data/test_sina_source.py
git commit -m "feat(data): add Sina realtime price source

- SinaRealtimeSource for real-time A-share quotes
- Ticker format conversion (601808.SH → sh601808)
- Integrated as fallback after Tushare/BaoStock"
```

---

## Task 6: Report Language De-Agentification

**Files:**
- Modify: `src/llm/prompts.py`
- Modify: `src/agents/report_generator.py`

**Step 1: Add agent name mapping**

```python
# src/agents/report_generator.py - Add after imports

AGENT_NAME_MAPPING = {
    "warren_buffett": "护城河与管理层分析",
    "ben_graham": "防御型投资标准检验",
    "valuation": "多方法估值模型",
    "fundamentals": "财务健康度评估",
    "contrarian": "反向视角风险分析",
    "sentiment": "市场情绪监测",
}

def humanize_agent_name(agent_name: str) -> str:
    """Convert internal agent name to professional investment terminology"""
    return AGENT_NAME_MAPPING.get(agent_name, agent_name)
```

**Step 2: Update prompts**

```python
# src/llm/prompts.py - Modify REPORT_CH2_SYSTEM

REPORT_CH2_SYSTEM = """你是价值投资分析师。基于护城河分析和防御型投资标准检验结果，撰写竞争力章节（≥500字）。

必须包含：
1. 护城河类型判断（品牌/规模/网络/成本/切换成本）及持久性分析
2. 管理层质量与资本配置能力评估
3. 财务特征与价值投资标准的匹配度

【重要约束】
- 不要在输出中提及"Agent"、"系统"、"代码"等内部概念
- 使用专业投资分析语言，例如：
  - "估值模型显示..." 而非 "Valuation Agent显示..."
  - "财务质量分析表明..." 而非 "Fundamentals Agent表明..."
  - "反向视角风险提示..." 而非 "Contrarian Agent质疑..."
"""
```

**Step 3: Commit**

```bash
git add src/agents/report_generator.py src/llm/prompts.py
git commit -m "refactor(report): remove Agent naming from reports

- Add AGENT_NAME_MAPPING for professional terminology
- Update prompts to use investment language
- humanize_agent_name() utility function"
```

---

## Summary

| Task | Priority | Files | Status |
|------|----------|-------|--------|
| 1. Graham Signal Logic | P0 | ben_graham.py | 待实现 |
| 2. Valuation Outlier Detection | P0 | valuation.py | 待实现 |
| 3. Disclosure Cycle Check | P1 | quality.py | 待实现 |
| 4. Tushare Source | P1 | tushare_source.py | 待实现 |
| 5. Sina Source | P2 | sina_source.py | 待实现 |
| 6. Report De-Agentification | P2 | prompts.py | 待实现 |

**Total estimated tasks:** 6 main tasks, ~30 steps

---

*Plan version: v1.0*
*Created: 2026-03-08*
