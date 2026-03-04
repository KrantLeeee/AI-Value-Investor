# Contrarian Agent 设计文档

> **前身**: Bear Case Agent v1.0
> **版本**: v2.0 | **更新日期**: 2026-03-04
> **核心变化**: 从固定唱空 → 动态辩证（根据共识方向切换 BEAR_CASE / BULL_CASE / CRITICAL_QUESTIONS 三种模式）

## 文档目的

在现有 AI 价值投资研究系统中引入 **辩证分析（Dialectical Analysis）** 机制，弥补「全分析者、无唱反调者」的架构缺陷，降低确认偏误。

### 与 v1.0 Bear Case Agent 的关键差异

| 维度 | v1.0 Bear Case | v2.0 Contrarian |
|:-----|:--------------|:----------------|
| **触发逻辑** | 无论共识方向，始终唱空 | 根据前序共识动态切换模式 |
| **全场看空时** | 仍唱空（无意义） | 切换为 BULL_CASE，找被忽视的上行催化剂 |
| **无共识时** | 仍唱空（偏颇） | 切换为 CRITICAL_QUESTIONS，列出决定方向的关键问题 |
| **攻击目标** | 泛泛挑战所有Agent | 提取当前最强论点，定向攻击 |
| **与报告集成** | Report Generator重新理解 | 结构化JSON直接渲染为报告第五章 |
| **文件名** | `src/agents/bear_case.py` | `src/agents/contrarian.py` |

---

## 第一部分：可衡量性与 Benchmark 设计

### 1.1 核心问题

> 如何让「添加 Contrarian Agent 后的系统改进」**可量化、可复现、可对比**？

### 1.2 关键数据类型

| 类别 | 数据项 | 采集来源 | 用途 |
|:-----|:-------|:---------|:-----|
| **输入快照** | `ticker`, `analysis_date`, 全部 Agent 信号与 reasoning | `agent_signals` 表 + signals dict | 回溯分析时的完整上下文 |
| **共识快照** | 共识方向(bullish/bearish/mixed)、共识强度(0-1)、触发模式 | Contrarian Agent 计算 | 验证模式切换逻辑的合理性 |
| **假设追溯** | DCF 假设（WACC, FCF growth, terminal growth） | Valuation Agent `metrics` 字段 | Contrarian 质疑的锚点 |
| **行业依赖** | 油价、利率、大宗商品等关键宏观变量 | 外部 API 或手动录入 | 压力测试/情景分析 |
| **历史验证** | 报告生成日 → T+1M, T+3M, T+6M, T+12M 股价 | `daily_prices` 表 | 检验「建议 vs 实际」 |
| **决策记录** | `invest` / `hold` / `wait`，用户实际行为 | `portfolio.json` / `decision_log` | 评估系统对真实决策的影响 |

### 1.3 需新增的 Schema / 存储

```sql
-- 报告级元数据（便于后续评估）
CREATE TABLE report_metadata (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    report_date         DATE NOT NULL,
    report_path         TEXT,
    agents_used         TEXT,      -- JSON: ["fundamentals", "valuation", ...]
    contrarian_mode     TEXT,      -- "bear_case" / "bull_case" / "critical_questions" / NULL
    consensus_direction TEXT,      -- "bullish" / "bearish" / "mixed"
    consensus_strength  REAL,      -- 0.0-1.0
    overall_signal      TEXT,
    overall_confidence  REAL,
    data_quality_score  REAL,      -- 来自数据质量层
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker, report_date)
);

-- Contrarian Agent 输出（存入 agent_signals 表，agent_name='contrarian'）
-- 扩展 metrics_json 字段保存结构化输出，格式如下：
-- BEAR_CASE 模式:
-- {
--   "mode": "bear_case",
--   "consensus": {"direction": "bullish", "strength": 0.75},
--   "assumption_challenges": [
--     {
--       "original_claim": "安全边际36%",
--       "assumption_under_attack": "WACC=10%",
--       "challenge": "中海油服Beta=1.3，应适用12%WACC",
--       "if_assumption_wrong": "安全边际缩至8%",
--       "severity": "high",
--       "evidence": "..."
--     }
--   ],
--   "risk_scenarios": [
--     {
--       "scenario": "油价跌至60美元/桶",
--       "trigger_probability": "20-30%",
--       "impact_on_revenue": "-25%至-35%",
--       "historical_precedent": "2020年Q1油价战期间该股跌幅40%"
--     }
--   ],
--   "bear_case_target_price": 12.50,
--   "dividend_sustainability": "..."
-- }
--
-- BULL_CASE 模式:
-- {
--   "mode": "bull_case",
--   "consensus": {"direction": "bearish", "strength": 0.70},
--   "overlooked_positives": [...],
--   "priced_in_analysis": "当前股价已反映...",
--   "survival_advantage": "比同行更能扛周期因为...",
--   "bull_case_target_price": 28.00
-- }
--
-- CRITICAL_QUESTIONS 模式:
-- {
--   "mode": "critical_questions",
--   "consensus": {"direction": "mixed", "strength": 0.45},
--   "core_contradiction": "基本面看空但情绪看多",
--   "must_answer_questions": [
--     {"question": "...", "preliminary_judgment": "...", "evidence_needed": "..."}
--   ]
-- }

-- 后续评估用：报告 vs 实际表现
CREATE TABLE report_performance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    report_date         DATE NOT NULL,
    horizon_months      INTEGER NOT NULL,  -- 1, 3, 6, 12
    price_at_report     REAL,
    price_at_horizon    REAL,
    return_pct          REAL,
    contrarian_mode     TEXT,
    contrarian_severity TEXT,  -- "strong" / "moderate" / "weak"
    UNIQUE(ticker, report_date, horizon_months)
);
```

