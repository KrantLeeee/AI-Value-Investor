# V3.0 行业识别引擎设计文档

> 设计日期: 2026-03-14
> 状态: 待审批
> 作者: Claude + 用户协作

## 1. 概述

### 1.1 问题背景

当前 V2.x 行业分类系统存在以下问题：

1. **命中率低**：约 40% 的股票落入 `generic` 兜底
2. **依赖不可靠标签**：AKShare 返回的行业标签不稳定、不一致（如宁德时代被标记为"医药生物"）
3. **关键词匹配局限性**：基于字符串的行业匹配无法穷举所有变体
4. **估值方法选择粗糙**：generic 兜底使用 8x EV/EBITDA，对许多行业不适用

### 1.2 设计目标

1. **从"查字典"到"看本质"**：基于财务特征而非行业标签字符串
2. **三层漏斗架构**：硬规则（零成本）→ LLM 路由（缓存后低成本）→ 安全兜底
3. **永不崩溃**：任何情况下都返回有效配置
4. **渐进迁移**：与现有系统并行运行，验证后切换

### 1.3 设计方案选择

| 方案 | 描述 | 决策 |
|-----|------|-----|
| A: 独立模块 | 新建模块，完全独立 | - |
| B: 原地重构 | 直接修改现有代码 | - |
| **C: 渐进迁移** | 新模块 + Feature Flag + 并行验证 | ✅ 采用 |

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     get_valuation_config()                       │
│                        统一入口函数                               │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  第一层：硬规则探针 detect_special_regime()                       │
│  ────────────────────────────────────────────────────────────── │
│  • 银行：DE > 8 + has_loan_loss_provision                        │
│  • 保险：DE > 4 + has_insurance_reserve                          │
│  • 房地产：inventory > 40% + advance_receipts > 10%              │
│  • 困境企业：3年均值负 + 连续亏损（排除金融股）                     │
│  • 消费护城河：gross_margin > 70% + ROE_5yr > 18% + FCF 稳定      │
│  • 创新药：R&D > 30% + 低利润 + 管线关键词                        │
│                                                                 │
│  命中 → 直接返回 ValuationConfig (confidence ≥ 0.82)            │
│  未命中 → 进入第二层                                             │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  第二层：LLM 动态路由                                            │
│  ────────────────────────────────────────────────────────────── │
│  1. 检查缓存（key = stock_code + report_period）                 │
│  2. 缓存命中 → 直接返回                                          │
│  3. 缓存未命中 → 调用 DeepSeek-Reasoner                          │
│     • 输入：公司信息 + 财务指标摘要                               │
│     • 输出：JSON 格式的 ValuationConfig                          │
│     • 使用 method_importance (1-10分) 代替精确权重               │
│     • Python 侧自动归一化                                        │
│  4. 保存到缓存                                                   │
│                                                                 │
│  成功 → 返回 ValuationConfig (source='llm')                     │
│  失败 → 进入第三层                                               │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  第三层：安全兜底                                                │
│  ────────────────────────────────────────────────────────────── │
│  • regime: 'generic'                                            │
│  • methods: ['ev_ebitda', 'pe', 'pb']                           │
│  • weights: {ev_ebitda: 0.4, pe: 0.35, pb: 0.25}                │
│  • confidence: 0.40                                             │
│  • 永远不会失败                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 文件结构变更

```
新增文件:
├── src/agents/industry_engine.py      # 三层漏斗主入口
├── src/agents/valuation_config.py     # ValuationConfig Pydantic 模型
├── src/data/balance_sheet_scanner.py  # 资产负债表科目扫描器
└── tests/test_industry_engine.py      # 单元测试

修改文件:
├── src/data/models.py                 # BalanceSheet 新增字段
├── src/data/database.py               # Schema 新增字段
├── src/data/akshare_source.py         # 集成科目扫描器
├── src/agents/valuation.py            # 调用新引擎
├── src/utils/config.py                # Feature Flag
└── config/llm_config.yaml             # 新增 industry_routing 任务
```

