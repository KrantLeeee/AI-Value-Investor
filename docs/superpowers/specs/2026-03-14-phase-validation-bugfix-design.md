# Phase 1/2/3 验收问题修复设计方案

**版本**: v1.0
**日期**: 2026-03-14
**目标**: 修复批量报告验收中发现的所有问题，确保估值系统正确识别行业、应用合适的估值方法、并生成可信的投资分析

---

## 1. 问题总览

根据两次批量报告执行日志（`batch_execution.log` 和 `batch_execution_strict.log`）的分析，共发现 **8个问题**，分为3个优先级：

### P0 关键问题（影响投资决策）
| # | 问题 | 受影响标的 | 根源 |
|---|------|-----------|------|
| 1 | 宁德时代被误用"医药生物18x倍数" | 300750 | `classify_industry()` 未优先检查 `PRIORITY_KEYWORDS` |
| 2 | 工业富联被误分类为"亏损期科技股" | 601138 | `detect_loss_making_tech_stock()` 条件过于宽松 |
| 3 | 茅台护城河P/E被异常检测排除 | 600519 | 异常检测阈值60%对护城河公司不适用 |

### P1 重要问题（功能未生效）
| # | 问题 | 受影响标的 | 根源 |
|---|------|-----------|------|
| 4 | 中国移动Graham仍在运行 | 600941 | `disable_methods` 配置未被读取 |
| 5 | 周期行业评分豁免未实现 | 牧原股份 | `scoring_mode: cycle_adjusted` 未实现 |
| 6 | 困境企业框架未触发 | 永辉超市 | 困境检测后估值逻辑未执行 |

### P2 次要问题（边缘情况）
| # | 问题 | 受影响标的 | 根源 |
|---|------|-----------|------|
| 7 | 辩证分析NoneType崩溃（残留） | 网达软件、行动教育 | 提示拼接中还有None未处理 |
| 8 | 保利发展DCF对房地产不适用 | 001979 | 房地产应禁用DCF或使用收缩假设 |

---

## 2. 修复方案设计

### 2.1 P0-1: 宁德时代行业误识别

**问题分析**:
- `valuation.py:974` 调用 `get_industry_from_watchlist(ticker)` 获取行业
- 若返回 `"default"`，则调用 `classify_industry(sector)`
- `classify_industry()` 函数（`industry_classifier.py:364-393`）使用 YAML 中的 `applies_to` 关键词匹配
- 但 YAML 的 `pharma_innovative.applies_to` 包含 "生物"，而宁德时代的行业描述中可能包含该词（如"新能源生物"误匹配）
- **核心问题**: `PRIORITY_KEYWORDS` 定义了正确的优先关键词，但 `classify_industry()` 没有使用它

**修复方案**:
```python
# industry_classifier.py:364-393

def classify_industry(sector: str | None, sub_industry: str | None = None) -> str:
    """Classify stock into industry category."""
    if not sector:
        return "default"

    # 1. 优先检查 PRIORITY_KEYWORDS（新能源制造等）
    search_text = (sector + " " + (sub_industry or ""))
    for industry_type, keywords in PRIORITY_KEYWORDS.items():
        if any(kw in search_text for kw in keywords):
            logger.info(f"[Industry] Priority matched '{sector}' as '{industry_type}'")
            return industry_type

    # 2. 检查 INDUSTRY_KEYWORDS
    for industry_type, keywords_dict in INDUSTRY_KEYWORDS.items():
        primary = keywords_dict.get('primary', [])
        secondary = keywords_dict.get('secondary', [])
        negative = keywords_dict.get('negative', [])

        # 检查负面关键词
        if any(neg in search_text for neg in negative):
            continue

        if any(kw in search_text for kw in primary + secondary):
            logger.info(f"[Industry] Matched '{sector}' as '{industry_type}'")
            return industry_type

    # 3. 检查 YAML applies_to
    _, keywords = _load_profiles()
    for industry, keyword_list in keywords.items():
        for keyword in keyword_list:
            if keyword in search_text.lower():
                logger.info(f"[Industry] YAML matched '{sector}' as '{industry}'")
                return industry

    logger.warning(f"[Industry] No match for '{sector}', using default")
    return "default"
```

**测试验证**:
```bash
poetry run invest report --ticker 300750.SZ  # 应显示 new_energy_mfg
```

---

### 2.2 P0-2: 工业富联被误分类为亏损期科技股

