# PROJECT_MAP — AI Value Investor
<!-- 用于 Claude Code / Cursor 快速定位文件，减少无效 token 消耗 -->
<!-- Last Updated: 2026-03-14 | Version: 1.1 -->

## 🗺️ 系统一句话总结
多 Agent 协作的 A 股价值投资研究助手：CLI 触发 → 数据抓取（多源回退）→ 7 个 Agent 并行分析 → Contrarian Agent 辩证挑战 → LLM 生成中文研报 → Telegram 推送。

---

## 🎯 改动场景快速索引（最省 token 的入口！）

| 我想做的事 | 直接去改这些文件 |
|-----------|----------------|
| 添加新估值方法（DCF/PE/PB 等） | `src/agents/valuation.py` + `src/agents/industry_valuation.py` |
| 修改报告章节结构/内容 | `src/agents/report_generator.py` + `src/agents/report_config.py` |
| 修改报告章节的 LLM Prompt | `src/llm/prompts.py` |
| 添加/调整行业分类规则 | `config/industry_profiles.yaml` + `src/agents/industry_classifier.py` |
| **启用 V3 行业引擎** | 设置 `USE_INDUSTRY_ENGINE_V3=true` 环境变量 |
| **修改 V3 硬规则** | `src/agents/industry_engine.py` → `detect_special_regime()` |
| 修改行业估值参数（EV/EBITDA 倍数等） | `config/industry_profiles.yaml` |
| 修改因子筛选规则 | `config/screening_rules.yaml` ⚠️ 受保护 |
| 添加新数据源 | `src/data/` 下新建 `xxx_source.py` + 注册到 `fetcher.py` |
| 修改数据抓取优先级 | `src/data/fetcher.py` → `_SOURCE_PRIORITY` 字典 |
| 修改 Contrarian 分析逻辑 | `src/agents/contrarian.py` |
| 修改各 Agent 的信号权重 | `config/industry_profiles.yaml` → `weights` 节 |
| 修改 Telegram 推送格式 | `src/notification/telegram_notifier.py` |
| 修改 LLM 路由（哪个任务用哪个模型） | `config/llm_config.yaml` ⚠️ 受保护 |
| 添加新的 CLI 命令 | `src/main.py` → 参照现有命令结构添加 |
| 修改数据质量检查规则 | `src/data/quality.py` |
| 修改 WACC 计算参数 | `src/agents/wacc.py` → 顶部常量区 |
| 添加可比公司对比数据 | `src/agents/comparables.py` |
| 修改宏观数据（PMI/PPI）获取 | `src/data/macro_data.py` |
| 添加新 Agent 到报告流程 | `src/agents/registry.py` → `run_all_agents()` |
| 修改看门狗列表（监控股票） | `config/watchlist.yaml` |

---

## 📐 系统架构图

```
用户 CLI
  │
  ├── invest fetch   ──→ [Fetcher] 多源数据抓取 → SQLite 本地库
  ├── invest scan    ──→ [Screener] 规则筛选 → output/signals/
  ├── invest report  ──→ [Registry] 编排所有 Agent
  │                          │
  │                    ┌─────┴──────────────────────────────┐
  │                    ↓                                     ↓
  │             分析 Agents (前5个)               数据质量层
  │        ┌──────────────────────┐          src/data/quality.py
  │        │ fundamentals         │
  │        │ valuation            │
  │        │ warren_buffett       │
  │        │ ben_graham           │
  │        │ sentiment            │
  │        └──────┬───────────────┘
  │               │ signals dict
  │               ↓
  │        [Contrarian Agent] ← 辩证分析，挑战共识
  │               │
  │               ↓
  │        [Signal Aggregator] ← 加权聚合，行业特定权重
  │               │
  │               ↓
  │        [Report Generator] ← LLM 生成中文研报 (8章结构)
  │               │
  │               ↓
  │        output/reports/{ticker}_{date}.md
  │               │
  ├── invest portfolio ──→ [Portfolio] 持仓管理
  └── invest backtest  ──→ [Backtester] 因子回测
                              │
                        [Telegram / Email] 推送通知
```