---

## 3. 数据层设计

### 3.1 新增文件：`src/data/balance_sheet_scanner.py`

```python
"""资产负债表科目扫描器 — 从原始科目名称提取行业特征标志位"""

BANK_BALANCE_SHEET_KEYWORDS = [
    '贷款和垫款', '发放贷款及垫款', '吸收存款', '向中央银行借款',
    '贷款损失准备', '应收款项类投资', '存放同业款项', '拆出资金',
    '买入返售金融资产', '应付债券',
]

INSURANCE_BALANCE_SHEET_KEYWORDS = [
    '未到期责任准备金', '未决赔款准备金', '寿险责任准备金',
    '长期健康险责任准备金', '保户储金及投资款', '保费收入',
    '应付赔付款', '应付保单红利',
]

def extract_industry_flags(raw_balance_sheet_items: list[str]) -> dict:
    """
    扫描资产负债表科目名称，提取行业专属标志位。

    Args:
        raw_balance_sheet_items: 资产负债表的科目名称列表

    Returns:
        dict with has_loan_loss_provision, has_insurance_reserve
    """
    flags = {
        'has_loan_loss_provision': False,
        'has_insurance_reserve': False,
    }

    if not raw_balance_sheet_items:
        return flags

    all_items_str = ' '.join(raw_balance_sheet_items)

    # 银行科目检测（至少2个科目命中）
    bank_hits = sum(1 for kw in BANK_BALANCE_SHEET_KEYWORDS if kw in all_items_str)
    flags['has_loan_loss_provision'] = bank_hits >= 2

    # 保险科目检测（至少2个科目命中）
    insurance_hits = sum(1 for kw in INSURANCE_BALANCE_SHEET_KEYWORDS if kw in all_items_str)
    flags['has_insurance_reserve'] = insurance_hits >= 2

    return flags
```

### 3.2 BalanceSheet 模型扩展

```python
# src/data/models.py

class BalanceSheet(BaseModel):
    # ... 现有字段保持不变 ...

    # V3.0 新增：房地产识别所需字段
    inventory: float | None = None           # 存货
    advance_receipts: float | None = None    # 预收款项/合同负债
    fixed_assets: float | None = None        # 固定资产（含在建工程）

    # V3.0 新增：行业专属标志位
    has_loan_loss_provision: bool = False    # 银行：贷款损失准备
    has_insurance_reserve: bool = False      # 保险：保险准备金
```

**fixed_assets 字段说明**：
- 用于区分房地产开发商（轻资产）与重工制造业（重资产）
- 房地产：土地计入存货，固定资产占比通常 < 5%
- 重工/造船：大量厂房设备，固定资产占比通常 20-40%

### 3.3 数据库 Schema 扩展与迁移

**新增字段**（通过迁移添加）：
```sql
-- balance_sheets 表新增字段
ALTER TABLE balance_sheets ADD COLUMN inventory REAL;
ALTER TABLE balance_sheets ADD COLUMN advance_receipts REAL;
ALTER TABLE balance_sheets ADD COLUMN fixed_assets REAL;
ALTER TABLE balance_sheets ADD COLUMN has_loan_loss_provision INTEGER DEFAULT 0;
ALTER TABLE balance_sheets ADD COLUMN has_insurance_reserve INTEGER DEFAULT 0;
```

**迁移策略**：在 `database.py` 中新增迁移函数：

```python
# src/data/database.py

def _run_v3_migrations(conn: sqlite3.Connection) -> None:
    """
    V3.0 Schema 迁移：为 balance_sheets 表添加行业识别所需字段。

    SQLite 支持 ALTER TABLE ADD COLUMN，但不支持 IF NOT EXISTS。
    使用 PRAGMA table_info 检查字段是否存在。
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
            logger.info(f"[Migration] Added column: balance_sheets.{col_name}")


def init_db(db_path: Path | None = None) -> None:
    """Create all tables and indexes if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _run_v3_migrations(conn)  # V3.0 迁移
    logger.info("Database initialised at %s", db_path or get_db_path())
```