### 1.4 Benchmark 设计

#### A. 报告质量 Benchmark（人工标定）

| 维度 | 描述 | 标定方式 |
|:-----|:-----|:---------|
| **假设透明度** | 关键假设是否被明确列出 | 人工评分 1–5 |
| **反驳覆盖度** | 估值/ROE/股息等关键论断是否有对应质疑 | 人工检查 checklist |
| **分歧呈现** | 多空分歧是否被清晰呈现 | 人工评分 1–5 |
| **风险提示充分性** | 下行情景是否被讨论 | 人工评分 1–5 |
| **辩证平衡性（新增）** | 是否在主流方向外提供了对立视角 | 人工评分 1–5 |

**报告完整性分数 (RCS)**:
```
RCS = 0.3 × 假设透明度 + 0.25 × 反驳覆盖度 + 0.2 × 分歧呈现 + 0.15 × 风险充分性 + 0.1 × 辩证平衡性
```

**操作方式**：对 20–50 份报告（有/无 Contrarian Agent 各半），由 1–2 名投资背景人员盲测打分，做配对 t 检验。

#### B. 决策质量 Benchmark（滞后验证）

- **指标**：在「建议买入」标的上的 T+6M / T+12M 胜率、平均回报、最大回撤
- **对比组**：
  - **对照组**：无 Contrarian Agent 的报告
  - **实验组**：含 Contrarian Agent 的报告
- **分层**：
  - 按 `overall_confidence` 分层
  - 按 `contrarian_mode`（bear_case / bull_case / critical_questions）分层
  - 按 `contrarian_severity`（strong / moderate / weak）分层

#### C. 模式合理性 Benchmark（新增）

| 检验项 | 方法 | 通过标准 |
|:------|:-----|:---------|
| 模式切换正确性 | 人工审核50份报告的共识判定 + 模式选择 | ≥90%模式选择合理 |
| BULL_CASE信息增量 | 对看空共识报告，检查BULL_CASE是否提出了前序Agent未提及的正面因素 | ≥80%有新增信息 |
| CRITICAL_QUESTIONS可操作性 | 检查提出的问题是否可通过公开信息回答 | ≥70%可查证 |

---

## 第二部分：验证方法论 — 如何证明 Contrarian Agent 有效

### 2.1 验证目标

1. **报告层面**：加入 Contrarian 后，报告是否更「平衡」、更「可质疑」？
2. **决策层面**：基于新报告做出的 invest/hold 决策，事后回报是否更稳健？
3. **辩证层面（新增）**：Contrarian 是否能在看空共识时找到有价值的上行催化剂？
4. **机制层面**：Contrarian 的质疑/发现是否有信息增量（非重复、非空洞）？

### 2.2 待提取的数据

#### 用于报告质量分析

