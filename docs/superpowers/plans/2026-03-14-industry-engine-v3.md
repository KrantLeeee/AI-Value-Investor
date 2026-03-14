# V3.0 Industry Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform industry classification from keyword-matching to financial characteristic analysis using a three-layer funnel architecture.

**Architecture:** Hard Rules (zero-cost, ~40% coverage) → LLM Dynamic Routing (cached, ~50% coverage) → Safe Fallback (never-fail, ~10%). Feature flags enable gradual migration with parallel comparison mode.

**Tech Stack:** Python 3.12, Pydantic V2, SQLite, DeepSeek-Reasoner LLM, pytest

**Spec Document:** `docs/superpowers/specs/2026-03-14-industry-engine-v3-design.md`

---

## File Structure

### New Files
| Path | Responsibility |
|------|----------------|
| `src/data/balance_sheet_scanner.py` | Extract industry flags from balance sheet item names |
| `src/agents/valuation_config.py` | `ValuationConfig` Pydantic model with weight normalization |
| `src/agents/industry_engine.py` | Three-layer funnel: hard rules → LLM → fallback |
| `tests/test_industry_engine.py` | Unit tests for all engine components |

### Modified Files
| Path | Changes |
|------|---------|
| `src/data/models.py:48-60` | Add 5 new fields to `BalanceSheet` (after `book_value_per_share`) |
| `src/data/database.py:61-77,247-269` | Add new columns to schema and upsert |
| `src/data/akshare_source.py` | Integrate balance sheet scanner into `get_balance_sheets()` method |
| `src/llm/prompts.py` | Add `INDUSTRY_ROUTING_*` prompt templates |
| `config/llm_config.yaml` | Add `industry_routing` task config |
| `src/utils/config.py` | Add `get_feature_flags()` function |
| `src/agents/valuation.py` | Integrate new engine via feature flag |

---

## Chunk 1: Data Layer Foundation

### Task 1.1: Create Balance Sheet Scanner

**Files:**
- Create: `src/data/balance_sheet_scanner.py`
- Test: `tests/test_industry_engine.py`

- [ ] **Step 1: Write the failing test for bank detection**

```python
# tests/test_industry_engine.py
"""Unit tests for V3.0 Industry Engine."""

import pytest


class TestBalanceSheetScanner:
    """Tests for balance_sheet_scanner.py."""

    def test_bank_detection_with_loan_loss_provision(self):
        """Bank balance sheet items trigger has_loan_loss_provision."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        bank_items = [
            "货币资金", "发放贷款及垫款", "贷款损失准备",
            "吸收存款", "向中央银行借款", "总资产"
        ]
        flags = extract_industry_flags(bank_items)
        assert flags["has_loan_loss_provision"] is True
        assert flags["has_insurance_reserve"] is False

    def test_non_bank_no_false_positive(self):
        """Normal company balance sheet should not trigger bank flag."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        normal_items = [
            "货币资金", "存货", "固定资产", "应付账款", "总资产"
        ]
        flags = extract_industry_flags(normal_items)
        assert flags["has_loan_loss_provision"] is False
        assert flags["has_insurance_reserve"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_industry_engine.py::TestBalanceSheetScanner::test_bank_detection_with_loan_loss_provision -v`
Expected: FAIL with "ModuleNotFoundError" or "ImportError"

- [ ] **Step 3: Write minimal implementation**

```python
# src/data/balance_sheet_scanner.py
"""Balance sheet item scanner — extract industry flags from column names.

Scans raw balance sheet column names to detect industry-specific characteristics:
- Banks: loan loss provisions, interbank deposits
- Insurance: insurance reserves, premium income
"""

BANK_KEYWORDS = [
    "贷款和垫款", "发放贷款及垫款", "吸收存款", "向中央银行借款",
    "贷款损失准备", "贷款减值准备", "应收款项类投资", "存放同业款项",
    "拆出资金", "买入返售金融资产", "应付债券",
]

INSURANCE_KEYWORDS = [
    "未到期责任准备金", "未决赔款准备金", "寿险责任准备金",
    "长期健康险责任准备金", "保户储金及投资款", "保费收入",
    "应付赔付款", "应付保单红利",
]


def extract_industry_flags(raw_balance_sheet_items: list[str]) -> dict[str, bool]:
    """
    Scan balance sheet column names and extract industry flags.

    Args:
        raw_balance_sheet_items: List of column names from balance sheet DataFrame

    Returns:
        dict with has_loan_loss_provision, has_insurance_reserve booleans
    """
    flags = {
        "has_loan_loss_provision": False,
        "has_insurance_reserve": False,
    }

    if not raw_balance_sheet_items:
        return flags

    all_items_str = " ".join(raw_balance_sheet_items)

    # Bank: require >= 2 keyword matches (avoid false positives)
    bank_hits = sum(1 for kw in BANK_KEYWORDS if kw in all_items_str)
    flags["has_loan_loss_provision"] = bank_hits >= 2

    # Insurance: require >= 2 keyword matches
    insurance_hits = sum(1 for kw in INSURANCE_KEYWORDS if kw in all_items_str)
    flags["has_insurance_reserve"] = insurance_hits >= 2

    return flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_industry_engine.py::TestBalanceSheetScanner -v`
Expected: PASS

- [ ] **Step 5: Add insurance detection test**

```python
# Add to tests/test_industry_engine.py TestBalanceSheetScanner class

    def test_insurance_detection_with_reserves(self):
        """Insurance balance sheet items trigger has_insurance_reserve."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        insurance_items = [
            "货币资金", "未到期责任准备金", "寿险责任准备金",
            "保户储金及投资款", "总资产"
        ]
        flags = extract_industry_flags(insurance_items)
        assert flags["has_insurance_reserve"] is True
        assert flags["has_loan_loss_provision"] is False

    def test_empty_input_returns_false_flags(self):
        """Empty input should return all False flags."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        flags = extract_industry_flags([])
        assert flags["has_loan_loss_provision"] is False
        assert flags["has_insurance_reserve"] is False

    def test_single_keyword_not_enough(self):
        """Single keyword match is not enough to trigger flag (avoid false positives)."""
        from src.data.balance_sheet_scanner import extract_industry_flags

        # Only one bank keyword
        items = ["货币资金", "贷款损失准备", "固定资产"]
        flags = extract_industry_flags(items)
        assert flags["has_loan_loss_provision"] is False
```

- [ ] **Step 6: Run all scanner tests**

Run: `pytest tests/test_industry_engine.py::TestBalanceSheetScanner -v`
Expected: PASS (all 5 tests)

- [ ] **Step 7: Commit**

```bash
git add src/data/balance_sheet_scanner.py tests/test_industry_engine.py
git commit -m "feat(v3): add balance sheet scanner for bank/insurance detection"
```

---

### Task 1.2: Extend BalanceSheet Model

**Files:**
- Modify: `src/data/models.py:48-61`
- Test: `tests/test_industry_engine.py`

- [ ] **Step 1: Write test for new fields**