**SCHEMA_SQL 更新**：在 `CREATE TABLE balance_sheets` 中直接包含新字段（新数据库无需迁移）：

```sql
CREATE TABLE IF NOT EXISTS balance_sheets (
    -- ... 现有字段 ...
    inventory            REAL,
    advance_receipts     REAL,
    fixed_assets         REAL,
    has_loan_loss_provision INTEGER DEFAULT 0,
    has_insurance_reserve   INTEGER DEFAULT 0,
    -- ... 其余字段 ...
);
```

**开发/测试环境简化方案**：
> ⚡ 如果处于测试阶段且本地 `output/database.db` 中没有大量不可再生数据，
> 最简单的做法是直接删除 `.db` 文件，让 `SCHEMA_SQL` 在空库上按 V3 版本重新建表。
> Migration 逻辑保留作为生产环境或协作场景的备用方案。

### 3.4 AKShare 集成与字段映射

**AKShare 资产负债表字段映射表**：

| AKShare 中文字段名 | BalanceSheet 字段 | 备注 |
|-------------------|------------------|------|
| `*存货` / `存货` | `inventory` | 一般企业 |
| `*预收款项` / `预收款项` | `advance_receipts` | 旧会计准则 |
| `*合同负债` / `合同负债` | `advance_receipts` | 新会计准则（优先使用） |
| `*固定资产` / `固定资产` | `fixed_assets` | 含在建工程 |
| `*在建工程` / `在建工程` | 累加到 `fixed_assets` | 重资产判断 |
| `贷款损失准备` / `贷款减值准备` | 触发 `has_loan_loss_provision=True` | 银行专属 |
| `未到期责任准备金` | 触发 `has_insurance_reserve=True` | 保险专属 |

**实现逻辑**（在 `akshare_source.py` 的 `get_balance_sheets()` 中）：

```python
from src.data.balance_sheet_scanner import extract_industry_flags

def get_balance_sheets(self, ticker: str, market: MarketType, ...) -> list[BalanceSheet]:
    # ... 获取原始 DataFrame df ...

    # 提取所有科目名称（DataFrame 的列名）
    raw_items = list(df.columns)

    # 调用科目扫描器获取行业标志位
    industry_flags = extract_industry_flags(raw_items)

    for _, row in df.iterrows():
        # 提取存货
        inventory = _get_val("*存货", "存货")

        # 提取预收款项（优先使用合同负债，新准则）
        advance_receipts = _get_val("*合同负债", "合同负债", "*预收款项", "预收款项")

        # 提取固定资产（含在建工程，用于区分房地产 vs 重工制造）
        fixed_assets_base = _get_val("*固定资产", "固定资产") or 0
        construction_in_progress = _get_val("*在建工程", "在建工程") or 0
        fixed_assets = fixed_assets_base + construction_in_progress

        results.append(BalanceSheet(
            # ... 现有字段 ...
            inventory=inventory,
            advance_receipts=advance_receipts,
            fixed_assets=fixed_assets if fixed_assets > 0 else None,
            has_loan_loss_provision=industry_flags['has_loan_loss_provision'],
            has_insurance_reserve=industry_flags['has_insurance_reserve'],
            source=self.source_name,
        ))

    return results
```

---

## 4. 核心引擎设计

### 4.1 ValuationConfig 模型