| 数据 | 来源 | 用途 |
|:-----|:-----|:-----|
| 各 Agent 的 `signal`, `confidence`, `reasoning` | `agent_signals` | 对比有/无 Contrarian 时的信号分布 |
| 报告全文 | `output/reports/*.md` | 人工标定、NLP 分析 |
| Contrarian 的 `mode`, `challenges`/`positives`/`questions` | `agent_signals.metrics_json` | 分析质疑维度覆盖度 |
| 共识计算中间结果 | Contrarian 日志 | 验证模式切换逻辑 |

### 2.3 测试分析流程

```
1. 构建 A/B 数据集
   - A 组：历史报告（无 Contrarian）
   - B 组：对同一批 ticker + 同一 analysis_date，重新跑带 Contrarian 的报告

2. 报告质量评估
   - 人工标定：假设透明度、反驳覆盖度、分歧呈现、风险提示、辩证平衡性
   - 自动化：报告长度、负面/中性词占比、显式「风险」「假设」「但是」「然而」出现次数

3. 模式合理性评估（新增）
   - 对每份报告：共识判定是否正确？选择的模式是否合理？
   - 对 BULL_CASE 模式：找到的上行催化剂是否有价值？（人工评审）
   - 对 CRITICAL_QUESTIONS 模式：提出的问题是否切中要害？

4. 滞后绩效评估（需时间积累）
   - 对「建议买入」的标的，按 T+6M / T+12M 计算收益
   - 分层：按 contrarian_mode × contrarian_severity
   - 特别关注：BULL_CASE 预测被忽视的上行机会是否兑现

5. 消融实验（Ablation Study）
   - 仅 Bear Case 模式运行 vs 三模式动态切换
   - 比较 RCS 和 ABS 差异
```

### 2.4 Contrarian Agent 各模式应覆盖的方面

#### BEAR_CASE 模式（前序共识偏多时触发）

| 维度 | 示例质疑 | 数据依赖 |
|:-----|:---------|:---------|
| **估值假设** | DCF 的 FCF 增长假设是否乐观？WACC 是否低估？ | Valuation `metrics` |
| **盈利质量** | ROE 高是否来自一次性收益？ | 利润表、QualityReport |
| **股息持续性** | 历史上行业低迷期是否削减过分红？ | `cash_flows.dividends_paid` |
| **行业周期** | 所处行业处于周期什么位置？ | 行业数据（可选） |
| **护城河质疑** | Buffett Agent 判定的护城河是否有反例？ | Buffett reasoning |
| **安全边际脆弱性** | Graham 的安全边际在极端情景下是否消失？ | Valuation bear scenario |

#### BULL_CASE 模式（前序共识偏空时触发）

| 维度 | 示例发现 | 数据依赖 |
|:-----|:---------|:---------|
| **被忽视的催化剂** | 行业政策转向、新业务放量、成本改善 | 新闻、行业数据 |
| **Price-in 分析** | 当前股价已反映了多少坏消息？ | 估值历史百分位 |
| **生存优势** | 在行业下行中比同行更能扛的原因 | 资产负债表强度 |
| **反转信号** | 管理层增持、回购、大股东行为 | 公开信息 |
| **估值底部** | 历史上类似估值水平后的表现 | `daily_prices` 历史 |

#### CRITICAL_QUESTIONS 模式（无明确共识时触发）

| 维度 | 示例问题 | 数据依赖 |
|:-----|:---------|:---------|
| **核心矛盾** | 基本面和估值信号为什么打架？ | 各Agent信号对比 |
| **决定性变量** | 什么单一因素能打破僵局？ | 行业+宏观 |
| **时间窗口** | 这个矛盾多久能明朗化？ | 财报日历、行业事件 |

### 2.5 成功判据（用于验收）

- **最小可行**：Contrarian 能对每份报告的 ≥3 个关键论断提出有据质疑或发现
- **模式切换**：在全场看空的case中（模拟或真实），Contrarian 正确切换为 BULL_CASE 并出具有信息增量的上行分析
- **理想目标**：在有强烈 Contrarian 信号时，Report Generator 的综合置信度适当调整 ≥0.05，且人工评审认为「分析更全面」
- **长期目标**：T+12M 数据积累后，含 Contrarian 的报告所推荐标的，风险调整收益优于对照组

---

## 第三部分：Contrarian Agent 开发规格

### 3.1 架构定位

