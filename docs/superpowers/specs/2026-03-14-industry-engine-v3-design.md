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

    # V3.0 新增：行业专属标志位
    has_loan_loss_provision: bool = False    # 银行：贷款损失准备
    has_insurance_reserve: bool = False      # 保险：保险准备金
```

### 3.3 数据库 Schema 扩展

```sql
-- balance_sheets 表新增字段
ALTER TABLE balance_sheets ADD COLUMN inventory REAL;
ALTER TABLE balance_sheets ADD COLUMN advance_receipts REAL;
ALTER TABLE balance_sheets ADD COLUMN has_loan_loss_provision INTEGER DEFAULT 0;
ALTER TABLE balance_sheets ADD COLUMN has_insurance_reserve INTEGER DEFAULT 0;
```

### 3.4 AKShare 集成

在 `akshare_source.py` 的 `get_balance_sheets()` 方法中：

1. 获取原始 DataFrame 后，提取所有科目名称
2. 调用 `extract_industry_flags()` 获取标志位
3. 提取 `inventory`（存货）和 `advance_receipts`（预收款项）
4. 填充到 BalanceSheet 对象

---

## 4. 核心引擎设计

### 4.1 ValuationConfig 模型

```python
# src/agents/valuation_config.py

from pydantic import BaseModel, field_validator, Field
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

    @field_validator('weights', mode='before')
    @classmethod
    def auto_normalize_weights(cls, v, info):
        """自动归一化权重"""
        if not v:
            methods = info.data.get('primary_methods', [])
            if methods:
                return {m: round(1.0 / len(methods), 4) for m in methods}
            return {}

        total = sum(v.values())
        if total == 0:
            raise ValueError('所有权重不能都为0')

        return {k: round(val / total, 4) for k, val in v.items()}
```

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
| 房地产 | inventory/assets > 40% AND advance/assets > 10% | 0.90 |
| 困境企业 | (margin_3yr < -10% OR roe_3yr < -10%) AND loss_years >= 2 AND NOT 金融股 | 0.85 |
| 消费护城河 | gross_margin > 70% AND roe_5yr > 18% AND fcf_positive >= 4 | 0.88 |
| 创新药 | rd_ratio > 30% AND net_margin < 5% AND has_pipeline_keywords | 0.82 |

### 4.4 LLM 动态路由

**缓存策略**：
- Key: `md5(stock_code + report_period)[:12]`
- 存储位置: `output/industry_cache/{key}.json`
- V3.1 修正：使用 `report_period`（季度）而非 `fiscal_year`，防止季报更新后脏读

**JSON 提取器**：
- 移除 `<think>...</think>` 块
- 优先提取 ` ```json...``` ` 代码块
- 降级提取裸 JSON 对象

**LLM 任务配置**：
```yaml
# config/llm_config.yaml
industry_routing:
  provider: deepseek
  model: deepseek-reasoner
  max_tokens: 800
  temperature: 0.1
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
- 行业标志位：has_loan_loss_provision, has_insurance_reserve, inventory, advance_receipts, total_assets
- 派生指标：roe_3yr_avg, roe_5yr_avg, net_margin_3yr_avg, fcf_positive_years, consecutive_loss_years
- 报告期：report_period

---

## 6. 测试计划

### 6.1 单元测试覆盖

| 测试类 | 覆盖内容 |
|-------|---------|
| TestHardRuleProbes | 6 条硬规则的正向/负向测试 |
| TestJSONExtraction | `<think>` 块处理、代码块提取、裸 JSON 提取、错误处理 |
| TestValuationConfig | 权重归一化、非法方法拒绝、空权重自动均分 |
| TestUnifiedEntry | 兜底永不失败、get_valuation_config 永远返回 |

### 6.2 集成测试标的

| 股票代码 | 名称 | 预期 regime | 预期 source |
|---------|------|------------|-------------|
| 601398.SH | 工商银行 | bank | hard_rule |
| 601318.SH | 中国平安 | insurance | hard_rule |
| 001979.SZ | 招商蛇口 | real_estate | hard_rule |
| 600519.SH | 贵州茅台 | brand_moat | hard_rule |
| 688235.SH | 百济神州 | pharma_innovative | hard_rule/llm |

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