```python
# src/agents/valuation_config.py

from pydantic import BaseModel, field_validator, model_validator, Field
from typing import Literal

ALLOWED_METHODS = {
    'pe', 'ev_ebitda', 'dcf', 'ps', 'pb', 'pb_roe', 'ddm',
    'peg', 'normalized_pe', 'pe_moat', 'ev_sales',
    'asset_replacement', 'net_net', 'graham_number'
}

class ValuationConfig(BaseModel):
    """估值框架配置 — 三层漏斗的统一输出"""

    regime: str                                    # 估值体系名称
    primary_methods: list[str]                     # 估值方法列表
    weights: dict[str, float] = {}                 # 方法权重（归一化后）
    method_importance: dict[str, int] = {}         # 【接收 LLM 1-10 打分】
    disabled_methods: list[str] = []               # 禁用的方法
    exempt_scoring_metrics: list[str] = []         # 基本面评分豁免项
    scoring_mode: str = 'standard'                 # standard/cycle_adjusted/financial
    ev_ebitda_multiple_range: tuple[float, float] = (8.0, 12.0)

    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    source: Literal['hard_rule', 'llm', 'fallback'] = 'llm'
    rationale: str = ''
    triggered_rules: list[str] = []

    @field_validator('primary_methods')
    @classmethod
    def methods_must_be_allowed(cls, v):
        invalid = set(v) - ALLOWED_METHODS
        if invalid:
            raise ValueError(f'非法估值方法: {invalid}')
        return v

    @model_validator(mode='after')
    def auto_normalize_weights(self):
        """
        Pydantic V2 推荐的跨字段校验与计算方法。

        优先级：
        1. 如果 weights 已有值 → 直接使用（硬规则场景）
        2. 如果 method_importance 有值 → 归一化为 weights（LLM 场景）
        3. 都没有 → 按 primary_methods 均分（兜底场景）
        """
        if self.weights:
            # weights 已有值，确保归一化
            total = sum(self.weights.values())
            if total > 0 and abs(total - 1.0) > 0.01:
                self.weights = {k: round(v / total, 4) for k, v in self.weights.items()}
            return self

        # 1. 优先使用 LLM 的 method_importance 打分
        if self.method_importance:
            total = sum(self.method_importance.values())
            if total == 0:
                raise ValueError('method_importance 不能全为 0')

            normalized = {k: round(v / total, 4) for k, v in self.method_importance.items()}

            # 处理浮点数尾差，确保加总绝对等于 1.0（量化严谨性）
            keys = list(normalized.keys())
            if keys:
                current_sum = sum(list(normalized.values())[:-1])
                normalized[keys[-1]] = round(1.0 - current_sum, 4)

            self.weights = normalized
            return self

        # 2. 都没有，按 primary_methods 均分
        if self.primary_methods:
            n = len(self.primary_methods)
            base_weight = round(1.0 / n, 4)
            self.weights = {m: base_weight for m in self.primary_methods}
            # 同样处理尾差
            keys = list(self.weights.keys())
            if keys:
                current_sum = sum(list(self.weights.values())[:-1])
                self.weights[keys[-1]] = round(1.0 - current_sum, 4)

        return self
```

**关键修正说明**：
- 新增 `method_importance: dict[str, int]` 字段，用于接收 LLM 的 1-10 打分
- 使用 `@model_validator(mode='after')` 替代 `@field_validator`，实现跨字段计算
- 添加浮点数尾差处理，确保权重严格归一（如 `[0.33, 0.33, 0.34]` 而非 `[0.3333, 0.3333, 0.3333]`）

### 4.2 硬规则探针配置

