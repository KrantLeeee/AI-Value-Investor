# AI Value Investor

AI-powered investment research assistant for value investing.

Automates: fundamental analysis, deep research reports, factor screening, portfolio management.

## Quick Start

```bash
# Install dependencies
poetry install

# Set up API keys
cp .env.example .env
# Edit .env with your API keys

# Fetch data for a stock
invest fetch --ticker 601808.SH

# Generate a research report
invest report --ticker 601808.SH

# Run opportunity scan
invest scan

# View portfolio
invest portfolio
```

## Architecture

See `References/Docs/Tech Design/tech-design-v1.md` for full technical design.

## Commands

### fetch — 抓取市场数据
抓取价格和财务报表，存入本地 SQLite。**生成报告前必须先执行。**

```bash
# 单只股票（自动识别市场）
invest fetch -t 601808.SH        # A股
invest fetch -t 0700.HK          # 港股
invest fetch -t AAPL             # 美股

# 指定市场
invest fetch -t 601808.SH -m a_share

# 抓取 watchlist 全部股票
invest fetch --all

# 指定历史天数（默认3年）
invest fetch -t 601808.SH -d 365   # 只抓1年
```

| 选项 | 说明 |
|------|------|
| `-t, --ticker` | 股票代码，如 `601808.SH` |
| `-m, --market` | 市场类型：`a_share` / `hk` / `us` |
| `--all` | 抓取 watchlist.yaml 中所有股票 |
| `-d, --days` | 价格历史天数（默认 1095 = 3年）|

---

### report — 生成研报
调用多 Agent 分析，生成中文深度研报。

```bash
# 完整版（含 LLM 分析，需 API Key）
invest report -t 601808.SH

# 快速版（纯数据，无 LLM，秒出）
invest report -t 601808.SH --quick

# 跳过公司信息确认（自动化场景）
invest report -t 601808.SH --skip-confirm

# 手动指定公司信息（当自动检测失败时）
invest report -t 601808.SH --company-name "中海油服" --industry "油气服务"

# 指定 LLM 模型
invest report -t 601808.SH --model gpt-4o
invest report -t 601808.SH --model deepseek-chat

# 批量生成 watchlist 前 N 个
invest report --watchlist-top 5

# 生成后发送 Telegram 通知
invest report -t 601808.SH --notify
```

| 选项 | 说明 |
|------|------|
| `-t, --ticker` | 股票代码 |
| `--quick` | 快速模式，不调用 LLM，纯数据报告 |
| `--model` | 覆盖默认 LLM 模型 |
| `--watchlist-top N` | 批量生成前 N 只股票的报告 |
| `--notify` | 通过 Telegram 发送报告 |
| `--skip-confirm` | 跳过公司信息确认步骤 |
| `--company-name` | 手动指定公司名称 |
| `--industry` | 手动指定行业 |

---

### scan — 因子筛选
基于 `config/screening_rules.yaml` 规则扫描 watchlist，输出买入信号。

```bash
# 运行筛选
invest scan

# 筛选后发送邮件通知
invest scan --notify
```

| 选项 | 说明 |
|------|------|
| `--notify` | 有信号时发送邮件通知 |

---

### ingest — 解析手动文档
解析 `data/manual/{ticker}/` 目录下的 PDF/文本文件，提取财务数据。

```bash
# 解析全部
invest ingest

# 只解析某只股票
invest ingest -t 601808.SH
```

| 选项 | 说明 |
|------|------|
| `-t, --ticker` | 只解析指定股票的文档 |

---

### status — 系统状态
显示数据源健康状态、watchlist 大小。

```bash
invest status
```

---

### network — 网络诊断
检查代理配置和 API 连通性。

```bash
# 显示配置
invest network

# 运行连通性测试
invest network --test
```

| 选项 | 说明 |
|------|------|
| `-t, --test` | 测试 OpenAI/Anthropic/DeepSeek 等 API 连通性 |

---

### profile — 投资者画像
管理投资者资金、风险偏好配置。

```bash
# 交互式设置
invest profile --setup

# 查看当前配置
invest profile --show
```

| 选项 | 说明 |
|------|------|
| `--setup` | 交互式设置资金、风险参数 |
| `--show` | 显示当前投资者画像 |

---

### portfolio — 查看持仓
显示当前持仓和可用资金。

```bash
invest portfolio
```

---

### backtest — 因子回测
对筛选规则进行历史回测。

```bash
# 回测 "安全边际" 规则，2020-2024，持有3年
invest backtest --rule "安全边际" --start 2020 --end 2024 --hold 3
```

| 选项 | 说明 |
|------|------|
| `--rule` | 规则名称（来自 screening_rules.yaml）|
| `--start` | 起始年份 |
| `--end` | 结束年份 |
| `--hold` | 持有期（年），默认 3 |

---

### invest — 仓位建议
⚠️ **尚未实现**（计划中）

```bash
invest invest -t 601808.SH
```

---

## Environment Variables

### Feature Flags

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `USE_INDUSTRY_ENGINE_V3` | `false` | 启用 V3 行业引擎（三层漏斗：硬规则 → LLM → fallback）|
| `INDUSTRY_ENGINE_PARALLEL` | `false` | V3/V2 并行对比模式，输出两套结果供验证 |

### Rate Limiting (Anti-Bot)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FETCH_DELAY` | `3.0` | 每次成功抓取后的延迟（秒）|
| `FETCH_DELAY_BETWEEN_SOURCES` | `2.0` | 数据源 fallback 切换时的延迟（秒）|
| `FETCH_DELAY_BETWEEN_TYPES` | `2.0` | 不同数据类型之间的延迟（秒）|
| `FETCH_DELAY_BETWEEN_TICKERS` | `5.0` | 不同股票之间的延迟（秒）|
| `SKIP_AKSHARE` | `false` | 跳过 AKShare（避免 eastmoney.com 封 IP），优先级变为 Tushare → BaoStock → QVeris |

### API Keys

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | OpenAI GPT-4o（主力 LLM）|
| `ANTHROPIC_API_KEY` | Anthropic Claude（备用）|
| `DEEPSEEK_API_KEY` | DeepSeek（低成本任务，V3 行业路由）|
| `TAVILY_API_KEY` | Tavily 新闻搜索 |
| `TUSHARE_TOKEN` | Tushare A股数据 |
| `TUSHARE_API_URL` | Tushare 自定义端点（可选，用于镜像/代理）|
| `QVERIS_API_KEY` | QVeris iFinD（付费精准数据）|
| `TELEGRAM_BOT_TOKEN` | Telegram 推送 |
| `TELEGRAM_CHAT_ID` | Telegram 目标会话 |
## License

This project is licensed under the [GNU General Public License V3.0](LICENSE).
