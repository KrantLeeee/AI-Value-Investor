# AI Value Investor — 项目路线图与开发进度

> **版本**: v2.0 | **创建日期**: 2026-03-04 | **最后更新**: 2026-03-04
> **市场范围**: 当前仅A股。港股/美股列入后续规划。

---

## 一、项目现状概述

### 已完成功能（v1.0）
- [x] 数据层：AKShare/BaoStock/yfinance/FMP多源适配器 + SQLite存储
- [x] 5个分析Agent：Fundamentals(纯代码) / Valuation(代码+LLM) / Buffett(LLM) / Graham(LLM) / Sentiment(LLM)
- [x] 信号聚合 + 报告生成（LLM撰写Markdown）
- [x] 因子筛选器 + 因子回测引擎
- [x] CLI命令体系（fetch/scan/report/backtest/invest/status）
- [x] GitHub Actions自动化（每日扫描+每周报告）
- [x] Telegram通知推送

### v1.0核心问题（用户反馈，已确认）
1. **无数据质量层**：AKShare原始数据直接进入分析，无清洗/异常检测
2. **置信度不可信**：全部Agent置信度落在50-55%，本质是硬编码+LLM幻觉
3. **信号聚合黑箱**：简单多数投票，无Agent权重差异化
4. **报告过短**：1082字符，缺结构、缺风险章节、缺敏感性分析
5. **无反向检验**：没有Devil's Advocate机制，只有确认偏误
6. **基本面评分无行业适配**：所有行业用统一ROE/净利率阈值
7. **DCF假设硬编码且不透明**：WACC=10%适用所有公司
8. **情绪Agent薄弱**：无新闻来源分级，无时间衰减
9. **无预测追踪**：无法校准系统准确性

---

## 二、v2.0 改进计划

### 开发优先级与依赖关系

```
P0-① 数据质量层 ──────────────────┐
P0-② Contrarian Agent ────────────┤
                                   ├──→ P0-③ 报告结构化输出
P1-④ 置信度引擎 ──┐               │
P1-⑤ 行业分类+权重 ┴──→ P1-⑥ 信号聚合器 ──→ (集成到报告)
                                   │
P2-⑦ DCF/WACC改进 ────────────────┤
P2-⑧ 可比公司 ────────────────────┘
                                   
P3-⑨ 预测追踪 + 权重校准（独立，需12个月数据积累）
```

### 时间线

| 周次 | 交付物 | 状态 |
|------|--------|------|
| Week 1-2 | P0-① 数据质量层 + P0-② Contrarian Agent | 🔲 未开始 |
| Week 2-3 | P0-③ 报告结构化JSON输出+逐章验证 | 🔲 未开始 |
| Week 3-4 | P1-④⑤⑥ 置信度+行业分类+信号聚合 | 🔲 未开始 |
| Week 5 | P2-⑦ DCF/WACC行业化+敏感性分析 | 🔲 未开始 |
| Week 6 | P2-⑧ 可比公司（用户输入+AKShare自动） | 🔲 未开始 |
| Week 7 | P3-⑨ 预测追踪系统上线 | 🔲 未开始 |
| +12月 | P3 权重自动校准（需积累≥20条/行业预测） | 🔲 等待数据 |

---

## 三、各模块详细规格

### P0-① 数据质量层

**文件**: `src/data/quality.py`
**集成点**: `registry.py` 的 `run_all_agents()` 开头调用
**输出**: `QualityReport` 对象，传给每个Agent和报告生成器

**11条检查规则**:

| # | 检查 | 逻辑 | severity | 理由 |
|---|------|------|----------|------|
| 1 | 财报新鲜度 | 最新period_end_date距今>15月 | critical | 年报最迟4月底出 |
| 2 | 价格新鲜度 | 最新价格>3交易日前 | warning | 停牌/退市信号 |
| 3 | 收入/利润异常波动 | YoY变动>±80%且绝对值>5亿 | warning | 疑似一次性项目，标注让用户判断 |
| 4 | NI vs OCF背离 | NI>0但OCF<0连续2年 | warning | 盈利质量差信号 |
| 5 | 负净资产 | total_equity<0 | critical | ROE/BVPS等指标无效 |
| 6 | 关键字段缺失 | 逐一检查12个核心字段 | 按缺失量分级 | 缺字段→降置信度 |
| 7 | FCF近似标记 | 当前fcf=ocf+inv_cf | info | 告知下游这是估算值 |
| 8 | EPS交叉验证 | abs(eps - ni/shares)/eps > 0.1 | warning | 数据源内部不一致 |
| 9 | 重复报告期 | 同ticker同期多条 | warning | upsert可能脏数据 |
| 10 | 量级校验 | 营收<净利润 | critical | 单位转换可能出错 |
| 11 | 数据源一致性 | 同期不同source数据差异>20% | warning | 标注以哪个为准 |

**QualityReport结构**:
```python
@dataclass
class QualityReport:
    ticker: str
    check_date: date
    flags: list[dict]  # {"flag": str, "field": str, "detail": str, "severity": str}
    overall_quality_score: float  # 0.0-1.0，每个flag按severity扣分
    data_completeness: float  # 可用字段数/总字段数
    stale_fields: list[str]  # 过期的字段列表
```

**关键设计决策**:
- 异常标注但不自动排除——用户可通过手动上传年报PDF补充context
- quality_score乘法影响置信度（不是加法）
- 报告附录展示完整数据质量评估

---

### P0-② Contrarian Agent（辩证分析师）

**文件**: `src/agents/contrarian.py`
**执行阶段**: Phase 3（在所有其他Agent之后，报告生成之前）
**LLM任务名**: `contrarian_analysis`
**模型**: gpt-4o, max_tokens=2500, temperature=0.3

**三种动态模式**:

| 前序信号共识 | 模式 | Contrarian做什么 |
|------------|------|-----------------|
| ≥60%看多 | BEAR_CASE | 逐条反驳多头论点，构建3个下行风险场景，给出悲观目标价 |
| ≥60%看空 | BULL_CASE | 寻找被忽视的上行催化剂，检查"坏消息是否已price-in" |
| 无明确共识 | CRITICAL_QUESTIONS | 指出核心矛盾，列3个决定方向的关键问题 |

**共识计算**:
```python
def _determine_consensus(signals):
    bull = sum(1 for s in signals.values() if s and s.signal == "bullish")
    bear = sum(1 for s in signals.values() if s and s.signal == "bearish")
    total = sum(1 for s in signals.values() if s)
    bull_ratio = bull / total if total else 0
    bear_ratio = bear / total if total else 0
    if bull_ratio >= 0.6: return "bullish", bull_ratio
    if bear_ratio >= 0.6: return "bearish", bear_ratio
    return "mixed", max(bull_ratio, bear_ratio)
```

**JSON输出结构** (BEAR_CASE模式):
```json
{
  "mode": "bear_case",
  "assumption_challenges": [
    {"original_claim": "...", "challenge": "...", "impact_if_wrong": "..."}
  ],
  "risk_scenarios": [
    {"scenario": "...", "probability": "20-30%", "impact": "...", "precedent": "..."}
  ],
  "bear_case_target_price": 12.50,
  "dividend_sustainability": "...",
  "reasoning": "..."
}
```

**Prompt构建**: 将前序Agent中最强方向的论据extraction后传入，让Contrarian针对性攻击，而非泛泛而谈。

---

### P0-③ 报告结构化输出

**改造文件**: `src/agents/report_generator.py` (重写)
**新增文件**: `templates/report_template.md` (Jinja2模板)
**核心架构变化**: 从"1次LLM调用生成全文"改为"逐章生成+验证+模板渲染"

**7章结构**:

| 章节 | 生成方式 | 验证规则 | LLM任务名 | max_tokens |
|------|---------|---------|-----------|-----------|
| Ch1 行业背景 | LLM | ≥400字 | report_ch1 | 1500 |
| Ch2 竞争力分析 | LLM | ≥500字,含"护城河"或"竞争" | report_ch2 | 2000 |
| Ch3 财务质量 | 纯代码 | 含3个数据表格 | - | - |
| Ch4 估值分析 | 纯代码 | 含估值对比表+敏感性矩阵 | - | - |
| Ch5 风险因素 | Contrarian输出直接渲染 | ≥1个风险场景 | - | - |
| Ch6 市场情绪 | LLM | ≥200字 | report_ch6 | 800 |
| Ch7 综合建议 | LLM | ≥300字,含推荐+目标价 | report_ch7 | 1500 |
| 附录 | 纯代码 | Agent信号表+数据质量+估值假设 | - | - |

**验证+重试逻辑**:
```python
def _generate_chapter(key, system, user, max_retries=2):
    reqs = CHAPTER_REQUIREMENTS[key]
    for attempt in range(max_retries + 1):
        text = call_llm(f"report_{key}", system, user)
        issues = validate_chapter(text, reqs)
        if not issues:
            return text
        if attempt < max_retries:
            user += f"\n[重试] 上次未通过: {issues}"
    return text + f"\n> ⚠️ 质量验证未通过: {issues}"
```

**成本预估**: ~$0.05/报告（4个LLM调用），用户已确认$1以内可接受。

---

### P1-④ 置信度引擎

**文件**: `src/agents/confidence.py`

**计算公式**:
```
final = min(0.85, max(0.10, signal_strength × 0.5 + indicator_agreement × 0.5)) × data_quality_score
```

- **0.85上限**: 未经历史校准前不表达>85%信心（参考Tetlock超级预测研究）
- **0.10下限**: 有数据比没数据强
- **data_quality_score**: 来自P0-①的QualityReport，乘法惩罚
- **historical_calibration**: P3交付后才有，之前为None不参与

**各Agent的signal_strength和indicator_agreement计算**:

| Agent | signal_strength来源 | indicator_agreement来源 |
|-------|-------------------|----------------------|
| Fundamentals | \|score - 57.5\| / 57.5 (偏离中性点幅度) | 4个子维度方向一致性 |
| Valuation | margin_of_safety绝对值 | DCF/Graham/EV-EBITDA一致性 |
| Buffett | ROE一致性+NI稳定性代码预判 | 代码预判 vs LLM判断一致? |
| Graham | 通过标准占比 | 各标准方向一致性 |
| Sentiment | 正面vs负面新闻比例极端性 | 不同来源情绪一致性 |

---

### P1-⑤ 行业分类与权重

**文件**: `src/agents/industry_classifier.py`, `config/industry_profiles.yaml`

**行业识别方式**: 读取watchlist.yaml中的`sector`字段 + AKShare行业板块API交叉验证

**权重表（附理由，标注未经实证验证）**:

```yaml
# config/industry_profiles.yaml
industry_profiles:
  energy:
    weights: {fundamentals: 0.25, valuation: 0.30, buffett: 0.15, graham: 0.20, sentiment: 0.10}
    rationale: "资源股估值对油价极敏感(→高valuation权重)，护城河非差异化因素(→低buffett)，情绪主要反映油价非公司基本面"
    validated: false  # 需P3回测校准
    
    # 基本面评分阈值调整
    scoring:
      roe_thresholds: [15, 10, 6]  # 重资产行业ROE门槛降低
      net_margin_thresholds: [12, 6, 3]
      de_thresholds: [0.5, 0.8, 1.5]  # 资源股杠杆通常较高
      growth_weight: 0.15  # 周期股增长不稳定，降低权重
      cash_quality_weight: 0.30  # 现金流对资源股极重要

  consumer:
    weights: {fundamentals: 0.20, valuation: 0.15, buffett: 0.35, graham: 0.10, sentiment: 0.20}
    rationale: "消费品核心壁垒是品牌+渠道(→高buffett)，估值因稳定增长较少波动"
    validated: false
    scoring:
      roe_thresholds: [25, 20, 15]  # 消费品ROE门槛更高
      growth_weight: 0.30

  tech:
    weights: {fundamentals: 0.15, valuation: 0.25, buffett: 0.20, graham: 0.10, sentiment: 0.30}
    rationale: "科技股市场情绪影响极大，基本面变化快(YoY参考有限)"
    validated: false

  banking:
    weights: {fundamentals: 0.30, valuation: 0.20, buffett: 0.10, graham: 0.30, sentiment: 0.10}
    rationale: "银行核心看资产质量,格雷厄姆框架(低PE/PB+稳定分红)天然适用"
    validated: false

  default:
    weights: {fundamentals: 0.25, valuation: 0.25, buffett: 0.20, graham: 0.15, sentiment: 0.15}
    rationale: "均等分布偏保守"
    validated: false
```