---

## 📁 模块目录

### 🟢 入口层
---
#### `src/main.py` — CLI 命令入口
- **职责**：所有用户命令的入口（Click 框架），编排数据抓取和报告生成流程
- **关键函数**：`fetch()`, `report()`, `scan()`, `invest()`, `portfolio()`, `status()`, `backtest()`
- **命令→实现映射**：
  - `invest fetch` → 调用 `Fetcher.fetch_all()`
  - `invest report` → 调用 `run_all_agents()` (registry)
  - `invest scan` → 调用 `run_scan()` (screener)
- **改动触发点**：添加新 CLI 命令；修改命令行为逻辑

---

### 🟡 Agent 分析层 (`src/agents/`)
---
#### `src/agents/registry.py` — Agent 总编排器
- **职责**：按正确顺序调用所有分析 Agent，汇总 signals，触发报告生成
- **关键函数**：`run_all_agents(ticker, market, *, quick, use_llm, analysis_date)`
- **执行顺序**：fundamentals → valuation → warren_buffett → ben_graham → sentiment → contrarian → report_generator
- **改动触发点**：添加新 Agent 到分析链；调整 Agent 执行顺序

#### `src/agents/fundamentals.py` — 基本面评分（纯代码，不调 LLM）
- **职责**：100 分制量化基本面评分（盈利30分+成长30分+安全20分+质量20分）
- **关键函数**：`run(ticker, market)` → `AgentSignal`
- **行业适配**：金融股/公用事业股财务比率另设阈值
- **输出字段**：`total_score`, `profitability_score`, `growth_score`, `safety_score`, `quality_score`
- **改动触发点**：调整评分权重；新增评分维度；修改行业豁免规则

#### `src/agents/valuation.py` — 多方法估值引擎（核心最大文件 1678 行）
- **职责**：6+ 种估值方法综合加权，输出目标价和安全边际
- **估值方法**：DCF（三情景）、Graham Number、EV/EBITDA、P/B、P/E、P/S（亏损科技股）、DDM（公用事业）、PEG（成长股）
- **关键函数**：`run(ticker, market, use_llm)` → `AgentSignal`
- **依赖**：`wacc.py`（WACC计算）、`industry_classifier.py`（行业识别）
- **改动触发点**：添加新估值方法；修复估值公式；调整行业适配逻辑
- **注意**：文件顶部有大量行业特定常量（PE倍数、WACC参数等）

#### `src/agents/industry_classifier.py` — 行业分类和参数管理 (V2)
- **职责**：行业识别 + 从 YAML 加载行业特定估值倍数和 Agent 权重
- **关键函数**：
  - `classify_industry(sector, sub_industry)` → 行业 key
  - `get_industry_profile(industry)` → 完整行业配置
  - `detect_loss_making_tech_stock(...)` → 是否亏损科技股
  - `detect_growth_stock(...)` / `detect_financial_stock(...)`
  - `get_ev_ebitda_multiple()`, `get_pe_multiple()`, `get_ps_multiple()`, `get_pb_multiple()`
- **数据来源**：`config/industry_profiles.yaml`
- **改动触发点**：添加新行业类型；修改行业估值参数（改 YAML）；调整分类逻辑

#### `src/agents/industry_engine.py` — V3 行业引擎（三层漏斗架构）⭐ NEW
- **职责**：基于财务特征的行业分类，替代基于标签的 V2 分类器
- **三层架构**：
  1. **硬规则**（零成本）：银行/保险/地产/困境/品牌护城河/创新药 → 即时返回
  2. **LLM 动态路由**：DeepSeek-Reasoner + 缓存，输出 method_importance 权重
  3. **安全回退**：generic 体系，永不失败