```python
# Add to tests/test_industry_engine.py

class TestBalanceSheetModel:
    """Tests for BalanceSheet model V3 fields."""

    def test_balance_sheet_has_new_v3_fields(self):
        """BalanceSheet model should have V3 industry detection fields."""
        from datetime import date
        from src.data.models import BalanceSheet

        bs = BalanceSheet(
            ticker="601398.SH",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            total_assets=10_000_000_000,
            inventory=500_000_000,
            advance_receipts=200_000_000,
            fixed_assets=1_000_000_000,
            has_loan_loss_provision=True,
            has_insurance_reserve=False,
            source="test",
        )
        assert bs.inventory == 500_000_000
        assert bs.advance_receipts == 200_000_000
        assert bs.fixed_assets == 1_000_000_000
        assert bs.has_loan_loss_provision is True
        assert bs.has_insurance_reserve is False

    def test_balance_sheet_v3_fields_default_none(self):
        """V3 fields should default to None/False for backward compatibility."""
        from datetime import date
        from src.data.models import BalanceSheet

        bs = BalanceSheet(
            ticker="000001.SZ",
            period_end_date=date(2024, 12, 31),
            period_type="annual",
            source="test",
        )
        assert bs.inventory is None
        assert bs.advance_receipts is None
        assert bs.fixed_assets is None
        assert bs.has_loan_loss_provision is False
        assert bs.has_insurance_reserve is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_industry_engine.py::TestBalanceSheetModel -v`
Expected: FAIL with "unexpected keyword argument 'inventory'"

- [ ] **Step 3: Add new fields to BalanceSheet model**

Edit `src/data/models.py` lines 48-61, add after `book_value_per_share`:

```python
class BalanceSheet(BaseModel):
    ticker: str
    period_end_date: date
    period_type: PeriodType
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    book_value_per_share: float | None = None
    # V3.0: Industry detection fields
    inventory: float | None = None
    advance_receipts: float | None = None
    fixed_assets: float | None = None
    has_loan_loss_provision: bool = False
    has_insurance_reserve: bool = False
    source: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_industry_engine.py::TestBalanceSheetModel -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/models.py tests/test_industry_engine.py
git commit -m "feat(v3): add V3 industry detection fields to BalanceSheet model"
```

---

### Task 1.3: Update Database Schema

**Files:**
- Modify: `src/data/database.py:61-77` (SCHEMA_SQL)
- Modify: `src/data/database.py:247-270` (upsert_balance_sheets)
- Modify: `src/data/database.py:193-198` (init_db)

- [ ] **Step 1: Update SCHEMA_SQL with new columns**

Edit `src/data/database.py` lines 61-77, add new columns:

```python
CREATE TABLE IF NOT EXISTS balance_sheets (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker               TEXT NOT NULL,
    period_end_date      TEXT NOT NULL,
    period_type          TEXT NOT NULL,
    total_assets         REAL,
    total_liabilities    REAL,
    total_equity         REAL,
    current_assets       REAL,
    current_liabilities  REAL,
    cash_and_equivalents REAL,
    total_debt           REAL,
    book_value_per_share REAL,
    inventory            REAL,
    advance_receipts     REAL,
    fixed_assets         REAL,
    has_loan_loss_provision INTEGER DEFAULT 0,
    has_insurance_reserve   INTEGER DEFAULT 0,
    source               TEXT NOT NULL,
    updated_at           TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, period_end_date, period_type)
);
```

- [ ] **Step 2: Add migration function**

Add after line 173 (before `@contextmanager`):

```python
def _run_v3_migrations(conn: sqlite3.Connection) -> None:
    """
    V3.0 Schema migration: add industry detection fields to balance_sheets.

    SQLite supports ALTER TABLE ADD COLUMN but not IF NOT EXISTS.
    Use PRAGMA table_info to check column existence.
    """
    cursor = conn.execute("PRAGMA table_info(balance_sheets)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    migrations = [
        ("inventory", "REAL"),
        ("advance_receipts", "REAL"),
        ("fixed_assets", "REAL"),
        ("has_loan_loss_provision", "INTEGER DEFAULT 0"),
        ("has_insurance_reserve", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_type in migrations:
        if col_name not in existing_columns:
            conn.execute(f"ALTER TABLE balance_sheets ADD COLUMN {col_name} {col_type}")
            logger.info("[Migration] Added column: balance_sheets.%s", col_name)
```

- [ ] **Step 3: Update init_db to call migration**

Edit `src/data/database.py` init_db function:

```python
def init_db(db_path: Path | None = None) -> None:
    """Create all tables and indexes if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _run_v3_migrations(conn)  # V3.0 migration
    logger.info("Database initialised at %s", db_path or get_db_path())
```

- [ ] **Step 4: Update upsert_balance_sheets with new columns**

Edit `src/data/database.py` upsert_balance_sheets function:

```python
def upsert_balance_sheets(sheets: list[BalanceSheet]) -> int:
    if not sheets:
        return 0
    sql = """
        INSERT INTO balance_sheets
            (ticker, period_end_date, period_type, total_assets, total_liabilities, total_equity,
             current_assets, current_liabilities, cash_and_equivalents, total_debt,
             book_value_per_share, inventory, advance_receipts, fixed_assets,
             has_loan_loss_provision, has_insurance_reserve, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, period_end_date, period_type) DO UPDATE SET
            total_assets=excluded.total_assets, total_equity=excluded.total_equity,
            total_liabilities=excluded.total_liabilities,
            inventory=excluded.inventory, advance_receipts=excluded.advance_receipts,
            fixed_assets=excluded.fixed_assets,
            has_loan_loss_provision=excluded.has_loan_loss_provision,
            has_insurance_reserve=excluded.has_insurance_reserve,
            source=excluded.source, updated_at=datetime('now')
    """
    rows = [
        (s.ticker, str(s.period_end_date), s.period_type, s.total_assets, s.total_liabilities,
         s.total_equity, s.current_assets, s.current_liabilities, s.cash_and_equivalents,
         s.total_debt, s.book_value_per_share,
         s.inventory, s.advance_receipts, s.fixed_assets,
         1 if s.has_loan_loss_provision else 0,
         1 if s.has_insurance_reserve else 0,
         s.source)
        for s in sheets
    ]
    with get_connection() as conn:
        conn.executemany(sql, rows)
    return len(rows)
```

- [ ] **Step 5: Run existing database tests to verify no regression**