**问题分析**:
- `detect_loss_making_tech_stock()` 的条件（`industry_classifier.py:714-750`）：
  - `net_margin < 0.05` 即触发 → 工业富联净利率3.9%满足此条件
  - `revenue_growth >= 0.15` → 工业富联营收增长48%满足
  - 但**工业富联净利润353亿，ROE 21.6%，明显是盈利公司**

**根因**: 代码只检查净利率，没有检查净利润绝对值是否显著为正

**修复方案**:
```python
# industry_classifier.py:686-750

def detect_loss_making_tech_stock(
    net_income: float | None,
    net_margin: float | None,
    revenue_growth: float | None,
    rd_ratio: float | None = None,
    industry: str | None = None,
    roe: float | None = None,  # 新增参数
) -> bool:
    """
    BUG-03A修复: 检测真正的亏损期科技股

    关键修复: 必须同时满足以下条件才算亏损期科技股：
    1. 净利润 <= 0 或 净利率 < 2%（不是5%！工业富联3.9%利润率但净利353亿）
    2. ROE < 5%（真正亏损的公司ROE一定很低或为负）
    3. 营收高增长 >= 15%（有成长潜力才值得用PS估值）
    """
    # 条件1: 真正亏损或微利（不是低利润率高利润额的制造业）
    is_truly_loss_making = False

    if net_income is not None and net_income <= 0:
        is_truly_loss_making = True
    elif net_margin is not None and net_margin < 0.02:  # 2%阈值，不是5%
        is_truly_loss_making = True

    if not is_truly_loss_making:
        return False

    # 条件2: ROE必须很低（盈利公司ROE > 10%不应被分类为亏损期）
    if roe is not None and roe > 0.05:  # ROE > 5% 说明是盈利公司
        logger.debug(
            f"[Industry] Net margin low but ROE={roe*100:.1f}% > 5%, "
            f"not a loss-making tech stock"
        )
        return False

    # 条件3: 必须有高增长（否则只是失败的公司）
    if revenue_growth is None or revenue_growth < 0.15:
        logger.debug(f"[Industry] Loss-making but low growth, not a growth tech")
        return False

    # ... 其余逻辑保持不变
```

**测试验证**:
```bash
poetry run invest report --ticker 601138.SH  # 不应显示"亏损期科技股"
```

---

### 2.3 P0-3: 茅台护城河P/E被异常检测排除

**问题分析**:
- `_validate_valuation_result()` 使用60%偏离中位数阈值排除异常
- 茅台护城河P/E（premium档30-40x）计算的目标价偏离中位数85.5%
- **问题**: 护城河P/E方法本就应该给出高于其他方法的估值，异常检测不应排除

**修复方案**:
```python
# valuation.py:573-608

def get_outlier_threshold(industry_type: str, method_name: str = None) -> float:
    """
    Get outlier threshold based on industry type AND method name.

    护城河P/E方法需要放宽阈值，因为它设计上就会给出高于中位数的估值。
    """
    # 护城河P/E方法使用更宽松的阈值
    if method_name == "P/E_Moat":
        return 1.0  # 100% deviation allowed for moat P/E

    thresholds = {
        'new_energy_mfg': 1.5,
        'pharma_innovative': 1.5,
        'auto_new_energy': 1.5,
        'brand_moat': 0.8,  # 护城河公司整体放宽到80%
        'default': 0.6,
    }
    return thresholds.get(industry_type, thresholds['default'])
```

同时修改 `_validate_valuation_result()` 调用：
```python
# valuation.py:2186-2198

for method_name, target_price in valuation_methods:
    # 为护城河P/E方法传递方法名以获取正确阈值
    threshold = get_outlier_threshold(
        industry_type_for_threshold,
        method_name=method_name if method_name == "P/E_Moat" else None
    )
    validation = _validate_valuation_result(
        method_name=method_name,
        target_price=target_price,
        current_price=current_price,
        all_results=all_target_prices,
        industry_type=industry_type_for_threshold,
        outlier_threshold=threshold,  # 新增参数
    )
```

---

### 2.4 P1-4: 中国移动Graham Number仍在运行

**问题分析**:
- `industry_profiles.yaml` 中 `telecom_operator.disable_methods: ["graham_number"]`
- 但 `valuation.py` 从未读取该配置