```python
SPECIAL_REGIME_CONFIGS = {
    'bank': {
        'primary_methods': ['pb_roe', 'ddm'],
        'weights': {'pb_roe': 0.6, 'ddm': 0.4},
        'disabled_methods': ['ev_ebitda', 'graham_number', 'dcf'],
        'exempt_scoring_metrics': ['debt_equity', 'current_ratio', 'fcf_ni'],
        'scoring_mode': 'financial',
    },
    'insurance': {
        'primary_methods': ['pb_roe', 'ddm', 'pe'],
        'weights': {'pb_roe': 0.4, 'ddm': 0.35, 'pe': 0.25},
        'disabled_methods': ['ev_ebitda', 'graham_number'],
        'exempt_scoring_metrics': ['debt_equity', 'current_ratio', 'fcf_ni'],
        'scoring_mode': 'financial',
    },
    'real_estate': {
        'primary_methods': ['pb'],
        'weights': {'pb': 1.0},
        'disabled_methods': ['graham_number', 'ev_ebitda', 'dcf'],
        'scoring_mode': 'standard',
        'pb_multiple_cap': 0.5,
    },
    'distressed': {
        'primary_methods': ['ev_sales', 'net_net', 'asset_replacement'],
        'weights': {'ev_sales': 0.5, 'net_net': 0.3, 'asset_replacement': 0.2},
        'disabled_methods': ['pe', 'graham_number', 'dcf', 'pb_roe'],
        'scoring_mode': 'distressed',
    },
    'brand_moat': {
        'primary_methods': ['pe_moat', 'dcf', 'ev_ebitda'],
        'weights': {'pe_moat': 0.5, 'dcf': 0.3, 'ev_ebitda': 0.2},
        'disabled_methods': ['graham_number'],
        'ev_ebitda_multiple_range': (15.0, 25.0),
    },
    'pharma_innovative': {
        'primary_methods': ['ps', 'ev_sales'],
        'weights': {'ps': 0.6, 'ev_sales': 0.4},
        'disabled_methods': ['pe', 'graham_number', 'ev_ebitda'],
        'exempt_scoring_metrics': ['fcf_ni', 'net_margin'],
    },
}
```

### 4.3 硬规则探针逻辑

| 规则 | 条件 | 置信度 |
|-----|------|-------|
| 银行 | DE > 8 AND has_loan_loss_provision | 0.95 |
| 保险 | DE > 4 AND has_insurance_reserve | 0.92 |
| 房地产 | inventory > 40% AND advance > 10% AND **fixed_assets < 10%** AND NOT 金融股 | 0.90 |
| 困境企业 | (margin_3yr < -10% OR roe_3yr < -10%) AND loss_years >= 2 AND NOT 金融股 | 0.85 |
| 消费护城河 | gross_margin > 70% AND roe_5yr > 18% AND fcf_positive >= 4 | 0.88 |
| 创新药 | rd_ratio > 30% AND net_margin < 5% AND has_pipeline_keywords | 0.82 |

**金融股排除逻辑**：
```python
is_financial = has_loan_loss_provision or has_insurance_reserve
```

**房地产 vs 重工制造区分逻辑**：
```python
# 房地产开发商是轻资产模式（土地算存货，厂房设备极少）
# 重工/造船是重资产模式（大量厂房设备，固定资产占比高）
fixed_assets = metrics.get('fixed_assets', 0) or 0
total_assets = metrics.get('total_assets', 1) or 1
fixed_assets_ratio = fixed_assets / total_assets

# 轻资产判定：固定资产占比 < 10%
is_asset_light = fixed_assets_ratio < 0.10

if inventory_ratio > 0.40 and advance_ratio > 0.10 and is_asset_light and not is_financial:
    return SpecialRegimeResult(regime='real_estate', confidence=0.90, ...)
```

**设计原理**：
- 房地产开发商：土地储备计入「存货」，自用办公楼/设备极少 → `fixed_assets < 5%`
- 造船/重工：厂房、龙门吊、重型设备 → `fixed_assets 20-40%`
- 添加 `fixed_assets < 10%` 条件，可有效排除三一重工、中国船舶等制造业误判

**创新药管线关键词列表**：
```python
PIPELINE_KEYWORDS = [
    '临床', '管线', '适应症', 'pipeline',
    'IND', 'NDA', 'FDA', 'NMPA', 'BLA',
    '一期', '二期', '三期', 'Phase',
    '创新药', '生物药', '单抗', '双抗',
]
```