Run: `pytest tests/test_data_sources.py -v -k "balance" --no-header`
Expected: PASS (or skip if tests don't exist)

- [ ] **Step 6: Commit**

```bash
git add src/data/database.py
git commit -m "feat(v3): add V3 columns to balance_sheets schema with migration"
```

---

### Task 1.4: Integrate Scanner into AKShare Source

**Files:**
- Modify: `src/data/akshare_source.py` (get_balance_sheets method, around lines 340-420)

**Context:** The existing `get_balance_sheets()` method has a helper `_get_val(row, *col_names)` defined around line 396. We will:
1. Add import at top of file
2. Call `extract_industry_flags()` BEFORE the row iteration loop
3. Inside the loop, extract new field values using the existing `_get_val` helper pattern
4. Add new fields to the `BalanceSheet()` constructor call

- [ ] **Step 1: Add import for balance sheet scanner**

Add at top of `src/data/akshare_source.py` after line 45 (after other src imports):

```python
from src.data.balance_sheet_scanner import extract_industry_flags
```

- [ ] **Step 2: Add industry flag extraction before row loop**

In `get_balance_sheets()` method, AFTER the line `df = ak.stock_financial_debt_ths(...)` (around line 390) and BEFORE the `for _, row in df.iterrows():` loop, add:

```python
        # V3.0: Extract industry flags from column names (once per DataFrame)
        raw_column_names = list(df.columns)
        industry_flags = extract_industry_flags(raw_column_names)
```

- [ ] **Step 3: Add new field extraction inside the row loop**

Inside the `for _, row in df.iterrows():` loop, AFTER the existing field extractions and BEFORE the `results.append(BalanceSheet(...))` call, add:

```python
            # V3.0: Extract new fields for industry detection
            inventory = _get_val(row, "*存货", "存货")

            # Prefer 合同负债 (new standard) over 预收款项 (old standard)
            advance_receipts = _get_val(row, "*合同负债", "合同负债", "*预收款项", "预收款项")

            # Fixed assets = 固定资产 + 在建工程 (for asset-heavy detection)
            fixed_assets_base = _get_val(row, "*固定资产", "固定资产") or 0
            construction_in_progress = _get_val(row, "*在建工程", "在建工程") or 0
            fixed_assets = (fixed_assets_base + construction_in_progress) or None
```

- [ ] **Step 4: Update BalanceSheet constructor**

In the `results.append(BalanceSheet(...))` call, add new fields before `source=self.source_name`:

```python
            results.append(BalanceSheet(
                # ... existing fields unchanged ...
                book_value_per_share=bvps,
                # V3.0: New fields
                inventory=inventory,
                advance_receipts=advance_receipts,
                fixed_assets=fixed_assets,
                has_loan_loss_provision=industry_flags["has_loan_loss_provision"],
                has_insurance_reserve=industry_flags["has_insurance_reserve"],
                source=self.source_name,
            ))
```

- [ ] **Step 3: Verify import works**

Run: `python -c "from src.data.akshare_source import AKShareSource; print('OK')"`
Expected: "OK"

- [ ] **Step 4: Commit**

```bash
git add src/data/akshare_source.py
git commit -m "feat(v3): integrate balance sheet scanner into AKShare source"
```

---

## Chunk 2: Core Engine Implementation

### Task 2.1: Create ValuationConfig Model

**Files:**
- Create: `src/agents/valuation_config.py`
- Test: `tests/test_industry_engine.py`

- [ ] **Step 1: Write test for ValuationConfig weight normalization**

```python
# Add to tests/test_industry_engine.py

class TestValuationConfig:
    """Tests for ValuationConfig Pydantic model."""

    def test_method_importance_converts_to_weights(self):
        """method_importance scores should auto-normalize to weights."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="test_regime",
            primary_methods=["pe", "ev_ebitda", "dcf"],
            method_importance={"pe": 8, "ev_ebitda": 5, "dcf": 2},
            source="llm",
        )
        # Total = 15, so pe=8/15, ev_ebitda=5/15, dcf=2/15
        assert abs(config.weights["pe"] - 0.5333) < 0.01
        assert abs(config.weights["ev_ebitda"] - 0.3333) < 0.01
        assert abs(sum(config.weights.values()) - 1.0) < 0.001

    def test_weights_sum_to_one(self):
        """Weights should always sum to exactly 1.0."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="test",
            primary_methods=["pe", "pb", "dcf"],
            method_importance={"pe": 3, "pb": 3, "dcf": 3},
            source="llm",
        )
        assert sum(config.weights.values()) == 1.0

    def test_explicit_weights_used_directly(self):
        """If weights provided, method_importance is ignored."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="bank",
            primary_methods=["pb_roe", "ddm"],
            weights={"pb_roe": 0.6, "ddm": 0.4},
            method_importance={"pb_roe": 1, "ddm": 9},  # Should be ignored
            source="hard_rule",
        )
        assert config.weights == {"pb_roe": 0.6, "ddm": 0.4}

    def test_empty_weights_and_importance_uses_equal_distribution(self):
        """No weights or importance → equal distribution."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="generic",
            primary_methods=["pe", "pb", "ev_ebitda"],
            source="fallback",
        )
        assert len(config.weights) == 3
        assert abs(sum(config.weights.values()) - 1.0) < 0.001

    def test_invalid_method_raises_error(self):
        """Invalid valuation method should raise ValueError."""
        from src.agents.valuation_config import ValuationConfig
        import pytest

        with pytest.raises(ValueError, match="非法估值方法"):
            ValuationConfig(
                regime="test",
                primary_methods=["invalid_method"],
                source="llm",
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_industry_engine.py::TestValuationConfig -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Create ValuationConfig model**

```python
# src/agents/valuation_config.py
"""ValuationConfig — unified output model for the three-layer industry engine.

This model represents the valuation framework configuration determined by:
1. Hard rules (bank, insurance, real_estate, etc.)
2. LLM dynamic routing (with method_importance scores)
3. Safe fallback (generic regime)

Key design decisions:
- method_importance (1-10 scale) is normalized to weights automatically
- Pydantic V2 @model_validator ensures cross-field consistency
- Floating-point tail-diff handling for exact weight sum of 1.0
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_METHODS = {
    "pe", "ev_ebitda", "dcf", "ps", "pb", "pb_roe", "ddm",
    "peg", "normalized_pe", "pe_moat", "ev_sales",
    "asset_replacement", "net_net", "graham_number",
}


class ValuationConfig(BaseModel):
    """Valuation framework configuration — output of three-layer funnel."""

    regime: str
    primary_methods: list[str]
    weights: dict[str, float] = {}
    method_importance: dict[str, int] = {}
    disabled_methods: list[str] = []
    exempt_scoring_metrics: list[str] = []
    scoring_mode: str = "standard"
    ev_ebitda_multiple_range: tuple[float, float] = (8.0, 12.0)
    pb_multiple_cap: float | None = None  # For real_estate regime (e.g., 0.5)

    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    source: Literal["hard_rule", "llm", "fallback"] = "llm"
    rationale: str = ""
    triggered_rules: list[str] = []

    @field_validator("primary_methods")
    @classmethod
    def methods_must_be_allowed(cls, v: list[str]) -> list[str]:
        invalid = set(v) - ALLOWED_METHODS
        if invalid:
            raise ValueError(f"非法估值方法: {invalid}")
        return v

    @model_validator(mode="after")
    def auto_normalize_weights(self) -> "ValuationConfig":
        """
        Auto-normalize weights from method_importance or equal distribution.

        Priority:
        1. weights already provided → use directly (hard_rule scenario)
        2. method_importance provided → normalize to weights (LLM scenario)
        3. neither → equal distribution (fallback scenario)
        """
        if self.weights:
            # Weights provided, ensure normalized
            total = sum(self.weights.values())
            if total > 0 and abs(total - 1.0) > 0.01:
                self.weights = {k: round(v / total, 4) for k, v in self.weights.items()}
            return self

        # Priority 1: Use method_importance from LLM
        if self.method_importance:
            total = sum(self.method_importance.values())
            if total == 0:
                raise ValueError("method_importance 不能全为 0")

            normalized = {k: round(v / total, 4) for k, v in self.method_importance.items()}

            # Handle float tail-diff for exact 1.0 sum
            keys = list(normalized.keys())
            if keys:
                current_sum = sum(list(normalized.values())[:-1])
                normalized[keys[-1]] = round(1.0 - current_sum, 4)

            self.weights = normalized
            return self

        # Priority 2: Equal distribution based on primary_methods
        if self.primary_methods:
            n = len(self.primary_methods)
            base_weight = round(1.0 / n, 4)
            self.weights = {m: base_weight for m in self.primary_methods}

            # Handle tail-diff
            keys = list(self.weights.keys())
            if keys:
                current_sum = sum(list(self.weights.values())[:-1])
                self.weights[keys[-1]] = round(1.0 - current_sum, 4)

        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_industry_engine.py::TestValuationConfig -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/valuation_config.py tests/test_industry_engine.py
git commit -m "feat(v3): add ValuationConfig model with weight normalization"
```

---

### Task 2.2: Implement Hard Rules Detection

**Files:**
- Modify: `src/agents/industry_engine.py`
- Test: `tests/test_industry_engine.py`

- [ ] **Step 1: Write tests for hard rule detection**

```python
# Add to tests/test_industry_engine.py

class TestHardRuleDetection:
    """Tests for detect_special_regime() hard rules."""

    def test_bank_detection(self):
        """High DE + loan loss provision → bank regime."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 12.0,
            "has_loan_loss_provision": True,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "bank"
        assert result.confidence >= 0.90

    def test_insurance_detection(self):
        """DE > 4 + insurance reserve → insurance regime."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 6.0,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": True,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "insurance"
        assert result.confidence >= 0.90

    def test_real_estate_detection(self):
        """High inventory + advance + asset-light → real_estate regime."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "total_assets": 100_000_000_000,
            "inventory": 50_000_000_000,      # 50%
            "advance_receipts": 15_000_000_000,  # 15%
            "fixed_assets": 3_000_000_000,    # 3% (asset-light)
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "real_estate"

    def test_heavy_manufacturing_not_real_estate(self):
        """High inventory + advance but heavy fixed assets → NOT real_estate."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "total_assets": 100_000_000_000,
            "inventory": 45_000_000_000,      # 45%
            "advance_receipts": 12_000_000_000,  # 12%
            "fixed_assets": 30_000_000_000,   # 30% (heavy assets)
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        # Should NOT match real_estate due to high fixed_assets
        assert result is None or result.regime != "real_estate"

    def test_brand_moat_detection(self):
        """High gross margin + high ROE + stable FCF → brand_moat."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "gross_margin": 75.0,
            "roe_5yr_avg": 22.0,
            "fcf_positive_years": 5,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is not None
        assert result.regime == "brand_moat"

    def test_no_match_returns_none(self):
        """Normal company metrics should return None (go to LLM)."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 1.5,
            "gross_margin": 35.0,
            "roe_5yr_avg": 12.0,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        result = detect_special_regime(metrics, {})
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_industry_engine.py::TestHardRuleDetection -v`
Expected: FAIL with "cannot import name 'detect_special_regime'"

- [ ] **Step 3: Create industry_engine.py with hard rules**

```python
# src/agents/industry_engine.py
"""V3.0 Industry Engine — three-layer funnel for valuation config routing.

Architecture:
1. Hard Rules (zero-cost): Bank, Insurance, Real Estate, Distressed, Brand Moat, Pharma
2. LLM Dynamic Routing (cached): DeepSeek-Reasoner with method_importance scoring
3. Safe Fallback (never-fail): Generic regime with balanced weights

Usage:
    from src.agents.industry_engine import get_valuation_config

    config = get_valuation_config(ticker, company_info, metrics)
"""

from dataclasses import dataclass

from src.agents.valuation_config import ValuationConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Special Regime Configurations ────────────────────────────────────────────

SPECIAL_REGIME_CONFIGS = {
    "bank": {
        "primary_methods": ["pb_roe", "ddm"],
        "weights": {"pb_roe": 0.6, "ddm": 0.4},
        "disabled_methods": ["ev_ebitda", "graham_number", "dcf"],
        "exempt_scoring_metrics": ["debt_equity", "current_ratio", "fcf_ni"],
        "scoring_mode": "financial",
    },
    "insurance": {
        "primary_methods": ["pb_roe", "ddm", "pe"],
        "weights": {"pb_roe": 0.4, "ddm": 0.35, "pe": 0.25},
        "disabled_methods": ["ev_ebitda", "graham_number"],
        "exempt_scoring_metrics": ["debt_equity", "current_ratio", "fcf_ni"],
        "scoring_mode": "financial",
    },
    "real_estate": {
        "primary_methods": ["pb"],
        "weights": {"pb": 1.0},
        "disabled_methods": ["graham_number", "ev_ebitda", "dcf"],
        "scoring_mode": "standard",
        "pb_multiple_cap": 0.5,  # Cap P/B at 0.5 for distressed real estate
    },
    "distressed": {
        "primary_methods": ["ev_sales", "net_net", "asset_replacement"],
        "weights": {"ev_sales": 0.5, "net_net": 0.3, "asset_replacement": 0.2},
        "disabled_methods": ["pe", "graham_number", "dcf", "pb_roe"],
        "scoring_mode": "distressed",
    },
    "brand_moat": {
        "primary_methods": ["pe_moat", "dcf", "ev_ebitda"],
        "weights": {"pe_moat": 0.5, "dcf": 0.3, "ev_ebitda": 0.2},
        "disabled_methods": ["graham_number"],
        "ev_ebitda_multiple_range": (15.0, 25.0),
    },
    "pharma_innovative": {
        "primary_methods": ["ps", "ev_sales"],
        "weights": {"ps": 0.6, "ev_sales": 0.4},
        "disabled_methods": ["pe", "graham_number", "ev_ebitda"],
        "exempt_scoring_metrics": ["fcf_ni", "net_margin"],
    },
}

PIPELINE_KEYWORDS = [
    "临床", "管线", "适应症", "pipeline",
    "IND", "NDA", "FDA", "NMPA", "BLA",
    "一期", "二期", "三期", "Phase",
    "创新药", "生物药", "单抗", "双抗",
]


@dataclass
class SpecialRegimeResult:
    """Result from hard rule detection."""
    regime: str
    confidence: float
    triggered_rules: list[str]
    rationale: str


def detect_special_regime(
    metrics: dict,
    company_info: dict,
) -> SpecialRegimeResult | None:
    """
    Layer 1: Hard rule detection for special regimes.

    Returns SpecialRegimeResult if a hard rule matches, None otherwise.
    """
    # Extract common metrics with safe defaults
    de_ratio = metrics.get("de_ratio") or 0
    has_loan_loss = metrics.get("has_loan_loss_provision", False)
    has_insurance = metrics.get("has_insurance_reserve", False)
    is_financial = has_loan_loss or has_insurance

    total_assets = metrics.get("total_assets") or 1
    inventory = metrics.get("inventory") or 0
    advance = metrics.get("advance_receipts") or 0
    fixed_assets = metrics.get("fixed_assets") or 0

    inventory_ratio = inventory / total_assets if total_assets > 0 else 0
    advance_ratio = advance / total_assets if total_assets > 0 else 0
    fixed_assets_ratio = fixed_assets / total_assets if total_assets > 0 else 0

    gross_margin = metrics.get("gross_margin") or 0
    roe_5yr = metrics.get("roe_5yr_avg") or 0
    fcf_years = metrics.get("fcf_positive_years") or 0
    rd_ratio = metrics.get("rd_expense_ratio") or 0
    net_margin = metrics.get("net_margin") or 0

    # Rule priority: Bank > Insurance > Real Estate > Distressed > Brand Moat > Pharma

    # Rule 1: Bank (DE > 8 AND has_loan_loss_provision)
    if de_ratio > 8 and has_loan_loss:
        return SpecialRegimeResult(
            regime="bank",
            confidence=0.95,
            triggered_rules=["de_ratio > 8", "has_loan_loss_provision"],
            rationale=f"DE={de_ratio:.1f}x with loan loss provisions",
        )

    # Rule 2: Insurance (DE > 4 AND has_insurance_reserve)
    if de_ratio > 4 and has_insurance:
        return SpecialRegimeResult(
            regime="insurance",
            confidence=0.92,
            triggered_rules=["de_ratio > 4", "has_insurance_reserve"],
            rationale=f"DE={de_ratio:.1f}x with insurance reserves",
        )

    # Rule 3: Real Estate (inventory > 40% AND advance > 10% AND fixed_assets < 10% AND NOT financial)
    if (inventory_ratio > 0.40 and advance_ratio > 0.10 and
            fixed_assets_ratio < 0.10 and not is_financial):
        return SpecialRegimeResult(
            regime="real_estate",
            confidence=0.90,
            triggered_rules=[
                f"inventory_ratio={inventory_ratio:.1%}",
                f"advance_ratio={advance_ratio:.1%}",
                f"fixed_assets_ratio={fixed_assets_ratio:.1%} (asset-light)",
            ],
            rationale="High inventory + advance receipts with light fixed assets (developer pattern)",
        )

    # Rule 4: Distressed (negative margins or ROE for 2+ years)
    margin_3yr = metrics.get("net_margin_3yr_avg")
    roe_3yr = metrics.get("roe_3yr_avg")
    loss_years = metrics.get("consecutive_loss_years") or 0
    if not is_financial and loss_years >= 2:
        if (margin_3yr is not None and margin_3yr < -10) or (roe_3yr is not None and roe_3yr < -10):
            return SpecialRegimeResult(
                regime="distressed",
                confidence=0.85,
                triggered_rules=[
                    f"loss_years={loss_years}",
                    f"margin_3yr={margin_3yr}" if margin_3yr else "",
                ],
                rationale="Consecutive losses with negative profitability trend",
            )

    # Rule 5: Brand Moat (gross_margin > 70% AND roe_5yr > 18% AND fcf_positive >= 4)
    if gross_margin > 70 and roe_5yr > 18 and fcf_years >= 4 and not is_financial:
        return SpecialRegimeResult(
            regime="brand_moat",
            confidence=0.88,
            triggered_rules=[
                f"gross_margin={gross_margin:.1f}%",
                f"roe_5yr={roe_5yr:.1f}%",
                f"fcf_years={fcf_years}",
            ],
            rationale="Consistently high margins and returns indicate durable brand moat",
        )

    # Rule 6: Pharma Innovative (rd_ratio > 30% AND net_margin < 5% AND pipeline keywords)
    business_desc = company_info.get("business_description", "")
    has_pipeline = any(kw in business_desc for kw in PIPELINE_KEYWORDS)
    if rd_ratio > 30 and net_margin < 5 and has_pipeline and not is_financial:
        return SpecialRegimeResult(
            regime="pharma_innovative",
            confidence=0.82,
            triggered_rules=[
                f"rd_ratio={rd_ratio:.1f}%",
                f"net_margin={net_margin:.1f}%",
                "has_pipeline_keywords",
            ],
            rationale="High R&D with low profits and pipeline keywords suggests innovative pharma",
        )

    return None


def _build_valuation_config_from_regime(
    regime: str,
    confidence: float,
    triggered_rules: list[str],
    rationale: str,
) -> ValuationConfig:
    """Build ValuationConfig from a detected special regime."""
    config_data = SPECIAL_REGIME_CONFIGS.get(regime, {})
    return ValuationConfig(
        regime=regime,
        primary_methods=config_data.get("primary_methods", ["ev_ebitda", "pe", "pb"]),
        weights=config_data.get("weights", {}),
        disabled_methods=config_data.get("disabled_methods", []),
        exempt_scoring_metrics=config_data.get("exempt_scoring_metrics", []),
        scoring_mode=config_data.get("scoring_mode", "standard"),
        ev_ebitda_multiple_range=config_data.get("ev_ebitda_multiple_range", (8.0, 12.0)),
        pb_multiple_cap=config_data.get("pb_multiple_cap"),  # For real_estate
        confidence=confidence,
        source="hard_rule",
        rationale=rationale,
        triggered_rules=triggered_rules,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_industry_engine.py::TestHardRuleDetection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/industry_engine.py tests/test_industry_engine.py
git commit -m "feat(v3): implement hard rule detection for special regimes"
```

---

### Task 2.3: Implement LLM Routing Layer

**Files:**
- Modify: `src/agents/industry_engine.py`
- Modify: `src/llm/prompts.py`
- Modify: `config/llm_config.yaml`
- Test: `tests/test_industry_engine.py`

- [ ] **Step 1: Add LLM prompts**

Add to `src/llm/prompts.py`:

```python
# ── Industry Routing (V3.0) ──────────────────────────────────────────────────

INDUSTRY_ROUTING_SYSTEM_PROMPT = """你是 A 股估值方法专家。根据公司描述和财务特征，选择最适合的估值框架。

## 可用估值方法

| 方法 | 适用场景 |
|-----|---------|
| pe | 稳定盈利企业 |
| ev_ebitda | 资本密集型、有折旧摊销的企业 |
| dcf | 现金流稳定、可预测的企业 |
| ps | 亏损但有收入的成长企业 |
| pb | 资产驱动型企业 |
| pb_roe | 金融股（银行、保险） |
| ddm | 高分红稳定企业 |
| peg | 高速成长股 |
| normalized_pe | 周期股（使用周期调整盈利） |
| pe_moat | 品牌消费股（护城河溢价） |
| ev_sales | 亏损成长股 |

## 输出格式（严格 JSON）

```json
{
  "regime": "行业体系名称（如 tech_saas, consumer_brand, cyclical_materials）",
  "primary_methods": ["方法1", "方法2"],
  "method_importance": {"方法1": 8, "方法2": 5},
  "disabled_methods": ["不适用的方法"],
  "scoring_mode": "standard 或 cycle_adjusted",
  "ev_ebitda_multiple_range": [低倍数, 高倍数],
  "rationale": "简要选择理由（1-2句）"
}
```

## 注意事项
- method_importance 使用 1-10 分制表示重要程度，系统会自动归一化为权重
- primary_methods 最多选 3 个，按重要性排序
- 如果公司亏损，禁用 pe 和 peg
- 如果公司无明显周期特征，不要使用 normalized_pe
"""

INDUSTRY_ROUTING_USER_PROMPT_TEMPLATE = """## 公司信息
- 名称：{name}
- 行业标签：{industry}
- 主营业务：{business_description}

## 关键财务指标
- 毛利率：{gross_margin:.1f}%
- 净利率：{net_margin:.1f}%
- ROE：{roe:.1f}%
- 研发费用率：{rd_expense_ratio:.1f}%
- 资产负债率：{de_ratio:.1f}x
- 营收增长：{revenue_growth:.1f}%
- 净利润增长：{net_income_growth:.1f}%
- FCF 正值年数（近5年）：{fcf_positive_years}

请选择最适合的估值框架，输出 JSON。"""
```

- [ ] **Step 2: Add industry_routing task to llm_config.yaml**

Add to `config/llm_config.yaml` under `task_routing`:

```yaml
  industry_routing:
    provider: deepseek
    model: deepseek-reasoner
    max_tokens: 800
    temperature: 0.1
```

- [ ] **Step 3: Write test for JSON extraction**

```python
# Add to tests/test_industry_engine.py

class TestJSONExtraction:
    """Tests for LLM JSON extraction with DeepSeek <think> handling."""

    def test_extract_json_from_code_block(self):
        """Extract JSON from markdown code block."""
        from src.agents.industry_engine import extract_json_from_llm_output

        raw = '''Some text
```json
{"regime": "tech", "primary_methods": ["pe", "ps"]}
```
More text'''
        result = extract_json_from_llm_output(raw)
        assert result["regime"] == "tech"
        assert result["primary_methods"] == ["pe", "ps"]

    def test_extract_json_with_think_block(self):
        """DeepSeek <think> blocks should be stripped."""
        from src.agents.industry_engine import extract_json_from_llm_output

        raw = '''<think>
Let me analyze this company...
The gross margin is high at 75%.
</think>

```json
{"regime": "brand_moat", "primary_methods": ["pe_moat"]}
```'''
        result = extract_json_from_llm_output(raw)
        assert result["regime"] == "brand_moat"

    def test_extract_bare_json(self):
        """Extract JSON without code block markers."""
        from src.agents.industry_engine import extract_json_from_llm_output

        raw = '{"regime": "utility", "primary_methods": ["ddm", "dcf"]}'
        result = extract_json_from_llm_output(raw)
        assert result["regime"] == "utility"

    def test_invalid_json_raises_error(self):
        """Invalid JSON should raise ValueError."""
        from src.agents.industry_engine import extract_json_from_llm_output
        import pytest

        with pytest.raises(ValueError):
            extract_json_from_llm_output("not valid json at all")
```

- [ ] **Step 4: Implement JSON extraction and LLM routing**

Add to `src/agents/industry_engine.py`:

```python
import hashlib
import json
import re
from pathlib import Path

from src.llm.router import call_llm, LLMError
from src.llm.prompts import INDUSTRY_ROUTING_SYSTEM_PROMPT, INDUSTRY_ROUTING_USER_PROMPT_TEMPLATE
from src.utils.config import get_output_dir


def extract_json_from_llm_output(raw_output: str) -> dict:
    """
    Extract JSON from LLM output, handling DeepSeek's <think> blocks.

    Strategies:
    1. Remove <think>...</think> blocks
    2. Try to extract from ```json...``` code block
    3. Fallback to extracting bare JSON object
    4. Raise ValueError if all fail
    """
    # Step 1: Remove <think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", raw_output, flags=re.DOTALL)

    # Step 2: Try markdown code block
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Step 3: Try bare JSON object
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from LLM output: {raw_output[:200]}")


def _get_cache_key(stock_code: str, report_period: str) -> str:
    """Generate cache key from stock code and report period."""
    raw = f"{stock_code}:{report_period}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached_config(cache_key: str) -> ValuationConfig | None:
    """Try to load cached ValuationConfig."""
    cache_dir = get_output_dir("industry_cache")
    cache_file = cache_dir / f"{cache_key}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return ValuationConfig(**data)
        except Exception as e:
            logger.warning("[IndustryEngine] Cache read failed: %s", e)
    return None


def _save_to_cache(cache_key: str, config: ValuationConfig) -> None:
    """Save ValuationConfig to cache."""
    cache_dir = get_output_dir("industry_cache")
    cache_file = cache_dir / f"{cache_key}.json"
    try:
        cache_file.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[IndustryEngine] Cache write failed: %s", e)


def _call_llm_for_routing(company_info: dict, metrics: dict) -> ValuationConfig | None:
    """
    Layer 2: LLM dynamic routing.

    Calls DeepSeek-Reasoner to determine the best valuation framework.
    Returns ValuationConfig on success, None on failure (falls through to Layer 3).
    """
    # Build user prompt
    prompt_vars = {
        "name": company_info.get("name", "Unknown"),
        "industry": company_info.get("industry", "Unknown"),
        "business_description": company_info.get("business_description", ""),
        "gross_margin": metrics.get("gross_margin") or 0,
        "net_margin": metrics.get("net_margin") or 0,
        "roe": metrics.get("roe") or 0,
        "rd_expense_ratio": metrics.get("rd_expense_ratio") or 0,
        "de_ratio": metrics.get("de_ratio") or 0,
        "revenue_growth": metrics.get("revenue_growth") or 0,
        "net_income_growth": metrics.get("net_income_growth") or 0,
        "fcf_positive_years": metrics.get("fcf_positive_years") or 0,
    }

    try:
        user_prompt = INDUSTRY_ROUTING_USER_PROMPT_TEMPLATE.format(**prompt_vars)
        raw_output = call_llm(
            task="industry_routing",
            system_prompt=INDUSTRY_ROUTING_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        # Extract and parse JSON
        parsed = extract_json_from_llm_output(raw_output)

        # Build ValuationConfig
        config = ValuationConfig(
            regime=parsed.get("regime", "llm_generic"),
            primary_methods=parsed.get("primary_methods", ["ev_ebitda", "pe"]),
            method_importance=parsed.get("method_importance", {}),
            disabled_methods=parsed.get("disabled_methods", []),
            scoring_mode=parsed.get("scoring_mode", "standard"),
            ev_ebitda_multiple_range=tuple(parsed.get("ev_ebitda_multiple_range", [8.0, 12.0])),
            confidence=0.75,
            source="llm",
            rationale=parsed.get("rationale", ""),
        )

        logger.info(
            "[IndustryEngine] LLM routing: regime=%s, methods=%s",
            config.regime, config.primary_methods
        )
        return config

    except LLMError as e:
        logger.warning("[IndustryEngine] LLM call failed: %s", e)
        return None
    except ValueError as e:
        logger.warning("[IndustryEngine] JSON extraction failed: %s", e)
        return None
    except Exception as e:
        logger.warning("[IndustryEngine] LLM routing error: %s", e)
        return None
```

- [ ] **Step 5: Run JSON extraction tests**

Run: `pytest tests/test_industry_engine.py::TestJSONExtraction -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agents/industry_engine.py src/llm/prompts.py config/llm_config.yaml tests/test_industry_engine.py
git commit -m "feat(v3): implement LLM routing layer with caching and JSON extraction"
```

---

### Task 2.4: Implement Unified Entry Point

**Files:**
- Modify: `src/agents/industry_engine.py`
- Test: `tests/test_industry_engine.py`

- [ ] **Step 1: Write test for unified entry point**

```python
# Add to tests/test_industry_engine.py

class TestGetValuationConfig:
    """Tests for the unified get_valuation_config entry point."""

    def test_fallback_never_fails(self):
        """Fallback should always return a valid config."""
        from src.agents.industry_engine import get_fallback_config

        config = get_fallback_config()
        assert config.regime == "generic"
        assert config.source == "fallback"
        assert len(config.primary_methods) >= 2
        assert sum(config.weights.values()) == 1.0

    def test_get_valuation_config_returns_config(self):
        """get_valuation_config should always return ValuationConfig (without LLM call)."""
        from src.agents.industry_engine import get_valuation_config
        from src.agents.valuation_config import ValuationConfig

        # Normal company metrics (no special regime)
        metrics = {
            "de_ratio": 1.5,
            "gross_margin": 35.0,
            "has_loan_loss_provision": False,
            "has_insurance_reserve": False,
        }
        company_info = {"name": "Test Corp", "industry": "Manufacturing"}

        # Use skip_llm=True to avoid real LLM calls in tests (falls back to generic)
        config = get_valuation_config("000001.SZ", company_info, metrics, skip_llm=True)
        assert isinstance(config, ValuationConfig)
        assert config.regime is not None
        assert len(config.primary_methods) > 0
        assert config.source in ("hard_rule", "fallback")  # No LLM in this test

    def test_hard_rule_takes_priority(self):
        """Hard rule match should return immediately (no LLM call)."""
        from src.agents.industry_engine import get_valuation_config

        # Bank metrics
        metrics = {
            "de_ratio": 12.0,
            "has_loan_loss_provision": True,
            "has_insurance_reserve": False,
        }
        company_info = {"name": "工商银行", "industry": "银行"}

        config = get_valuation_config("601398.SH", company_info, metrics)
        assert config.regime == "bank"
        assert config.source == "hard_rule"
```

- [ ] **Step 2: Implement fallback and unified entry**

Add to `src/agents/industry_engine.py`:

```python
# ── Fallback Configuration ───────────────────────────────────────────────────

def get_fallback_config() -> ValuationConfig:
    """
    Layer 3: Safe fallback — always returns valid config.

    Used when:
    - No hard rule matches
    - LLM call fails or returns invalid data
    - Cache miss and LLM disabled
    """
    return ValuationConfig(
        regime="generic",
        primary_methods=["ev_ebitda", "pe", "pb"],
        weights={"ev_ebitda": 0.4, "pe": 0.35, "pb": 0.25},
        confidence=0.40,
        source="fallback",
        rationale="No special regime detected, using balanced generic valuation",
    )


# ── Unified Entry Point ──────────────────────────────────────────────────────

def get_valuation_config(
    ticker: str,
    company_info: dict,
    metrics: dict,
    *,
    skip_llm: bool = False,
) -> ValuationConfig:
    """
    Unified entry point for the three-layer industry engine.

    Args:
        ticker: Stock ticker (e.g., "601398.SH")
        company_info: Dict with name, industry, business_description
        metrics: Dict with financial metrics

    Returns:
        ValuationConfig with regime, methods, weights, confidence, source
    """
    # Layer 1: Hard Rules
    hard_result = detect_special_regime(metrics, company_info)
    if hard_result:
        config = _build_valuation_config_from_regime(
            regime=hard_result.regime,
            confidence=hard_result.confidence,
            triggered_rules=hard_result.triggered_rules,
            rationale=hard_result.rationale,
        )
        logger.info(
            "[IndustryEngine] %s: regime=%s, source=hard_rule, confidence=%.2f",
            ticker, config.regime, config.confidence
        )
        return config

    # Layer 2: LLM Dynamic Routing (with cache)
    if not skip_llm:
        report_period = metrics.get("report_period", "unknown")
        cache_key = _get_cache_key(ticker, report_period)

        # Check cache first
        cached = _get_cached_config(cache_key)
        if cached:
            logger.info(
                "[IndustryEngine] %s: regime=%s, source=cache",
                ticker, cached.regime
            )
            return cached

        # Call LLM
        llm_config = _call_llm_for_routing(company_info, metrics)
        if llm_config:
            _save_to_cache(cache_key, llm_config)
            logger.info(
                "[IndustryEngine] %s: regime=%s, source=llm, confidence=%.2f",
                ticker, llm_config.regime, llm_config.confidence
            )
            return llm_config

    # Layer 3: Fallback
    fallback = get_fallback_config()
    logger.info(
        "[IndustryEngine] %s: regime=%s, source=fallback",
        ticker, fallback.regime
    )
    return fallback
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_industry_engine.py::TestGetValuationConfig -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/agents/industry_engine.py tests/test_industry_engine.py
git commit -m "feat(v3): implement unified entry point with three-layer funnel"
```

---

## Chunk 3: Integration and Migration

### Task 3.1: Add Feature Flags

**Files:**
- Modify: `src/utils/config.py`

- [ ] **Step 1: Add get_feature_flags function**

Add to `src/utils/config.py`:

```python
def get_feature_flags() -> dict[str, bool]:
    """Load feature flags from environment variables."""
    return {
        "use_industry_engine_v3": os.getenv("USE_INDUSTRY_ENGINE_V3", "false").lower() == "true",
        "industry_engine_parallel_mode": os.getenv("INDUSTRY_ENGINE_PARALLEL", "false").lower() == "true",
    }
```

- [ ] **Step 2: Verify import works**

Run: `python -c "from src.utils.config import get_feature_flags; print(get_feature_flags())"`
Expected: `{'use_industry_engine_v3': False, 'industry_engine_parallel_mode': False}`

- [ ] **Step 3: Commit**

```bash
git add src/utils/config.py
git commit -m "feat(v3): add feature flags for industry engine migration"
```

---

### Task 3.2: Add Comparison Mode for Parallel Validation

**Files:**
- Modify: `src/agents/industry_engine.py`

- [ ] **Step 1: Add ComparisonResult model and compare function**

Add to `src/agents/industry_engine.py`:

```python
from datetime import datetime
from pydantic import BaseModel


class ComparisonResult(BaseModel):
    """Result of comparing V3 engine with legacy V2 classifier."""
    ticker: str
    timestamp: datetime

    # V3 results
    v3_regime: str
    v3_source: str
    v3_methods: list[str]
    v3_confidence: float

    # V2 results (from legacy classifier)
    v2_regime: str
    v2_methods: list[str]

    # Comparison
    agreement: bool
    diff_summary: str | None = None


def compare_with_legacy(
    ticker: str,
    company_info: dict,
    metrics: dict,
    legacy_result: dict,
) -> ComparisonResult:
    """
    Compare V3 engine result with legacy V2 classifier.

    Used in parallel mode to validate V3 behavior before switching.
    """
    v3_config = get_valuation_config(ticker, company_info, metrics)

    v2_regime = legacy_result.get("regime", "unknown")
    v2_methods = legacy_result.get("primary_methods", [])

    # Determine agreement
    agreement = v3_config.regime == v2_regime

    diff_summary = None
    if not agreement:
        diff_summary = f"V3={v3_config.regime} vs V2={v2_regime}"

    return ComparisonResult(
        ticker=ticker,
        timestamp=datetime.now(),
        v3_regime=v3_config.regime,
        v3_source=v3_config.source,
        v3_methods=v3_config.primary_methods,
        v3_confidence=v3_config.confidence,
        v2_regime=v2_regime,
        v2_methods=v2_methods,
        agreement=agreement,
        diff_summary=diff_summary,
    )


def log_comparison_to_file(comparison: ComparisonResult) -> None:
    """Append comparison result to JSONL log file."""
    log_file = get_output_dir() / "engine_comparison.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(comparison.model_dump_json() + "\n")
```

- [ ] **Step 2: Commit**

```bash
git add src/agents/industry_engine.py
git commit -m "feat(v3): add comparison mode for parallel validation"
```

---

### Task 3.3: Integrate into Valuation Agent

**Files:**
- Modify: `src/agents/valuation.py`

- [ ] **Step 1: Add V3 integration to valuation.py**

Find the `run()` function in `src/agents/valuation.py` and add integration logic:

```python
# At the top of the file, add imports:
from src.utils.config import get_feature_flags

# Inside the run() function, after fetching data and before current valuation logic:

def run(ticker: str, market: str) -> AgentSignal:
    # ... existing data fetching code ...

    flags = get_feature_flags()

    if flags["use_industry_engine_v3"]:
        from src.agents.industry_engine import get_valuation_config, compare_with_legacy, log_comparison_to_file

        # Build metrics dict for engine
        engine_metrics = _build_engine_metrics(ticker, market)
        company_info = _get_company_info(ticker, market)

        # Get V3 config
        valuation_config = get_valuation_config(ticker, company_info, engine_metrics)

        # Optional: parallel comparison mode
        if flags["industry_engine_parallel_mode"]:
            legacy_result = _get_legacy_valuation_config(ticker, market, company_info)
            comparison = compare_with_legacy(ticker, company_info, engine_metrics, legacy_result)
            log_comparison_to_file(comparison)

        # Use valuation_config for subsequent logic
        # ...
    else:
        # Use existing legacy logic
        # ...
```

- [ ] **Step 2: Add _build_engine_metrics helper**

Add helper function to aggregate metrics for the engine. This function uses the existing database query functions already imported in valuation.py:

```python
def _build_engine_metrics(ticker: str, market: str) -> dict:
    """Build metrics dict required by industry_engine.get_valuation_config().

    Uses existing database query functions from src.data.database module.
    """
    from src.data.database import get_balance_sheets, get_financial_metrics

    # Fetch latest balance sheet
    balance_sheets = get_balance_sheets(ticker, limit=1, period_type="annual")
    latest_bs = balance_sheets[0] if balance_sheets else {}

    # Fetch latest financial metrics
    fin_metrics = get_financial_metrics(ticker, limit=5)
    latest_fm = fin_metrics[0] if fin_metrics else {}

    # Calculate derived metrics
    roe_values = [m.get("roe") for m in fin_metrics[:5] if m.get("roe") is not None]
    fcf_positive = sum(1 for m in fin_metrics[:5] if (m.get("fcf_per_share") or 0) > 0)

    return {
        # From balance sheet
        "total_assets": latest_bs.get("total_assets"),
        "inventory": latest_bs.get("inventory"),
        "advance_receipts": latest_bs.get("advance_receipts"),
        "fixed_assets": latest_bs.get("fixed_assets"),
        "has_loan_loss_provision": bool(latest_bs.get("has_loan_loss_provision", 0)),
        "has_insurance_reserve": bool(latest_bs.get("has_insurance_reserve", 0)),

        # From financial metrics
        "de_ratio": latest_fm.get("debt_to_equity"),
        "gross_margin": latest_fm.get("gross_margin"),
        "net_margin": latest_fm.get("operating_margin"),  # Approximate
        "roe": latest_fm.get("roe"),
        "rd_expense_ratio": latest_fm.get("rd_expense_ratio"),
        "revenue_growth": latest_fm.get("revenue_growth"),
        "net_income_growth": latest_fm.get("net_income_growth"),

        # Derived
        "roe_5yr_avg": sum(roe_values) / len(roe_values) if roe_values else None,
        "fcf_positive_years": fcf_positive,
        "report_period": latest_bs.get("period_end_date", "unknown"),
    }


def _get_company_info(ticker: str, market: str) -> dict:
    """Get basic company info for LLM routing.

    Returns minimal info needed for industry engine. The industry label is optional
    since V3 focuses on financial characteristics rather than labels.
    """
    # For now, return minimal info. In Phase 2+, can integrate with company profile API
    return {
        "name": ticker,  # Will be populated by LLM context if available
        "industry": "",
        "business_description": "",
    }
```

- [ ] **Step 2.5: Add _get_legacy_valuation_config for comparison mode**

```python
def _get_legacy_valuation_config(ticker: str, market: str, company_info: dict) -> dict:
    """Get valuation config from legacy V2 industry classifier.

    Used only in parallel comparison mode to compare V3 vs V2 results.
    """
    from src.agents.industry_classifier import classify_industry

    # Call existing V2 classifier
    v2_result = classify_industry(ticker, company_info.get("industry", ""))

    return {
        "regime": v2_result.get("regime", "generic"),
        "primary_methods": v2_result.get("valuation_methods", ["ev_ebitda", "pe"]),
    }
```

- [ ] **Step 3: Verify syntax is correct**

Run: `python -c "from src.agents.valuation import run; print('OK')"`
Expected: "OK"

- [ ] **Step 4: Commit**

```bash
git add src/agents/valuation.py
git commit -m "feat(v3): integrate industry engine into valuation agent"
```

---

## Chunk 4: Comprehensive Testing

### Task 4.1: Add Edge Case Tests

**Files:**
- Modify: `tests/test_industry_engine.py`

- [ ] **Step 1: Add edge case tests**

```python
# Add to tests/test_industry_engine.py

class TestEdgeCases:
    """Edge case tests for V3 industry engine."""

    def test_missing_inventory_no_real_estate_match(self):
        """Missing inventory should not match real_estate."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "total_assets": 100_000_000_000,
            "inventory": None,
            "advance_receipts": 20_000_000_000,
            "fixed_assets": 2_000_000_000,
        }
        result = detect_special_regime(metrics, {})
        assert result is None or result.regime != "real_estate"

    def test_multiple_rule_match_priority(self):
        """Bank rule should take priority over insurance if both match."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "de_ratio": 12,
            "has_loan_loss_provision": True,
            "has_insurance_reserve": True,  # Also true
        }
        result = detect_special_regime(metrics, {})
        assert result.regime == "bank"  # Bank has higher priority

    def test_zero_total_assets_no_crash(self):
        """Zero total_assets should not cause division by zero."""
        from src.agents.industry_engine import detect_special_regime

        metrics = {
            "total_assets": 0,
            "inventory": 1000,
            "advance_receipts": 500,
        }
        result = detect_special_regime(metrics, {})
        # Should not crash, may return None or some result
        assert result is None or result.regime is not None

    def test_valuation_config_weights_exact_sum(self):
        """Weights should sum to exactly 1.0, not 0.9999."""
        from src.agents.valuation_config import ValuationConfig

        config = ValuationConfig(
            regime="test",
            primary_methods=["pe", "pb", "dcf"],
            method_importance={"pe": 1, "pb": 1, "dcf": 1},
            source="llm",
        )
        total = sum(config.weights.values())
        assert total == 1.0, f"Weights sum to {total}, expected 1.0"
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/test_industry_engine.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_industry_engine.py
git commit -m "test(v3): add edge case tests for industry engine"
```

---

### Task 4.2: Run Full Test Suite

- [ ] **Step 1: Run entire test suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS (or expected skips)

- [ ] **Step 2: Final commit for Phase 1-4 completion**

```bash
git add -A
git commit -m "feat(v3): complete V3.0 Industry Engine implementation

- Data layer: balance_sheet_scanner, BalanceSheet model extensions, schema migration
- Core engine: ValuationConfig with weight normalization, hard rule detection
- LLM integration: DeepSeek-Reasoner routing with caching and JSON extraction
- Integration: feature flags, valuation.py integration, comparison mode
- Testing: comprehensive unit tests for all components"
```

---

## Summary

This plan implements the V3.0 Industry Engine in 4 chunks:

1. **Chunk 1: Data Layer Foundation** (Tasks 1.1-1.4)
   - Balance sheet scanner for bank/insurance detection
   - BalanceSheet model extensions
   - Database schema migration
   - AKShare integration

2. **Chunk 2: Core Engine Implementation** (Tasks 2.1-2.4)
   - ValuationConfig Pydantic model
   - Hard rule detection (6 regimes)
   - LLM routing with caching
   - Unified entry point

3. **Chunk 3: Integration and Migration** (Tasks 3.1-3.3)
   - Feature flags
   - Comparison mode
   - Valuation agent integration

4. **Chunk 4: Comprehensive Testing** (Tasks 4.1-4.2)
   - Edge case tests
   - Full regression testing

Total: 14 tasks, ~120 steps

**Enable V3 with:**
```bash
export USE_INDUSTRY_ENGINE_V3=true
export INDUSTRY_ENGINE_PARALLEL=true  # Optional: compare with V2
```
