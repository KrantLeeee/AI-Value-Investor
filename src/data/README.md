<!-- Last Updated: 2026-03-13 -->
# 数据层

## 核心文件
| 文件 | 职责 |
|------|------|
| `fetcher.py` | 多源数据编排器（对外主接口）|
| `database.py` | SQLite CRUD（所有 Agent 读写通道）|
| `quality.py` | 数据质量验证，生成 QualityReport |
| `models.py` | Pydantic 数据模型定义 |

## 数据源优先级（fetcher.py）
```
A股：akshare → tushare → baostock → sina_realtime → qveris
港股：akshare → yfinance → sina_realtime → fmp
美股：yfinance → fmp
```
**添加新数据源**：创建 `xxx_source.py` → 在 `fetcher.py` 注册到 `_SOURCE_PRIORITY`

## 数据库表结构
```sql
daily_prices        -- 日线行情
income_statements   -- 利润表
balance_sheets      -- 资产负债表
cash_flows          -- 现金流量表
financial_metrics   -- 财务指标（ROE/ROA/ROIC等）
manual_docs         -- 手动上传文档
agent_signals       -- Agent 输出信号
portfolio_positions -- 持仓
trade_log           -- 交易记录
```

## 关键函数
```python
# fetcher.py
Fetcher.fetch_all(ticker, market, start_date)  # 抓取全部数据
Fetcher.fetch_company_basics(ticker, market)   # 公司基本信息

# database.py
get_income_statements(ticker, limit)
get_balance_sheets(ticker, limit)
get_financial_metrics(ticker, limit)
get_price_data(ticker, start_date, end_date)

# quality.py
check_all(ticker, market) → QualityReport
```

## 数据源文件说明
| 文件 | 数据源 | 市场 |
|------|--------|------|
| `akshare_source.py` | AKShare | A股主力 |
| `tushare_source.py` | Tushare | A股备用 |
| `baostock_source.py` | BaoStock | A股备用 |
| `qveris_source.py` | QVeris iFinD | A股付费精准 |
| `yfinance_source.py` | Yahoo Finance | 美股/港股 |
| `fmp_source.py` | Financial Modeling Prep | 美股备用 |
| `sina_source.py` | 新浪财经 | 实时行情 |
| `tavily_source.py` | Tavily | 新闻搜索 |

## 宏观数据
`macro_data.py` 获取 PMI/PPI，缓存 4 小时于 `data/cache/macro_snapshot.json`

## 数据质量检查项（quality.py）
- 数据过期检测
- 负权益检测
- 收入/利润异常
- NI vs OCF 背离
- 核心字段缺失