> **注意**: 所有权重标注`validated: false`。P3预测追踪系统积累足够数据（每行业≥20条3个月预测记录）后，可通过逻辑回归自动校准。

---

### P1-⑥ 信号聚合器

**文件**: `src/agents/signal_aggregator.py`

**聚合逻辑**:
1. 信号数值化: bullish=+1, neutral=0, bearish=-1
2. 加权分: `Σ(signal_num × weight × confidence)`
3. 冲突检测: 两个Agent信号相反且各自confidence>0.6 → 标记冲突
4. 最终信号: score>+0.25→bullish, <-0.25→bearish, 其余neutral
5. 最终置信度: `min(加权avg_confidence, 1 - conflict_penalty)`

---

### P2-⑦ DCF/WACC行业化

**改造文件**: `src/agents/valuation.py`

**WACC计算改进（基于用户提供的A股实证文章）**:

核心公式: `WACC = E/(E+D) × re + D/(E+D) × rd × (1-Tc)`

关键参数A股取值:
- **E**: 总股本 × 最新收盘价（市值，非账面）
- **D**: 有息负债 = 短期借款 + 长期借款 + 应付债券 + 租赁负债 + 一年内到期非流动负债
- **rf**: 10年期国债到期收益率（可从AKShare获取: `ak.bond_zh_us_rate()`）
- **MRP**: A股市场风险溢价取5.5%（文章推荐值）
- **β**: 个股vs沪深300，滚动60个月回归，极端值1%缩尾
- **re = rf + β × MRP**
- **rd**: (利息支出 + 资本化利息) / 平均有息负债
- **Tc**: 实际缴纳所得税 / 利润总额（非法定25%）

**行业WACC参考区间**（来自文章实证数据）:
| 行业 | 典型WACC区间 | 说明 |
|------|-------------|------|
| 新能源/科技 | 6%-8% | 低债务成本+市场看好 |
| 消费品 | 7%-9% | 稳定现金流 |
| 能源/资源 | 8%-10% | β较高+周期波动 |
| 钢铁/传统制造 | 9%-11% | 高β+高杠杆 |
| 银行 | 特殊处理 | 不适用标准WACC框架 |

**敏感性矩阵**: 生成WACC×FCF增长率的二维矩阵，高亮当前假设所在位置。

**当前做不到但标注**: 
- β计算需要60个月历史数据，新股/次新股会fallback到行业平均β
- 资本化利息需要从年报附注获取，AKShare不直接提供，fallback用0

---

### P2-⑧ 可比公司

**文件**: `src/agents/comparable.py`

**两种数据来源**:
1. **用户手动输入**: watchlist.yaml中新增`comparables`字段
2. **AKShare自动获取**: `ak.stock_board_industry_cons_em()` 获取同行业成分股 → 按市值0.3x-3x筛选 → `ak.stock_individual_info_em()` 获取估值指标

```yaml
# watchlist.yaml 新增字段
watchlist:
  a_share:
    - ticker: "601808.SH"
      name: "中海油服"
      sector: "能源"
      sub_industry: "油田服务"  # 新增：细分行业
      comparables:              # 新增：用户指定可比公司(可选)
        - "600583.SH"  # 海油工程
        - "002353.SZ"  # 杰瑞股份  
```

**输出**: 可比公司估值对比表（PE/PB/ROE/股息率/市值），含行业中位数和目标公司百分位排名。

---

### P3-⑨ 预测追踪

**文件**: `src/strategy/prediction_tracker.py`
**存储**: `output/predictions/` 目录下JSON文件