### 4.4 LLM 动态路由

**缓存策略**：
- Key: `md5(stock_code + report_period)` (完整 32 字符，避免碰撞)
- 存储位置: `output/industry_cache/{key}.json`
- V3.1 修正：使用 `report_period`（季度）而非 `fiscal_year`，防止季报更新后脏读

**JSON 提取器**：
```python
def extract_json_from_llm_output(raw_output: str) -> dict:
    """
    从 LLM 输出中提取 JSON，处理 DeepSeek-Reasoner 的 <think> 块。

    策略:
    1. 移除 <think>...</think> 块
    2. 优先提取 ```json...``` 代码块
    3. 降级提取裸 JSON 对象
    4. 全部失败时抛出 ValueError（由调用者处理降级到 fallback）
    """
    # ... 实现见 Section 3 代码 ...
```

**LLM Prompt 模板**（添加到 `src/llm/prompts.py`）：

```python
# src/llm/prompts.py

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
- 毛利率：{gross_margin}%
- 净利率：{net_margin}%
- ROE：{roe}%
- 研发费用率：{rd_expense_ratio}%
- 资产负债率：{de_ratio}x
- 营收增长：{revenue_growth}%
- 净利润增长：{net_income_growth}%
- FCF 正值年数（近5年）：{fcf_positive_years}

请选择最适合的估值框架，输出 JSON。"""
```

**LLM 任务配置**：
```yaml
# config/llm_config.yaml
task_routing:
  # ... 现有配置 ...

  industry_routing:
    provider: deepseek
    model: deepseek-reasoner
    max_tokens: 800
    temperature: 0.1
    description: "Industry classification and valuation method selection"
```

**错误处理流程**：
```
LLM 调用 → JSON 提取成功 → Pydantic 校验 → 返回 ValuationConfig
    │              │                 │
    │              │                 └── 校验失败 → log warning → fallback
    │              └── 提取失败 → log warning → fallback
    └── 调用失败/超时 → log error → fallback
```

---

## 5. 集成设计

### 5.1 Feature Flag

```python
# src/utils/config.py
def get_feature_flags() -> dict:
    return {
        'use_industry_engine_v3': os.getenv('USE_INDUSTRY_ENGINE_V3', 'false').lower() == 'true',
        'industry_engine_parallel_mode': os.getenv('INDUSTRY_ENGINE_PARALLEL', 'false').lower() == 'true',
    }
```

### 5.2 Valuation.py 集成点

```python
def run(ticker: str, market: str) -> AgentSignal:
    flags = get_feature_flags()

    if flags['use_industry_engine_v3']:
        from src.agents.industry_engine import get_valuation_config
        engine_metrics = _build_engine_metrics(...)
        company_info = _get_company_info(ticker, market)
        valuation_config = get_valuation_config(ticker, company_info, engine_metrics)

        if flags['industry_engine_parallel_mode']:
            from src.agents.industry_engine import compare_with_legacy
            comparison = compare_with_legacy(ticker, company_info, engine_metrics)
            _log_comparison_to_file(comparison)
    else:
        valuation_config = _legacy_get_valuation_config(...)
```

### 5.3 _build_engine_metrics() 函数

聚合以下指标：
- 基础指标：roe, gross_margin, net_margin, de_ratio, rd_expense_ratio, revenue_growth, net_income_growth
- 行业标志位：has_loan_loss_provision, has_insurance_reserve, inventory, advance_receipts, **fixed_assets**, total_assets
- 派生指标：roe_3yr_avg, roe_5yr_avg, net_margin_3yr_avg, fcf_positive_years, consecutive_loss_years
- 报告期：report_period

### 5.4 compare_with_legacy() 返回类型

