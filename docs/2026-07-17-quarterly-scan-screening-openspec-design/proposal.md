# Proposal: Quarterly Scan Screening Workbench

## Summary

把现有“单次估值扫描 + 最近结果表”升级为“季度扫描计划 + 可恢复批次 + 策略漏斗 + 可解释筛选 + 行级研报状态”的本地研究工作台。

本变更以 `docs/plans/2026-07-17-quarterly-scan-screening-prd.md` 为产品输入，优先交付 P0/P1 能力，并保留 P2 的数据模型和接口扩展点。

## Motivation

当前系统已经具备：

- `stock_universe` 本地股票池。
- `valuation_jobs` JSON 持久化估值扫描任务。
- `valuation_scans` JSON/CSV 输出。
- `run_batch_valuation()` 轻量批量估值。
- 本地 HTML 控制台和研报生成入口。

但季度全市场研究还缺少：

- campaign 级计划、批次、股票池快照和可恢复 cursor。
- 可视化的筛选策略、规则阈值、通过数、剔除数、缺失数。
- 每只标的的结构化 `rule_evaluation` 和 `filter_result`。
- 暂停、停止、继续、只重试失败项。
- 行级研报 job 状态，而不是单个全局 latest report job。
- 失败分类、可执行恢复动作和导出。

## Goals

- 支持按季度创建 `scan_campaign`，单批最多 500 只。
- campaign 创建时固化 `universe_snapshot` 和 `strategy_snapshot`。
- 支持 `continue_last`、`restart_new`、`scan_remaining`、`retry_failed`。
- 服务重启后从持久化状态恢复 campaign 和 report job。
- 在估值前执行 L0、L1、red_flag 分层筛选。
- 筛选策略在前端可见，并能展示规则级统计和 ticker 级判定明细。
- `master_result` 持续累积，支持导出当前结果、候选池、筛选剔除明细、失败项。
- 最近估值扫描表格展示每行研报按钮状态。
- 设置页将白名单 true/false 配置渲染为 toggle。
- 失败信息标准化为 `failure_type`、`failure_scope`、`retryable`、`user_action`。

## Non-Goals

- 不引入 React、Vue、Next.js 等大型前端框架。
- 不做多用户登录和权限系统。
- 不改变完整研报的 Agent 架构和章节结构。
- 不把 LLM 放入默认扫描路径。
- 不修改受保护配置文件的默认内容，尤其是 `config/llm_config.yaml` 和 `config/screening_rules.yaml`。
- 不自动删除历史报告或 `output/` 下用户产物。

## Proposed Change

新增一个 campaign 服务层，包裹现有 `valuation_jobs`、`valuation_scans` 和 `run_batch_valuation()`：

```text
src/screening/campaigns.py
src/screening/campaign_runner.py
src/screening/strategy.py
src/screening/filter_engine.py
src/screening/failures.py
src/screening/report_jobs.py
```

扩展 SQLite schema：

```text
scan_campaigns
scan_batches
scan_campaign_items
screening_strategy_snapshots
scan_rule_evaluations
scan_master_results
scan_failures
report_jobs
app_settings
```

扩展本地控制台：

```text
/campaigns
/campaigns/new
/campaigns/{campaign_id}
/campaigns/{campaign_id}/strategy
/campaigns/{campaign_id}/rules/{rule_id}
/campaigns/{campaign_id}/tickers/{ticker}
/campaigns/{campaign_id}/history
/report-jobs/{report_job_id}
/settings
```

保留当前根页面作为工作台首页，并在首页突出最近 campaign、最近估值扫描、系统健康和报告状态。

## Impact

### Backend

- `src/data/database.py` 增加建表和 CRUD。
- `src/screening/jobs.py` 保留兼容，但由 campaign runner 调用或桥接。
- `src/screening/batch_valuation.py` 增加筛选结果字段和“跳过估值”路径。
- `src/web/local_console.py` 增加路由和页面，必要时拆出 `src/web/campaign_pages.py` 降低文件长度。
- `src/web/health.py` 的健康状态被 campaign failure classifier 复用。

### Frontend

- 继续使用服务端 HTML + 少量 vanilla JS 轮询。
- 新增 campaign 进度卡、批次卡、策略漏斗、规则表、单标的判定明细、错误面板、导出按钮、toggle 控件。
- 保持浅色工作台风格，预留 CSS token。

### Data

- campaign 和 report job 以 SQLite 为主，JSON/CSV 输出继续作为审计和导出产物。
- `valuation_scans` 文件继续存在，`master_result` 在每只 ticker 完成后增量更新。

## Rollout

1. P0 后端 schema 和 campaign runner。
2. P0 筛选策略解析、规则评估、master_result。
3. P0 工作台、策略详情、单标的明细。
4. P1 控制动作、行级 report job、配置 toggle、导出。
5. P2 历史对比、报告来源、失败可视化完善。

## Risks

- `src/web/local_console.py` 已较长，继续堆叠会难维护。设计要求新增页面渲染函数超过阈值时拆入 `src/web/campaign_pages.py`。
- 全市场扫描可能触发数据源限流。runner 必须按 ticker 持久化，失败不阻断，并支持重试失败项。
- `config/screening_rules.yaml` 是受保护文件。实现阶段默认只读和 snapshot，不直接改默认规则。
- SQLite 并发写入可能被锁。使用单后台 runner、短事务、WAL，失败归类为 `local_db_locked`。

## Open Questions

- 港股全市场股票池是否必须在 MVP 中支持完整刷新，还是允许首版通过 watchlist 和 AKShare 可用性降级。
- 审计意见、股东质押、立案处罚等数据源缺口是否首版全部接入 Tushare，还是先以 `missing/manual_review` 呈现。
- 是否需要为金融地产单独建立策略，而不是只在普通价值策略中排除。