- **关键函数**：
  - `get_valuation_config(ticker, company_info, metrics)` → `ValuationConfig`
  - `detect_special_regime(metrics, company_info)` → `SpecialRegimeResult | None`
  - `extract_json_from_llm_output(raw)` → 处理 DeepSeek `<think>` 块
  - `compare_with_legacy(...)` → 并行模式对比 V3/V2 结果
- **Feature Flag**：`USE_INDUSTRY_ENGINE_V3=true` 启用
- **改动触发点**：修改硬规则阈值；添加新特殊行业；调整 LLM Prompt

#### `src/agents/valuation_config.py` — 估值配置模型 ⭐ NEW
- **职责**：`ValuationConfig` Pydantic 模型，method_importance → weights 自动归一化
- **关键字段**：`regime`, `primary_methods`, `weights`, `method_importance`, `confidence`, `source`
- **改动触发点**：添加新估值方法到 `ALLOWED_METHODS`

#### `src/agents/wacc.py` — WACC 计算模块
- **职责**：CAPM 成本权益 + 负债成本 → WACC，生成 DCF 敏感性矩阵
- **关键函数**：`calculate_wacc(ticker, market, industry, current_price)`, `generate_sensitivity_heatmap(...)`, `format_sensitivity_heatmap(...)`
- **常量**：`MRP=0.055`（A 股风险溢价）, `RF_FALLBACK=0.028`
- **改动触发点**：调整风险溢价参数；实现 beta 自动计算（TODO）

#### `src/agents/warren_buffett.py` — 巴菲特框架（LLM 定性分析）
- **职责**：护城河类型 + 管理层质量 + 定价权 → bullish/bearish 信号
- **关键函数**：`run(ticker, market, fundamentals_signal, valuation_signal, use_llm)`
- **LLM 任务**：`buffett_analysis` → `src/llm/prompts.py:BUFFETT_SYSTEM_PROMPT`
- **改动触发点**：调整 Prompt；修改护城河评判维度

#### `src/agents/ben_graham.py` — 格雷厄姆防御性投资准则（LLM + 规则）
- **职责**：7 项格雷厄姆安全准则打分，LLM 综合判断，有信号封顶逻辑
- **关键函数**：`run(ticker, market, valuation_signal, use_llm)`
- **关键机制**：`_apply_signal_cap()` — 通过准则数量硬性限制最高信号等级
- **改动触发点**：修改 Graham 准则判断；调整信号封顶阈值

#### `src/agents/sentiment.py` — 情绪分析（LLM + 规则 + 多源新闻）
- **职责**：Tavily/AKShare/手动文档 → 新闻情绪 + 业绩预告 → sentiment 信号
- **关键函数**：`run(ticker, market, use_llm, use_tavily)`
- **数据来源优先级**：Tavily API → AKShare 新闻 → DB 手动文档
- **改动触发点**：添加新闻来源；修改情绪关键词词表；调整业绩预告处理

#### `src/agents/contrarian.py` — 辩证分析 Agent（LLM）
- **职责**：检测 Agent 共识方向（看多/看空/分歧），挑战共识，生成反方论点
- **关键函数**：`run(ticker, market, *, signals, quality_report, use_llm, company_context)`
- **三种模式**：`bear_case`（挑战看多）、`bull_case`（挑战看空）、`critical_questions`（分歧时提问）
- **关键依赖**：`_determine_consensus()` > `_select_mode()` > `_build_prompt()` > `_call_llm()`
- **改动触发点**：调整反向分析 Prompt；修改共识判断阈值；调整模式切换逻辑

#### `src/agents/signal_aggregator.py` — 信号加权聚合
- **职责**：将多 Agent 信号按行业权重加权，生成最终推荐
- **关键函数**：`aggregate_signals(signals, industry)`, `create_aggregated_signal(ticker, signals, industry)`
- **权重来源**：`industry_classifier.py` → `config/industry_profiles.yaml`
- **改动触发点**：修改聚合算法；添加冲突检测规则