```python
from pydantic import BaseModel
from datetime import datetime

class ComparisonResult(BaseModel):
    """新旧引擎对比结果"""
    ticker: str
    timestamp: datetime

    # V3 新引擎结果
    v3_regime: str
    v3_source: str  # 'hard_rule' | 'llm' | 'fallback'
    v3_methods: list[str]
    v3_confidence: float

    # V2 旧引擎结果
    v2_regime: str
    v2_methods: list[str]

    # 对比结论
    agreement: bool
    diff_summary: str | None = None  # 分歧说明


def compare_with_legacy(ticker: str, company_info: dict, metrics: dict) -> ComparisonResult:
    """与旧版 industry_classifier 对比，返回结构化结果"""
    # ... 实现 ...
```

**分歧日志存储**：`output/engine_comparison.jsonl`（每行一个 JSON）

### 5.5 可观测性设计

**日志格式**：
```python
logger.info(
    f"[IndustryEngine] {ticker}: "
    f"regime={config.regime}, "
    f"source={config.source}, "
    f"confidence={config.confidence:.2f}, "
    f"methods={config.primary_methods}, "
    f"triggered_rules={config.triggered_rules}"
)
```

**关键指标（未来 Phase 可实现）**：

| 指标 | 类型 | 说明 |
|-----|------|-----|
| `industry_engine_hard_rule_hit_rate` | Gauge | 硬规则命中率 |
| `industry_engine_llm_call_latency_ms` | Histogram | LLM 调用延迟 |
| `industry_engine_cache_hit_rate` | Gauge | 缓存命中率 |
| `industry_engine_fallback_count` | Counter | 兜底触发次数 |

**成本估算**：
- DeepSeek-Reasoner: 输入 ~$0.55/1M tokens, 输出 ~$2.19/1M tokens
- 单次调用估算: ~500 input tokens, ~300 output tokens → ~$0.001/次
- 假设 watchlist 200 只股票，全量无缓存调用 → ~$0.20
- 缓存后（季度更新）日常调用趋近于 0

---

## 6. 测试计划

### 6.1 单元测试覆盖

| 测试类 | 覆盖内容 |
|-------|---------|
| TestHardRuleProbes | 6 条硬规则的正向/负向测试 |
| TestJSONExtraction | `<think>` 块处理、代码块提取、裸 JSON 提取、错误处理 |
| TestValuationConfig | 权重归一化、非法方法拒绝、空权重自动均分 |
| TestUnifiedEntry | 兜底永不失败、get_valuation_config 永远返回 |
| **TestEdgeCases** | 边缘情况测试（见下文） |

**边缘情况测试用例**：

```python
class TestEdgeCases:
    def test_missing_inventory_uses_llm_or_fallback(self):
        """缺少 inventory 字段时不应误判为房地产"""
        metrics = {
            'total_assets': 100_000_000_000,
            'inventory': None,  # 缺失
            'advance_receipts': 20_000_000_000,
        }
        result = detect_special_regime(metrics, {})
        assert result is None or result.regime != 'real_estate'

    def test_multiple_rule_match_priority(self):
        """多规则同时满足时，按优先级返回（银行 > 保险 > 其他）"""
        # 银行规则优先级最高
        metrics = {
            'de_ratio': 12,
            'has_loan_loss_provision': True,
            'has_insurance_reserve': True,  # 同时满足保险条件
        }
        result = detect_special_regime(metrics, {})
        assert result.regime == 'bank'

    def test_llm_timeout_uses_fallback(self):
        """LLM 超时时使用 fallback"""
        # 通过 mock LLM 调用模拟超时
        # 验证返回 source='fallback'

    def test_stale_cache_not_used_after_new_report(self):
        """新季报发布后，旧缓存不应被使用"""
        # cache_key 包含 report_period，确保季报更新后 key 变化

    def test_high_inventory_manufacturing_not_real_estate(self):
        """高存货制造业不应误判为房地产（通过固定资产占比区分）"""
        metrics = {
            'total_assets': 100_000_000_000,
            'inventory': 45_000_000_000,      # 45% — 满足房地产存货条件
            'advance_receipts': 12_000_000_000, # 12% — 满足房地产预收条件
            'fixed_assets': 30_000_000_000,    # 30% — 重资产特征，排除房地产
        }
        company_info = {'name': '三一重工', 'industry': '工程机械'}
        result = detect_special_regime(metrics, company_info)
        # 由于 fixed_assets/total_assets = 30% > 10%，不满足轻资产条件
        # 因此不会命中房地产规则，应返回 None 交给 LLM 判断
        assert result is None or result.regime != 'real_estate'

    def test_real_estate_with_asset_light_structure(self):
        """真正的房地产开发商：高存货 + 高预收 + 轻资产 → 命中"""
        metrics = {
            'total_assets': 100_000_000_000,
            'inventory': 50_000_000_000,      # 50% — 土地储备
            'advance_receipts': 15_000_000_000, # 15% — 卖房预收款
            'fixed_assets': 3_000_000_000,     # 3% — 轻资产（仅有少量办公楼）
        }
        company_info = {'name': '招商蛇口', 'industry': '房地产'}
        result = detect_special_regime(metrics, company_info)
        # 满足所有条件：存货>40%, 预收>10%, 固定资产<10%
        assert result is not None
        assert result.regime == 'real_estate'
```