- **文件**: `src/agents/contrarian.py`
- **位置**: Phase 3 — 在所有分析Agent（Fundamentals/Valuation/Buffett/Graham/Sentiment）之后，Report Generator 之前
- **输入**: 所有前序 Agent 的 `AgentSignal` + 数据质量层的 `QualityReport`
- **输出**: `AgentSignal`（signal/confidence/reasoning） + 扩展 `metrics`（结构化JSON）
- **LLM任务名**: `contrarian_analysis`
- **模型**: GPT-4o, max_tokens=2500, temperature=0.3

### 3.2 共识计算逻辑

```python
def _determine_consensus(signals: dict[str, AgentSignal]) -> tuple[str, float]:
    """
    计算前序Agent的共识方向和强度。
    
    Args:
        signals: {"fundamentals": AgentSignal, "valuation": AgentSignal, ...}
    
    Returns:
        ("bullish" | "bearish" | "mixed", strength: 0.0-1.0)
    
    逻辑：
        - bullish占比 ≥ 60% → ("bullish", bullish_ratio)
        - bearish占比 ≥ 60% → ("bearish", bearish_ratio)
        - 其他 → ("mixed", max(bullish_ratio, bearish_ratio))
    """
    bull = sum(1 for s in signals.values() if s and s.signal == "bullish")
    bear = sum(1 for s in signals.values() if s and s.signal == "bearish")
    total = sum(1 for s in signals.values() if s)
    if total == 0:
        return "mixed", 0.0
    bull_ratio = bull / total
    bear_ratio = bear / total
    if bull_ratio >= 0.6:
        return "bullish", bull_ratio
    if bear_ratio >= 0.6:
        return "bearish", bear_ratio
    return "mixed", max(bull_ratio, bear_ratio)
```

### 3.3 Prompt 构建逻辑

Prompt 不是固定的，而是根据共识方向**动态生成**：

```python
def _build_contrarian_prompt(consensus, strength, signals, quality_report):
    """
    根据共识方向构建不同的LLM指令。
    关键设计：提取前序Agent中最强方向的论据，作为Contrarian的攻击目标。
    """
    # 1. 提取当前最强论点
    strongest_args = []
    for name, sig in signals.items():
        if sig is None:
            continue
        if consensus == "bullish" and sig.signal == "bullish":
            strongest_args.append(f"[{name}] {sig.reasoning[:200]}")
        elif consensus == "bearish" and sig.signal == "bearish":
            strongest_args.append(f"[{name}] {sig.reasoning[:200]}")
        else:  # mixed → 收集全部
            strongest_args.append(f"[{name}/{sig.signal}] {sig.reasoning[:200]}")

    # 2. 根据模式选择指令
    if consensus == "bullish":
        mode_instruction = BEAR_CASE_INSTRUCTION  # 见下方
    elif consensus == "bearish":
        mode_instruction = BULL_CASE_INSTRUCTION
    else:
        mode_instruction = CRITICAL_QUESTIONS_INSTRUCTION

    # 3. 附加数据质量提醒
    quality_context = _format_quality_flags(quality_report)
    
    return f"""[共识方向: {consensus}, 强度: {strength:.0%}]

{mode_instruction}

--- 当前分析论据（你的攻击/审视对象） ---
{chr(10).join(strongest_args)}

--- 数据质量提醒 ---
{quality_context}

输出严格JSON格式。"""
```

#### BEAR_CASE 指令模板

```
你是投资委员会中的辩证分析师。当前多数分析师看多，你的任务是：

1. 逐条审视以下多头论点，找出每个论点的前提假设漏洞
   - 必须明确指出：原始论断是什么 → 它依赖什么假设 → 如果假设不成立会怎样
2. 构建3个最可能导致投资亏损的风险场景
   - 每个场景需包含：触发概率估计、对营收/利润的影响、历史上是否有先例
3. 质疑估值假设是否过于乐观
   - 特别关注WACC、增长率、终值假设
4. 检查分红/回购的可持续性
   - 历史上行业低迷期有无削减案例

约束：
- 每个质疑必须有具体依据（引用数据或历史事实），不接受空洞反驳
- severity 按 high/medium/low 分级
- 给出你的悲观目标价
```

#### BULL_CASE 指令模板