#### `src/agents/report_generator.py` — 研报生成器（主力 LLM 调用，1358 行）
- **职责**：汇总所有 Agent 信号，生成结构化 8 章中文研报
- **报告结构**（章节顺序）：
  1. 行业分析（LLM）
  2. 竞争格局（LLM）
  3. 财务质量（代码生成表格）
  4. 估值分析（代码生成表格 + WACC 热力图）
  5. 风险与辩证分析（Contrarian Agent 输出）
  6. 综合结论（LLM）
  7. 附录（代码生成）
- **关键函数**：`run(ticker, market, *, signals, quality_report, use_llm, company_context)`
- **改动触发点**：修改章节结构(`report_config.py`)；修改 LLM Prompt；调整代码生成节

#### `src/agents/report_config.py` — 报告章节配置
- **职责**：定义报告章节顺序、LLM 章节 key 列表
- **改动触发点**：添加/删除报告章节

#### `src/agents/chapter_context.py` — 章节上下文管理
- **职责**：在 LLM 生成各章节时传递跨章节上下文，避免前后矛盾
- **改动触发点**：调整章节间信息传递逻辑

#### `src/agents/comparables.py` — 可比公司分析
- **职责**：自动选取同行可比公司，生成 PE/PB/ROE 对比表
- **关键函数**：`run_comparable_analysis(ticker, sector, user_comparables)`
- **选取逻辑**：watchlist.yaml → industry_profiles.yaml → AKShare 市值相近选取
- **改动触发点**：调整可比公司选取算法；修改对比指标

#### `src/agents/industry_valuation.py` — 行业估值配置（轻量封装）
- **职责**：按行业提供估值参数快捷访问
- **改动触发点**：需要时一般直接改 `industry_profiles.yaml`

---

### 🔵 数据层 (`src/data/`)
---
#### `src/data/fetcher.py` — 多源数据编排器（对外主接口）
- **职责**：多数据源按优先级回退抓取，写入 SQLite
- **优先级链**：
  - A 股：`akshare → tushare → baostock → sina_realtime → qveris`
  - HK：`akshare → yfinance → sina_realtime → fmp`
  - 美股：`yfinance → fmp`
- **关键类**：`Fetcher` → `fetch_prices()`, `fetch_financials()`, `fetch_all()`, `fetch_company_basics()`
- **改动触发点**：添加新数据源；调整回退优先级；修改公司信息 fallback 字典

#### `src/data/database.py` — SQLite CRUD 层
- **职责**：建表 + upsert + 查询，所有 Agent 读写数据的唯一通道
- **表结构**：`daily_prices`, `income_statements`, `balance_sheets`, `cash_flows`, `financial_metrics`, `manual_docs`, `agent_signals`, `portfolio_positions`, `trade_log`
- **关键函数**：`init_db()`, `upsert_*()`, `get_income_statements()`, `get_balance_sheets()`, `get_financial_metrics()`, `insert_agent_signal()`, `get_latest_agent_signals()`
- **改动触发点**：添加新字段（需加 migration）；修改查询条件

#### `src/data/quality.py` — 数据质量验证层
- **职责**：检查数据新鲜度、完整性、逻辑一致性，生成 `QualityReport` 注入报告
- **检查项**：数据过期、负权益、收入/利润异常、NI vs OCF 背离、核心字段缺失
- **关键函数**：`check_financial_freshness()`, `check_data_staleness()`, `check_negative_equity()`
- **改动触发点**：添加新的质量检查规则；调整质量评分权重

#### `src/data/macro_data.py` — 宏观数据模块
- **职责**：从 AKShare 获取 PMI（制造业、服务业、财新）和 PPI，缓存 4 小时
- **关键函数**：`get_macro_snapshot(use_cache, cache_ttl_hours)` → `MacroSnapshot`
- **缓存路径**：`data/cache/macro_snapshot.json`
- **改动触发点**：添加新宏观指标；修复 AKShare API 接口变化