**修复方案**:
```python
# valuation.py - 在 run() 函数开头添加

def _get_disabled_methods(industry: str) -> list[str]:
    """从 industry_profiles.yaml 获取应禁用的估值方法"""
    try:
        from src.agents.industry_classifier import get_industry_profile
        profile = get_industry_profile(industry)
        return profile.get("disable_methods", [])
    except Exception:
        return []

# 在 valuation methods 构建前调用
disabled_methods = _get_disabled_methods(industry_class)

# 修改估值方法添加逻辑
if "graham_number" not in disabled_methods:
    if graham_number_per_share:
        valuation_methods.append(("Graham Number", graham_number_per_share))
```

**修改位置**: `valuation.py` 约第1820行起的方法选择逻辑

---

### 2.5 P1-5: 周期行业评分豁免未实现

**问题分析**:
- `industry_profiles.yaml` 中 `cyclical_agri.scoring_mode: "cycle_adjusted"`
- `fundamentals.py` 没有实现该模式的评分逻辑

**修复方案**:
```python
# src/agents/fundamentals.py - 新增函数

def _calculate_cycle_adjusted_score(
    metrics: dict,
    income_history: list[dict],
    normalized_years: int = 5
) -> dict:
    """
    周期调整评分：使用5年平均值而非当年值

    适用于: 农业养殖、矿业、钢铁等强周期行业
    """
    # 计算5年平均ROE
    roe_history = [m.get('roe') for m in income_history if m.get('roe')]
    avg_roe = sum(roe_history) / len(roe_history) if roe_history else None

    # 计算5年平均净利
    ni_history = [m.get('net_income') for m in income_history if m.get('net_income')]
    avg_ni = sum(ni_history) / len(ni_history) if ni_history else None

    # 使用平均值计算评分
    adjusted_metrics = metrics.copy()
    if avg_roe:
        adjusted_metrics['roe'] = avg_roe
    if avg_ni and metrics.get('net_income'):
        # 净利YoY使用当年vs5年平均，而非当年vs去年
        adjusted_metrics['ni_yoy'] = (metrics['net_income'] - avg_ni) / abs(avg_ni) if avg_ni else 0

    return adjusted_metrics
```

---

### 2.6 P1-6: 困境企业框架未触发

**问题分析**:
- `detect_distressed_company()` 能正确检测到永辉超市
- 但检测后的估值逻辑在 `valuation.py` 中是空的

**修复方案**:
```python
# valuation.py - 在困境企业检测后添加估值逻辑

if is_distressed:
    distressed_type = classify_distressed_type(company_info, distressed_metrics)
    results["distressed_type"] = distressed_type

    # 根据困境类型选择估值方法
    distressed_config = DISTRESSED_CATEGORIES.get(distressed_type, DISTRESSED_CATEGORIES['generic_distressed'])

    if distressed_config['valuation_method'] == 'asset_replacement':
        # 资产重置价值估值
        if total_assets and revenue:
            ev_sales = revenue * distressed_config['ev_sales_multiple']
            distressed_value_per_share = ev_sales / shares if shares else None
            valuation_methods.append(("EV/Sales_Distressed", distressed_value_per_share))
            detail_lines.append(
                f"⚠ 困境企业估值 ({distressed_type}): "
                f"EV/Sales={distressed_config['ev_sales_multiple']}x → "
                f"¥{distressed_value_per_share:.2f}/股"
            )

    # 禁用标准DCF（困境企业DCF假设不成立）
    disabled_methods.append("DCF")
```

---

### 2.7 P2-7: 辩证分析NoneType崩溃（残留）

**问题分析**:
- 错误信息: `can only concatenate str (not "NoneType") to str`
- 位置: `contrarian.py:_build_prompt()` 的用户提示模板填充

**修复方案**:
```python
# contrarian.py:249-293 - 增强None处理

def _build_prompt(...) -> tuple[str, str]:
    # 使用 safe_format 统一处理所有可能为 None 的值
    ticker = safe_format(company_context.get("ticker") if company_context else None, default="N/A")
    industry = safe_format(company_context.get("sector") if company_context else None, default="未知行业")
    analysis_date = safe_format(
        company_context.get("analysis_date") if company_context else None,
        default="2026-03-14"
    )

    # 确保所有模板变量都有默认值
    template_vars = {
        'ticker': ticker,
        'industry': industry,
        'analysis_date': analysis_date,
        'consensus_direction': safe_format(consensus_direction, default="mixed"),
        'consensus_strength': safe_format(consensus_strength, fmt="{:.0%}", default="0%"),
        'strongest_arguments': arguments_text or "（无论据）",
        'all_arguments': arguments_text or "（无论据）",
        'quality_context': quality_context or "（无质量报告）",
        'macro_industry_context': macro_industry_context or "（无宏观背景）",
    }

    try:
        user_prompt = user_template.format(**template_vars)
    except KeyError as e:
        logger.error(f"[Contrarian] Template missing key: {e}")
        # 降级到简化模板
        user_prompt = f"请分析 {ticker} ({industry}) 的投资风险和机会。"
```