```
你是投资委员会中的辩证分析师。当前多数分析师看空，你的任务是：

1. 逐条审视以下空头论据，找出过度悲观的成分
   - 哪些负面因素可能已被市场充分定价（price-in）？
2. 寻找被忽视的上行催化剂
   - 行业拐点信号、政策变化、公司自身改善迹象
3. 评估这家公司在行业底部的生存优势
   - 比同行更能扛周期的原因（现金储备、成本结构、客户粘性）
4. 检查是否存在估值底部信号
   - PE/PB历史百分位、大股东/管理层增持、回购计划

约束：
- 不是盲目乐观，而是找到空头论据中的薄弱环节
- 每个正面发现必须有依据
- 给出你的乐观目标价
```

#### CRITICAL_QUESTIONS 指令模板

```
你是投资委员会中的辩证分析师。当前分析信号严重分歧，没有清晰共识。你的任务是：

1. 指出导致分歧的核心矛盾是什么
   - 是估值和基本面的冲突？短期和长期的冲突？定量和定性的冲突？
2. 列出3个必须回答的关键问题
   - 这些问题的答案将决定投资方向
   - 每个问题给出你的初步判断和所需的证据来源
3. 建议用户应如何进一步调研来打破僵局

约束：
- 问题必须是具体的、可通过公开信息查证的
- 不要给出倾向性结论，你的角色是提出正确的问题
```

### 3.4 输出结构设计

三种模式对应三种JSON结构（见 1.3 节 Schema 注释中的完整示例）。

**共同字段**:
- `mode`: "bear_case" | "bull_case" | "critical_questions"
- `consensus`: {"direction": str, "strength": float}
- `reasoning`: 综合论述

**signal 映射规则**:
| 模式 | signal取值 | 逻辑 |
|:-----|:----------|:-----|
| BEAR_CASE | bearish（质疑多头） | 固定 |
| BULL_CASE | bullish（质疑空头） | 固定 |
| CRITICAL_QUESTIONS | neutral（不倾向任一方） | 固定 |

**confidence 计算**: 与其他Agent一致，使用代码主导的置信度引擎（P1-④交付后集成），而非LLM自报。在P1交付前，临时使用 `0.60` 固定值（明确标注为未校准）。

### 3.5 与报告的集成

**核心变化**: Contrarian Agent 的结构化JSON输出直接对应报告的**第五章（风险因素/辩证分析）**，由代码模板渲染，无需Report Generator LLM重新理解。

```python
# 在 report_generator.py 中
def _render_chapter5(contrarian_signal: AgentSignal) -> str:
    """将Contrarian输出直接渲染为报告第五章。"""
    metrics = contrarian_signal.metrics  # JSON dict
    mode = metrics.get("mode", "bear_case")
    
    if mode == "bear_case":
        return _render_bear_case_chapter(metrics)
    elif mode == "bull_case":
        return _render_bull_case_chapter(metrics)
    else:
        return _render_critical_questions_chapter(metrics)

def _render_bear_case_chapter(metrics):
    sections = ["## 五、风险因素与反向检验\n"]
    sections.append("### 5.1 假设质疑\n")
    for challenge in metrics.get("assumption_challenges", []):
        severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        icon = severity_icon.get(challenge.get("severity", "medium"), "🟡")
        sections.append(f"- {icon} **{challenge['original_claim']}**")
        sections.append(f"  - 质疑: {challenge['challenge']}")
        sections.append(f"  - 若假设不成立: {challenge.get('if_assumption_wrong', 'N/A')}\n")
    
    sections.append("### 5.2 下行风险场景\n")
    for scenario in metrics.get("risk_scenarios", []):
        sections.append(f"**场景: {scenario['scenario']}**")
        sections.append(f"- 触发概率: {scenario.get('trigger_probability', 'N/A')}")
        sections.append(f"- 营收影响: {scenario.get('impact_on_revenue', 'N/A')}")
        sections.append(f"- 历史先例: {scenario.get('historical_precedent', '无')}\n")
    
    bear_target = metrics.get("bear_case_target_price")
    if bear_target:
        sections.append(f"**悲观目标价: ¥{bear_target}**\n")
    
    return "\n".join(sections)
```

**综合信号逻辑**: Contrarian 不直接参与信号聚合投票（防止系统性偏移），但：
- 当 BEAR_CASE 模式有 high severity 质疑 ≥2 条 → 对总体置信度施加 -0.05 惩罚
- 当 BULL_CASE 模式发现被忽视的强催化剂 → 在报告中添加「辩证视角」提示
- Ch7（综合建议）的LLM prompt中强制要求引用Contrarian的至少1条核心发现

