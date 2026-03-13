# AI Value Investor — Claude 工作说明
<!-- Claude Code 每次会话自动注入此文件，无需手动附加 -->

## 📌 项目简介
**AI 驱动的 A 股价值投资研究助手。** 多 Agent 协作（7个分析Agent + 1个辩证Agent）→ 生成中文深度研报 → Telegram 推送。

## 🎯 改动场景快速索引
| 我想做的事 | 直接改这些文件 | 先读 |
|-----------|---------------|------|
| 添加/修改估值方法 | `src/agents/valuation.py` | `src/agents/README.md` |
| 修改报告章节结构 | `src/agents/report_generator.py` | `src/agents/README.md` |
| 添加新 Agent | `src/agents/` 新建 + `registry.py` | `src/agents/README.md` |
| 修改 LLM Prompt | `src/llm/prompts.py` | `src/llm/README.md` |
| 添加 LLM 任务 | `config/llm_config.yaml` | `src/llm/README.md` |
| 添加新数据源 | `src/data/xxx_source.py` + `fetcher.py` | `src/data/README.md` |
| 调整行业参数 | `config/industry_profiles.yaml` | `config/README.md` |
| 添加新 CLI 命令 | `src/main.py` | — |

> 📚 **架构级任务**：需要理解完整模块关系时，读 `PROJECT_MAP.md`

## 🔥 当前开发重心（2026-Q1）
- **Contrarian Agent 完善** → `src/agents/contrarian.py`
- **多行业估值能力升级** → `src/agents/valuation.py` + `config/industry_profiles.yaml`
- 参考设计文档：`References/Docs/Tech Design/多行业估值能力进化方案改造 2.0.md`
- 参考路线图：`Tech Roadmap/PROJECT_ROADMAP.md`, `Tech Roadmap/V2.0.0 Plan.md`

## ⚙️ 技术栈
- **语言**：Python 3.12，使用 Poetry 管理依赖
- **CLI**：Click 框架，入口 `src/main.py`
- **数据库**：本地 SQLite，ORM 层 `src/data/database.py`
- **LLM**：OpenAI GPT-4o（主）+ DeepSeek（低成本任务）+ Anthropic（备用）
- **代码规范**：ruff（已配置 PostToolUse Hook 自动格式化）

## 🛡️ 保护规则（不要直接修改，需用户审批）
- `config/llm_config.yaml` — LLM 路由配置
- `config/screening_rules.yaml` — 因子筛选规则
- `.env` 文件 — API 密钥
- `output/` — 已生成报告，不删除

## 🚀 常用命令速查
```bash
# 抓取数据（报告生成前必须先做）
poetry run invest fetch --ticker 601808.SH

# 生成研报（完整 LLM 版本）
poetry run invest report --ticker 601808.SH

# 批量生成（watchlist 前 N 个）
poetry run invest report --watchlist-top 5

# 快速报告（无 LLM，秒出）
poetry run invest report --ticker 601808.SH --quick

# 因子筛选
poetry run invest scan

# 运行测试
poetry run pytest tests/ -v

# 查看数据状态
poetry run invest status
```

## 📂 关键文件路径速查
```
src/main.py                          CLI 入口，所有命令在此
src/agents/registry.py               Agent 编排入口
src/agents/report_generator.py       研报生成（最长，1358行）
src/agents/valuation.py             估值核心（最核心，1678行）
src/agents/contrarian.py            辩证分析
src/data/fetcher.py                  数据抓取编排器
src/data/database.py                 SQLite CRUD
src/llm/router.py                    LLM 调用统一入口
src/llm/prompts.py                   所有 LLM Prompt
config/industry_profiles.yaml        行业配置（无需代码即可调参数）
config/watchlist.yaml                监控股票列表
config/llm_config.yaml              ⚠️ LLM 路由（受保护）
```

## 💡 重要约束
1. **先 fetch 后 report**：Agent 读 SQLite，没有数据直接报错
2. **行业参数在 YAML**：修改估值倍数/Agent权重→ `config/industry_profiles.yaml`，不要 hardcode
3. **LLM Prompt 集中管理**：所有 System Prompt 在 `src/llm/prompts.py`，不要分散放
4. **最低数据完整度**：低于 20% 自动跳过（`registry.py:MIN_DATA_COMPLETENESS`）