#### `src/data/models.py` — Pydantic 数据模型
- **职责**：定义所有数据实体：`AgentSignal`, `DailyPrice`, `IncomeStatement`, `BalanceSheet`, `CashFlow`, `FinancialMetrics`, `QualityReport`, `MarketType`
- **改动触发点**：添加新字段到数据模型（同步更新 database.py）

#### `src/data/balance_sheet_scanner.py` — 资产负债表扫描器 ⭐ NEW
- **职责**：扫描资产负债表科目名，检测银行/保险特征（供 V3 行业引擎使用）
- **关键函数**：`extract_industry_flags(raw_balance_sheet_items)` → `{has_loan_loss_provision, has_insurance_reserve}`
- **改动触发点**：调整银行/保险关键词列表

#### `src/data/akshare_source.py` — AKShare 数据源（最主要的 A 股数据源）
- **改动触发点**：AKShare API 接口变更；添加新 AKShare 数据类型
- **V3 集成**：`get_balance_sheets()` 现在提取 V3 字段（inventory, advance_receipts, fixed_assets, industry_flags）

#### `src/data/qveris_source.py` — QVeris iFinD 数据源（付费精准数据）
- **改动触发点**：QVeris API 变更；添加新 iFinD 数据字段

#### 其他数据源（较少改动）
- `baostock_source.py` — 宝安财经
- `tushare_source.py` — Tushare
- `yfinance_source.py` — 美股/港股
- `fmp_source.py` — Financial Modeling Prep（备用）
- `sina_source.py` — 新浪财经实时行情
- `tavily_source.py` — Tavily 新闻搜索
- `manual_source.py` — 手动上传文档解析
- `industry_mapping.py` / `industry_macro_mapping.py` — 中文行业名映射表

---

### 🟣 LLM 路由层 (`src/llm/`)
---
#### `src/llm/router.py` — LLM 调用统一入口
- **职责**：按任务路由到正确 LLM 提供商，支持 retry + fallback
- **关键函数**：`call_llm(task, system_prompt, user_prompt, ...)` → `str`
- **支持提供商**：OpenAI, DeepSeek, Anthropic
- **任务→提供商映射**：由 `config/llm_config.yaml` 配置
- **改动触发点**：添加新 LLM 提供商；调整 fallback 顺序

#### `src/llm/prompts.py` — 所有 LLM System Prompt
- **职责**：集中管理所有 Agent 的 System Prompt 和 User Prompt 模板
- **包含**：`BUFFETT_SYSTEM_PROMPT`, `BUFFETT_USER_TEMPLATE`, 以及各 Agent Prompt
- **改动触发点**：调整 LLM 分析角度；修改 Prompt 质量

---

### ⚙️ 策略层 (`src/strategy/`)
---
#### `src/strategy/screener.py` — 因子筛选器
- **职责**：读取 `screening_rules.yaml`，对 watchlist 全量评估，输出买入信号
- **关键函数**：`run_scan(watchlist, notify)` → `List[ScreeningSignal]`
- **改动触发点**：修改筛选算子逻辑；修改规则→改 YAML

#### `src/strategy/backtester.py` — 因子回测
- **职责**：历史回测因子策略有效性
- **改动触发点**：修改回测逻辑；添加新回测指标

---

### 📬 通知层 (`src/notification/`)
---
#### `src/notification/telegram_notifier.py` — Telegram 推送（主推送渠道）
- **改动触发点**：修改消息格式；添加推送触发条件

#### `src/notification/email_sender.py` — 邮件推送（备用）
- **改动触发点**：修改邮件模板

---

### 🔧 工具层 (`src/utils/`)
---
#### `src/utils/config.py` — 配置加载工具
- **关键函数**：`get_project_root()`, `load_watchlist()`, `load_llm_config()`, `get_settings()`
- **改动触发点**：添加新配置文件的加载函数

#### `src/utils/logger.py` — 日志工具
- **关键函数**：`get_logger(name)` → 统一日志格式

#### `src/utils/calculation_tracer.py` — 计算过程追踪
- **职责**：记录估值计算步骤，用于报告中的透明度说明