每次生成报告时自动记录预测快照。新增CLI命令:
- `invest track-update` — 回填历史预测的实际价格（每月运行一次）
- `invest track-stats` — 查看各Agent历史准确率

**校准条件**: 每行业≥20条3个月预测记录 → 自动计算Agent准确率 → 按准确率归一化为新权重。

---

## 四、新增/修改文件完整清单

### 新增文件
| 文件 | 所属模块 | 依赖 |
|-----|---------|------|
| `src/data/quality.py` | P0-① | 无 |
| `src/agents/contrarian.py` | P0-② | 所有前序Agent |
| `src/agents/confidence.py` | P1-④ | quality.py |
| `src/agents/signal_aggregator.py` | P1-⑥ | confidence.py, industry_classifier.py |
| `src/agents/industry_classifier.py` | P1-⑤ | watchlist.yaml |
| `src/agents/comparable.py` | P2-⑧ | AKShare |
| `src/strategy/prediction_tracker.py` | P3-⑨ | 无 |
| `config/industry_profiles.yaml` | P1-⑤ | 无 |
| `templates/report_template.md` | P0-③ | 无 |

### 修改文件
| 文件 | 修改内容 | 所属模块 |
|-----|---------|---------|
| `src/agents/registry.py` | 插入quality检查+Contrarian阶段+aggregator | P0/P1 |
| `src/agents/report_generator.py` | **重写**: 逐章JSON+验证+模板渲染 | P0-③ |
| `src/agents/fundamentals.py` | 行业阈值适配+新置信度 | P1-④⑤ |
| `src/agents/valuation.py` | WACC行业化+敏感性矩阵+假设透明 | P2-⑦ |
| `src/agents/warren_buffett.py` | 置信度=代码主导+LLM微调 | P1-④ |
| `src/agents/ben_graham.py` | 同上 | P1-④ |
| `src/agents/sentiment.py` | 新闻来源分级(需验证API)+时间衰减 | P1 |
| `src/llm/prompts.py` | Contrarian prompt + 分章节prompt | P0 |
| `src/llm/router.py` | 新增任务路由配置 | P0 |
| `config/llm_config.yaml` | 新增contrarian/report_ch*任务 | P0 |
| `config/watchlist.yaml` | 新增sub_industry+comparables字段 | P2-⑧ |
| `src/data/models.py` | 新增QualityReport/PredictionRecord | P0/P3 |

---

## 五、设计决策记录

| # | 决策 | 理由 | 替代方案 |
|---|------|------|---------|
| 1 | 异常数据标注但不自动排除 | 用户可结合年报PDF补充判断 | 自动排除(风险:误排) |
| 2 | 报告逐章生成而非1次调用 | 可验证每章质量,失败可重试 | 1次调用(风险:质量不可控) |
| 3 | Contrarian动态模式(非固定唱空) | 全场看空时应找忽视的上行机会 | 固定Bear Case(风险:确认偏误) |
| 4 | WACC用市值权重非账面 | WACC参考文章明确推荐+理论正确 | 账面值(严重失真) |
| 5 | 行业权重标注未验证 | 诚实>精致;待P3校准 | 声称已验证(不诚实) |
| 6 | 置信度上限0.85 | Tetlock研究:未校准预测需收缩 | 无上限(风险:虚假确定性) |
| 7 | 先做A股后做港股美股 | 数据源最成熟+用户主要需求 | 三市场同时(风险:分散精力) |
| 8 | 可比公司支持用户输入 | 自动选取准确率有限,用户更懂行业 | 纯自动(风险:错配) |

---

## 六、开放问题与风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| AKShare新闻API不返回来源字段 | 新闻分级功能降级 | 需实测;降级为仅时间衰减 |
| β计算需60月数据,新股不足 | WACC不准 | fallback行业平均β |
| 资本化利息AKShare不提供 | rd偏低 | fallback用0,报告标注 |
| LLM章节生成仍可能不稳定 | 报告质量波动 | 验证+重试+降级标注 |
| 12个月才能校准权重 | 权重长期为"有依据的假设" | 明确标注,持续积累 |