### 6.2 集成测试标的

| 股票代码 | 名称 | 预期 regime | 预期 source | 测试目的 |
|---------|------|------------|-------------|---------|
| 601398.SH | 工商银行 | bank | hard_rule | 银行规则验证 |
| 601318.SH | 中国平安 | insurance | hard_rule | 保险规则验证 |
| 001979.SZ | 招商蛇口 | real_estate | hard_rule | 房地产规则验证 |
| 600519.SH | 贵州茅台 | brand_moat | hard_rule | 消费护城河验证 |
| 688235.SH | 百济神州 | pharma_innovative | hard_rule/llm | 创新药规则验证 |
| 600900.SH | 长江电力 | utility (via LLM) | llm | LLM 路由验证 |
| 000792.SZ | 盐湖股份 | cyclical/distressed | hard_rule/llm | 周期/困境判断 |

---

## 7. 实施阶段

| 阶段 | 内容 | 预计工作量 |
|-----|------|-----------|
| Phase 1 | 数据层变更：balance_sheet_scanner, models, schema, akshare 集成 | 1-2 天 |
| Phase 2 | 核心引擎：industry_engine.py, valuation_config.py, 单元测试 | 2-3 天 |
| Phase 3 | 集成与迁移：valuation.py 集成, feature flag, 并行模式 | 1-2 天 |
| Phase 4 | 验证与切换：集成测试, 分歧分析, 正式切换 | 1 天 |

---

## 8. 风险与缓解

| 风险 | 缓解措施 |
|-----|---------|
| 科目扫描误判 | 要求至少 2 个科目命中；银行/保险规则额外要求高杠杆 |
| LLM 输出格式不稳定 | extract_json_from_llm_output 多策略降级；Pydantic 严格校验 |
| 迁移期回归 | Feature Flag + 并行对比模式；分歧日志分析 |
| LLM 成本超预期 | 缓存策略；单日调用预算监控（待实现） |

---

## 9. 附录

### 9.1 与 V3.0.0 Plan.md 原方案的差异

| 项目 | 原方案 | 本设计 | 原因 |
|-----|-------|-------|-----|
| 配置存储 | SPECIAL_REGIME_CONFIGS 在 Python 中 | 同左 | 保持简单，后续可迁移到 YAML |
| Pydantic 版本 | 未明确 | V2 (`field_validator`) | 项目已使用 Pydantic V2 |
| 并行验证 | 未详细设计 | `compare_with_legacy()` + JSONL 日志 | 确保平滑迁移 |
| 成本监控 | 提及但未实现 | Phase 4 后续迭代 | 先完成核心功能 |