---

### 📋 配置文件 (`config/`)
---
| 文件 | 内容 | 保护状态 |
|------|------|----------|
| `config/industry_profiles.yaml` | 行业分类、Agent权重、估值倍数、可比公司 | 可编辑 |
| `config/watchlist.yaml` | 监控股票列表，含行业标签 | 可编辑 |
| `config/llm_config.yaml` | LLM任务路由、模型配置 | ⚠️ 受保护（需用户审批） |
| `config/screening_rules.yaml` | 因子筛选规则 | ⚠️ 受保护（需用户审批） |

---

### 📄 模板 (`templates/`)
---
- `templates/report_template.md` — 研报 Markdown 模板
- `templates/contrarian_templates/` — Contrarian Agent 的 Prompt 模板文件

---

## 🔄 数据流（以 `invest report --ticker 601808.SH` 为例）

```
main.py:report()
    ↓
Fetcher.fetch_all("601808.SH", "a_share")
    ↓ 写入 SQLite
registry.run_all_agents("601808.SH", "a_share")
    ↓
    ├── quality.check_all() → QualityReport
    ├── fundamentals.run() → AgentSignal (纯代码)
    ├── valuation.run()    → AgentSignal (代码 + 可选 LLM)
    │     ↑ 依赖 wacc.calculate_wacc()
    │     ↑ 依赖 industry_classifier.classify_industry()
    ├── warren_buffett.run() → AgentSignal (LLM)
    ├── ben_graham.run()     → AgentSignal (代码 + LLM)
    ├── sentiment.run()      → AgentSignal (Tavily + LLM)
    ├── contrarian.run(signals=前5个AgentSignals) → AgentSignal (LLM)
    └── report_generator.run(signals=全部)
          ↓
          8章 LLM 并行生成
          ↓
    output/reports/601808.SH_2026-03-12.md
          ↓
    telegram_notifier.send()
```

---

## 📊 数据库表结构速查

```sql
daily_prices        (ticker, date, open, high, low, close, volume, source)
income_statements   (ticker, period_end_date, period_type, revenue, net_income, ...)
balance_sheets      (ticker, period_end_date, total_assets, total_equity, inventory, advance_receipts, fixed_assets, has_loan_loss_provision, has_insurance_reserve, ...)
cash_flows          (ticker, period_end_date, operating_cash_flow, capex, free_cash_flow)
financial_metrics   (ticker, date, roe, roa, roic, de_ratio, current_ratio, ...)
manual_docs         (ticker, file_name, doc_type, extracted_text, ...)
agent_signals       (ticker, agent_name, signal, confidence, reasoning, metrics_json)
portfolio_positions (ticker, shares, avg_cost, target_weight, ...)
trade_log           (ticker, action, amount, shares, price, date)
```

---

## ⚠️ 已知约束和重要规则

1. **受保护文件**：`config/llm_config.yaml`, `config/screening_rules.yaml` 修改需用户确认
2. **数据必须先 fetch**：所有 Agent 都读 SQLite，必须先 `invest fetch --ticker XXX`
3. **最小数据完整性**：`registry.py` 中 `MIN_DATA_COMPLETENESS = 0.20`，低于 20% 直接报错
4. **行业权重来自 YAML**：修改 Agent 权重去 `config/industry_profiles.yaml`，不在代码里硬编码
5. **LLM fallback 链**：OpenAI → Anthropic → DeepSeek，可在 `llm_config.yaml` 调整
6. **QVeris 数据限额**：超限后自动降级到 `fetcher.py` 中的 `_COMPANY_INFO_FALLBACK` 本地字典
7. **宏观数据缓存**：`data/cache/macro_snapshot.json`，TTL 4 小时，删除可强制刷新
8. **V3 行业引擎 Feature Flag**：设置 `USE_INDUSTRY_ENGINE_V3=true` 启用三层漏斗架构，`INDUSTRY_ENGINE_PARALLEL=true` 启用 V2/V3 并行对比模式