### 3.6 与现有 Valuation 的配合

- Valuation 已有 `dcf_bear` 情景，但假设是**固定的**（如 FCF growth 2%）
- Contrarian (BEAR_CASE模式) 的职责是：**质疑这些固定假设是否足够悲观**
- 例如：Valuation 的 bear 可能只是「增长率下降」，Contrarian 则追问「若油价暴跌，增长率会不会变负？历史上有过吗？」
- P2-⑦ DCF/WACC行业化完成后，Contrarian 可以引用敏感性矩阵中的极端格来构建风险场景

### 3.7 与数据质量层的配合

Contrarian 接收 `QualityReport` 作为输入：
- 数据质量低 → Prompt 中额外提醒："以下分析基于低质量数据(score={X})，质疑力度应加大"
- 存在 `NON_RECURRING_ITEM` 标记 → 自动在质疑维度中加入"盈利质量"
- 存在 `STALE_DATA` 标记 → 在质疑中加入"数据时效性风险"

---

## 第四部分：开发阶段规划

| 阶段 | 任务 | 交付物 | 依赖 |
|:-----|:-----|:-------|:-----|
| **Phase 1** | 共识计算 + 三模式Prompt构建 + LLM调用 + JSON解析 | `src/agents/contrarian.py` | 无（可独立开发） |
| **Phase 2** | 接入 Registry（Phase 3 位置），确保接收所有前序信号 | 更新 `registry.py` | Phase 1 |
| **Phase 3** | LLM路由配置 + Prompt模板 | 更新 `llm_config.yaml`, `prompts.py` | Phase 1 |
| **Phase 4** | 报告第五章渲染逻辑 | 更新 `report_generator.py` | Phase 1 |
| **Phase 5** | Schema迁移（report_metadata, report_performance） | DB迁移脚本 | Phase 2 |
| **Phase 6** | Benchmark流水线（滞后价格回填） | `scripts/backfill_performance.py` | Phase 5，P3-⑨ |

### 依赖与配置

- **LLM**: `contrarian_analysis` 加入 `task_routing`，使用 GPT-4o, max_tokens=2500
- **数据**: 需能读取所有前序Agent信号 + QualityReport
- **可选增强**: 若接入油价等宏观数据API，可做更精准的stress test，但MVP仅基于财报+公开常识

---

## 附录A：与 601808 案例的对应关系

以中海油服报告为例，**假设前序Agent共识为bullish（4/5看多）**，Contrarian 进入 BEAR_CASE 模式：

| 报告中出现的论断 | Contrarian 应提出的质疑 | severity |
|:-----------------|:-----------------------|:---------|
| 安全边际 36%（基于 DCF, WACC=10%） | β=1.3的油服公司应使用WACC≥11%，调整后安全边际可能<15% | high |
| ROE 27% 很高 | 是否包含资产减值转回或一次性收益？（数据质量层标记了异常波动） | high |
| 股息可持续 | 2015-2016年油价低迷期分红记录？历史派息率? | medium |
| 市场情绪极度正面 | 情绪与估值高点重叠时，历史上往往是买入陷阱而非机会 | medium |

**若假设前序Agent共识为bearish（4/5看空）**，Contrarian 进入 BULL_CASE 模式：

| 空头论据 | Contrarian 应找的上行因素 |
|:---------|:------------------------|
| 油价可能下跌 | 当前估值已处于历史PE 10%分位，坏消息可能已price-in |
| 行业周期下行 | 公司现金储备/资产负债表强度是否优于同行，能否扛过周期 |
| 增长放缓 | 海外业务占比上升是否是被忽视的增长点 |

---

## 附录B：变更日志

| 版本 | 日期 | 变更内容 |
|:-----|:-----|:---------|
| v1.0 | 2026-03-04 | 初版 Bear Case Agent 设计 |
| v2.0 | 2026-03-04 | 重构为 Contrarian Agent：新增BULL_CASE/CRITICAL_QUESTIONS模式、动态Prompt、共识计算逻辑、QualityReport集成、报告直接渲染 |

---

*文档版本：v2.0 | 最后更新：2026-03-04*
