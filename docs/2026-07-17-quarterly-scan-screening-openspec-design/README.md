# 2026-07-17 Quarterly Scan Screening OpenSpec Design

本目录是基于 `docs/plans/2026-07-17-quarterly-scan-screening-prd.md` 产出的 OpenSpec 风格变更包。

## 建设范围

- 季度级 `scan_campaign` 分批续扫工作台。
- 筛选策略可视化与规则漏斗。
- L0 卫生过滤、L1 质量过滤、红旗直接出局。
- 暂停、停止、继续、重试失败。
- 最近估值扫描行级研报状态。
- true/false 配置 toggle 化。
- campaign master_result 累积导出。
- 数据源、LLM、网络失败可视化处理。
- 季度扫描历史对比。
- 研报阅读页关联扫描来源。

## 文件结构

```text
docs/2026-07-17-quarterly-scan-screening-openspec-design/
├── README.md
├── proposal.md
├── design.md
├── tasks.md
└── specs/
    ├── quarterly-scan-campaign/spec.md
    ├── screening-strategy-inspector/spec.md
    ├── screening-filter-engine/spec.md
    └── report-settings-failures/spec.md
```

## OpenSpec 对齐方式

- `proposal.md`: 说明为什么建设、建设什么、不建设什么。
- `design.md`: 详细前后端技术设计、数据模型、接口、状态机、迁移策略。
- `tasks.md`: 可执行开发任务清单。
- `specs/*/spec.md`: 行为增量，使用 `ADDED Requirements` 和 `Scenario` 描述可验收能力。

## 当前代码基线

- 本地控制台: `src/web/local_console.py`
- 估值扫描 job: `src/screening/jobs.py`
- 批量估值: `src/screening/batch_valuation.py`
- 股票池: `src/screening/universe.py`
- SQLite CRUD: `src/data/database.py`
- 健康检查: `src/web/health.py`
- 当前输出文件:
  - `output/valuation_jobs/*.json`
  - `output/valuation_scans/*.json`
  - `output/valuation_scans/*.csv`
  - `output/report_jobs/latest.json`
  - `output/*.md`

## 实施原则

1. MVP 保持本地单用户、SQLite、JSON、CSV、无大型前端框架。
2. 先复用 `stock_universe`、`valuation_jobs`、`valuation_scans`、现有 report job，再扩展 campaign 持久化。
3. 扫描阶段默认不启用 LLM；完整研报才运行多 Agent + LLM。
4. 任一标的失败不得阻断 campaign；连续失败超过阈值自动暂停。
5. campaign 创建时固化 `universe_snapshot` 和 `strategy_snapshot`。
6. 每只标的必须能追溯到通过或剔除的具体规则。
7. 红旗规则命中必须直接出局；关键数据缺失不得默认为通过。
8. 单批最多 500 只。