---

### 2.8 P2-8: 保利发展DCF对房地产不适用

**问题分析**:
- 房地产商在收缩周期中，DCF的增长假设不成立
- 当前P/B封顶生效，但DCF仍给出¥33目标价

**修复方案**:
```python
# valuation.py - 房地产行业特殊处理

# 在 industry_profiles.yaml 中添加
real_estate:
    disable_methods: ["dcf", "graham_number"]  # 房地产禁用DCF
    methods: ["pb"]
    pb_multiple_cap: 0.5

# 在 valuation.py 中确保 disable_methods 被应用
if is_real_estate_industry(industry):
    disabled_methods.extend(["dcf", "graham_number"])
    logger.info(f"[Valuation] {ticker}: 房地产行业，禁用DCF和Graham")
```

---

## 3. 修改文件清单

| 文件 | 修改类型 | 影响范围 |
|------|---------|----------|
| `src/agents/industry_classifier.py` | 修改 | `classify_industry()`, `detect_loss_making_tech_stock()` |
| `src/agents/valuation.py` | 修改 | `run()`, `get_outlier_threshold()`, `_validate_valuation_result()` |
| `src/agents/fundamentals.py` | 新增 | `_calculate_cycle_adjusted_score()` |
| `src/agents/contrarian.py` | 修改 | `_build_prompt()` |
| `config/industry_profiles.yaml` | 修改 | `real_estate.disable_methods` |

---

## 4. 测试计划

### 单元测试
```bash
# 新增测试用例
tests/test_industry_classifier.py::test_priority_keywords_override_yaml
tests/test_industry_classifier.py::test_profitable_company_not_classified_as_loss_making
tests/test_valuation.py::test_moat_pe_not_excluded_by_outlier
tests/test_valuation.py::test_disabled_methods_from_yaml
```

### 集成测试（批量报告验证）
```bash
# 关键测试标的
poetry run invest report --ticker 300750.SZ  # 宁德时代 → 应为 new_energy_mfg
poetry run invest report --ticker 601138.SH  # 工业富联 → 不应为 loss_making_tech
poetry run invest report --ticker 600519.SH  # 茅台 → P/E_Moat 应参与加权
poetry run invest report --ticker 600941.SH  # 中国移动 → Graham 应被禁用
poetry run invest report --ticker 002714.SZ  # 牧原股份 → 应使用周期调整评分
poetry run invest report --ticker 601933.SH  # 永辉超市 → 应触发困境企业框架
```

### 回归测试
```bash
# 确保修复不破坏现有正确行为
poetry run invest report --ticker 002304.SZ  # 碧水源 - 之前正常
poetry run invest report --ticker 601318.SH  # 中国平安 - 之前正常
poetry run invest report --ticker 300896.SZ  # 爱美客 - 之前正常
```

---

## 5. 实施顺序

1. **第一批（P0关键问题）**
   - 2.1 宁德时代行业误识别
   - 2.2 工业富联被误分类
   - 2.3 茅台护城河P/E被排除

2. **第二批（P1重要问题）**
   - 2.4 disable_methods配置读取
   - 2.5 周期行业评分豁免
   - 2.6 困境企业框架

3. **第三批（P2次要问题）**
   - 2.7 辩证分析NoneType
   - 2.8 房地产DCF禁用

每批修复后运行对应的测试验证，确保不引入回归问题。

---

## 6. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 行业分类器修改影响其他标的 | 高 | 增加单元测试覆盖，回归测试5+标的 |
| 亏损期检测条件修改过严 | 中 | 保留日志输出，监控边界案例 |
| 异常检测阈值放宽导致噪声 | 低 | 仅对P/E_Moat方法放宽，其他保持60% |

---

**审批**: 请确认方案后开始实施。
