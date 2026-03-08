# 数据质量与信号逻辑优化设计文档

**日期**: 2026-03-08
**状态**: 已批准，待实现
**优先级**: P0（阻断性问题修复）

---

## 一、问题摘要

基于最新报告的反馈，识别出5个核心问题：

| # | 问题 | 严重性 | 影响 |
|---|------|--------|------|
| 1 | Graham Agent 0/7标准通过却输出bullish | **致命** | 报告结论完全错误 |
| 2 | 估值异常值（-243%）参与加权计算 | **高** | 目标价不可靠 |
| 3 | 432天过期数据无阻断机制 | **高** | 分析基础失效 |
| 4 | 数据源单点依赖AKShare | **中** | 系统可用性风险 |
| 5 | 报告暴露Agent名称 | **低** | 专业性不足 |

---

## 二、设计方案

### 2.1 Graham Agent 信号逻辑修复

**问题根因**：`ben_graham.py:186-190` 的LLM fallback逻辑完全忽略了criteria_passed计数，LLM返回文本包含"bullish"就直接覆盖代码逻辑。

**修复方案**：代码是最后一道闸门，LLM不能覆盖硬规则。

| 通过数 | 信号硬规则 | LLM约束 | 置信度计算 |
|--------|-----------|---------|------------|
| 0/7 | **硬bearish** | 跳过LLM | 0.70 |
| 1-2/7 | **硬bearish** | LLM只补reasoning | `0.40 + 0.15 × data_completeness` |
| 3-4/7 | **上限neutral** | 代码强制覆盖 | 0.45 |
| 5-6/7 | LLM判断 | neutral/bullish | LLM输出 |
| 7/7 | 无限制 | — | LLM输出 |

**关键代码**：

```python
# src/agents/ben_graham.py

SIGNAL_ORDER = {"bearish": 0, "neutral": 1, "bullish": 2}

def run(ticker: str, market: str, ...) -> AgentSignal:
    # ... 计算 criteria_passed ...

    n_pass = metrics_snapshot.get("criteria_passed")
    n_total = metrics_snapshot.get("criteria_total", 7)
    data_completeness = metrics_snapshot.get("data_completeness", 0.5)

    # 防御性检查：数据不足
    if n_pass is None:
        return AgentSignal(
            signal="neutral", confidence=0.30,
            reasoning="格雷厄姆标准数据不足，无法评判"
        )

    # 硬规则：0标准通过 → 必须bearish，跳过LLM
    if n_pass == 0:
        return AgentSignal(
            signal="bearish", confidence=0.70,
            reasoning=f"格雷厄姆标准: 0/{n_total} 通过 → 不符合防御型投资标准"
        )

    # 硬规则：1-2标准通过 → 必须bearish
    if n_pass <= 2:
        max_signal = "bearish"
        confidence = 0.40 + (0.15 * data_completeness)
    # 上限：3-4标准通过 → 最多neutral
    elif n_pass <= 4:
        max_signal = "neutral"
        confidence = 0.45
    # 5-6标准通过 → neutral或bullish
    elif n_pass <= 6:
        max_signal = "bullish"  # 允许bullish但由LLM判断
        confidence = None  # 由LLM给出
    # 7/7全通过 → 无限制
    else:
        max_signal = None
        confidence = None

    # 调用LLM...
    llm_signal, llm_confidence, llm_reasoning = _call_llm(...)

    # 代码强制覆盖：LLM不能突破上限
    if max_signal is not None:
        if SIGNAL_ORDER.get(llm_signal, 1) > SIGNAL_ORDER[max_signal]:
            final_signal = max_signal
            final_confidence = min(llm_confidence, 0.55)
        else:
            final_signal = llm_signal
            final_confidence = llm_confidence if confidence is None else confidence
    else:
        final_signal = llm_signal
        final_confidence = llm_confidence

    return AgentSignal(signal=final_signal, confidence=final_confidence, ...)
```

---

### 2.2 估值异常检测

**问题根因**：`valuation.py` 中EV/EBITDA计算出负值或极端偏离，直接参与加权计算，无任何校验。

**修复方案**：三层检测 + 并集剔除 + 降级模式

**检测规则**：

| 规则 | 触发条件 | 处理 |
|------|----------|------|
| 单方法偏离 | 目标价偏离当前价 > ±80% | 剔除加权 |
| 负值/零值 | 目标价 ≤ 0 | 剔除加权 |
| 中位数偏离 | 与其他方法中位数偏离 > 50% | 剔除加权 |

**关键代码**：

```python
# src/agents/valuation.py

import statistics

def _validate_valuation_result(
    method_name: str,
    target_price: float,
    current_price: float,
    all_results: list[float]
) -> dict:
    """验证估值结果合理性，返回是否有效及警告信息"""
    result = {
        "method": method_name,
        "target_price": target_price,
        "valid": True,
        "warnings": [],
        "exclude_from_weighted": False
    }

    # 规则1: 负值/零值检测
    if target_price is None or target_price <= 0:
        result["valid"] = False
        result["exclude_from_weighted"] = True
        result["warnings"].append("目标价为负或零，计算错误")
        return result

    # 规则2: 单方法偏离检测（与当前价）
    if current_price and current_price > 0:
        deviation = (target_price - current_price) / current_price
        if abs(deviation) > 0.80:
            result["exclude_from_weighted"] = True
            result["warnings"].append(f"偏离当前价{deviation*100:.0f}%")

    # 规则3: 与其他方法中位数偏离检测
    other_results = [r for r in all_results if r != target_price and r and r > 0]
    if len(other_results) >= 2:
        median = statistics.median(other_results)
        if median > 0:
            cross_deviation = abs(target_price - median) / median
            if cross_deviation > 0.50:
                result["exclude_from_weighted"] = True
                result["warnings"].append(f"与中位数偏离{cross_deviation*100:.0f}%")

    return result


def _calculate_weighted_target(results: list[dict], current_price: float) -> dict:
    """计算加权目标价，含降级模式"""

    # 过滤有效方法
    valid_methods = [r for r in results if not r.get("exclude_from_weighted", False)]

    # 降级模式：有效方法不足
    if len(valid_methods) <= 1:
        return {
            "weighted_target": None,
            "signal": "neutral",
            "confidence": 0.25,
            "degraded": True,
            "warning": "⚠ 有效估值方法不足，无法形成可靠加权结论"
        }

    # 正常加权计算
    weights = _get_method_weights(valid_methods)
    weighted_target = sum(
        r["target_price"] * weights[r["method"]]
        for r in valid_methods
    )

    return {
        "weighted_target": weighted_target,
        "valid_methods": len(valid_methods),
        "excluded_methods": [r["method"] for r in results if r.get("exclude_from_weighted")],
        "degraded": False
    }
```

---

### 2.3 数据新鲜度硬阻断（基于披露周期）

**问题根因**：单纯用"距今天数"判断过期不合理，A股有固定披露节奏，需要检测"是否缺少应披露报告期"。

**A股披露节奏**：
- 年报：次年4月30日前
- 半年报：8月31日前
- 一季报：4月30日前
- 三季报：10月31日前

**检测逻辑**：

| 情况 | level | 处理方式 |
|------|-------|----------|
| 有应披露报告期但缺失 | CRITICAL | 生成**数据缺口警告报告**（降级模式） |
| >180天但在正常披露周期 | WARNING | 生成完整报告，**每章节黄色警告** |
| <180天 | OK | 正常生成 |

**关键代码**：

```python
# src/data/quality.py

from datetime import date
from dataclasses import dataclass
from typing import Literal

@dataclass
class ExpectedReport:
    period: str  # "年报", "半年报", "一季报", "三季报"
    deadline: date

@dataclass
class StalenessResult:
    level: Literal["OK", "WARNING", "CRITICAL"]
    reason: str = ""
    expected_report: ExpectedReport | None = None

def get_next_expected_report(last_report_date: date) -> ExpectedReport:
    """根据最后一份报告日期，推算下一份应有报告的披露截止日"""
    year = last_report_date.year
    month = last_report_date.month

    # 判断最后报告是哪个报告期，推算下一个
    if month <= 3:  # 最后是Q4/年报 → 下一个是一季报
        return ExpectedReport(period="一季报", deadline=date(year, 4, 30))
    elif month <= 6:  # 最后是Q1 → 下一个是半年报
        return ExpectedReport(period="半年报", deadline=date(year, 8, 31))
    elif month <= 9:  # 最后是Q2 → 下一个是三季报
        return ExpectedReport(period="三季报", deadline=date(year, 10, 31))
    else:  # 最后是Q3 → 下一个是年报
        return ExpectedReport(period="年报", deadline=date(year + 1, 4, 30))

def check_data_staleness(last_report_date: date) -> StalenessResult:
    """检测数据新鲜度，区分正常披露周期和真正的数据缺口"""
    today = date.today()
    days_old = (today - last_report_date).days

    expected = get_next_expected_report(last_report_date)
    missing_report = today > expected.deadline

    if missing_report:
        return StalenessResult(
            level="CRITICAL",
            reason=f"缺少{expected.period}，披露截止日{expected.deadline}已过",
            expected_report=expected
        )
    elif days_old > 180:
        return StalenessResult(
            level="WARNING",
            reason=f"数据已{days_old}天，{expected.period}预计{expected.deadline}前披露",
            expected_report=expected
        )
    return StalenessResult(level="OK", expected_report=expected)
```

**报告生成器集成**：

```python
# src/agents/report_generator.py

def generate_report(ticker: str, market: str, quality_report: QualityReport) -> str:
    # 检测数据新鲜度
    staleness = check_data_staleness(quality_report.latest_financial_date)

    if staleness.level == "CRITICAL":
        # 生成降级报告
        return _generate_degraded_report(
            ticker=ticker,
            quality_report=quality_report,
            staleness=staleness
        )

    # 正常生成，但WARNING时插入警告
    report_content = _generate_full_report(ticker, market, quality_report)

    if staleness.level == "WARNING":
        report_content = _inject_staleness_warnings(report_content, staleness)

    return report_content

def _generate_degraded_report(ticker: str, quality_report: QualityReport,
                               staleness: StalenessResult) -> str:
    """生成降级报告：仍输出历史趋势，但明确标注数据缺口"""
    return f"""# {ticker} 降级分析报告

⚠️ **数据缺口警告**

{staleness.reason}

> 以下分析基于过期数据（{quality_report.latest_financial_date}），仅供参考趋势，**不作为投资依据**。

---

## 历史财务趋势（只读参考）

{_render_historical_trends(ticker)}

---

## 建议操作

1. 运行 `invest fetch {ticker}` 尝试更新数据
2. 访问巨潮资讯网检查是否已有新财报：http://www.cninfo.com.cn/
3. 等待{staleness.expected_report.period}发布后重新生成报告
"""
```

---

### 2.4 多数据源Fallback链

**当前链**：AKShare → BaoStock → QVeris

**目标链**：AKShare → Tushare Pro → BaoStock → 新浪实时 → QVeris

**实现**：

#### 2.4.1 Fetcher优先级配置

```python
# src/data/fetcher.py

_SOURCE_PRIORITY: dict[MarketType, list[str]] = {
    "a_share": [
        "akshare",       # 主源：财报数据最全
        "tushare",       # 备源1：需要token，财报质量高
        "baostock",      # 备源2：免费，覆盖较全
        "sina_realtime", # 备源3：实时行情（仅价格）
        "qveris",        # 备源4：公司基础信息
    ],
    "hk": ["akshare", "yfinance", "fmp"],
    "us": ["yfinance", "fmp"],
}

def _get_source(name: str) -> BaseDataSource:
    sources = {
        "akshare":       AKShareSource,
        "tushare":       TushareSource,
        "baostock":      BaoStockSource,
        "sina_realtime": SinaRealtimeSource,
        "yfinance":      YFinanceSource,
        "fmp":           FMPSource,
        "qveris":        QVerisSource,
    }
    return sources[name]()
```

#### 2.4.2 新增Tushare源适配器

```python
# src/data/tushare_source.py

import tushare as ts
from src.data.base_source import BaseDataSource
from src.data.models import IncomeStatement, BalanceSheet, CashFlow, DailyPrice

class TushareSource(BaseDataSource):
    """Tushare Pro数据源适配器"""

    def __init__(self):
        # Token配置
        self.pro = ts.pro_api("fb807267d782ca1f32a9a907c399fed4ea0a611ff94b786239fc2021")

    def health_check(self) -> bool:
        try:
            df = self.pro.query('stock_basic', exchange='', list_status='L',
                                fields='ts_code', limit=1)
            return len(df) > 0
        except Exception:
            return False

    def get_income_statements(self, ticker: str, market: str,
                               period_type: str = "annual",
                               limit: int = 10) -> list[IncomeStatement]:
        """获取利润表"""
        report_type = "1" if period_type == "annual" else "2"
        df = self.pro.income(ts_code=ticker, report_type=report_type)

        if df.empty:
            return []

        return [
            IncomeStatement(
                ticker=ticker,
                period_end_date=self._parse_date(row["end_date"]),
                period_type=period_type,
                revenue=row.get("revenue"),
                net_income=row.get("n_income"),
                eps=row.get("basic_eps"),
                shares_outstanding=row.get("total_share"),
                source="tushare"
            )
            for _, row in df.head(limit).iterrows()
        ]

    def get_balance_sheets(self, ticker: str, market: str,
                           period_type: str = "annual",
                           limit: int = 5) -> list[BalanceSheet]:
        """获取资产负债表"""
        report_type = "1" if period_type == "annual" else "2"
        df = self.pro.balancesheet(ts_code=ticker, report_type=report_type)

        if df.empty:
            return []

        return [
            BalanceSheet(
                ticker=ticker,
                period_end_date=self._parse_date(row["end_date"]),
                period_type=period_type,
                total_assets=row.get("total_assets"),
                total_liabilities=row.get("total_liab"),
                total_equity=row.get("total_hldr_eqy_inc_min_int"),
                current_assets=row.get("total_cur_assets"),
                current_liabilities=row.get("total_cur_liab"),
                total_debt=row.get("lt_borr", 0) + row.get("st_borr", 0),
                source="tushare"
            )
            for _, row in df.head(limit).iterrows()
        ]

    def get_cash_flows(self, ticker: str, market: str,
                       period_type: str = "annual",
                       limit: int = 5) -> list[CashFlow]:
        """获取现金流量表"""
        report_type = "1" if period_type == "annual" else "2"
        df = self.pro.cashflow(ts_code=ticker, report_type=report_type)

        if df.empty:
            return []

        return [
            CashFlow(
                ticker=ticker,
                period_end_date=self._parse_date(row["end_date"]),
                period_type=period_type,
                operating_cash_flow=row.get("n_cashflow_act"),
                capital_expenditure=row.get("c_pay_acq_const_fiam"),
                free_cash_flow=self._calc_fcf(row),
                depreciation=row.get("depr_fa_coga_dpba"),
                source="tushare"
            )
            for _, row in df.head(limit).iterrows()
        ]

    def get_daily_prices(self, ticker: str, market: str,
                         start_date=None, end_date=None) -> list[DailyPrice]:
        """获取日线行情"""
        df = self.pro.daily(
            ts_code=ticker,
            start_date=start_date.strftime("%Y%m%d") if start_date else None,
            end_date=end_date.strftime("%Y%m%d") if end_date else None
        )

        if df.empty:
            return []

        return [
            DailyPrice(
                ticker=ticker,
                date=self._parse_date(row["trade_date"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["vol"],
                source="tushare"
            )
            for _, row in df.iterrows()
        ]

    def _parse_date(self, date_str: str):
        from datetime import datetime
        return datetime.strptime(date_str, "%Y%m%d").date()

    def _calc_fcf(self, row) -> float | None:
        ocf = row.get("n_cashflow_act")
        capex = row.get("c_pay_acq_const_fiasm")
        if ocf is not None and capex is not None:
            return ocf - abs(capex)
        return None
```

#### 2.4.3 新增新浪实时行情源

```python
# src/data/sina_source.py

import requests
import re
from datetime import date
from src.data.base_source import BaseDataSource
from src.data.models import DailyPrice

class SinaRealtimeSource(BaseDataSource):
    """新浪财经实时行情源（仅支持价格，不支持财报）"""

    def health_check(self) -> bool:
        try:
            resp = requests.get(
                "http://hq.sinajs.cn/list=sh000001",
                headers={"Referer": "http://finance.sina.com.cn"},
                timeout=5
            )
            return resp.status_code == 200 and "var hq_str" in resp.text
        except Exception:
            return False

    def get_daily_prices(self, ticker: str, market: str, **kwargs) -> list[DailyPrice]:
        """获取最新实时价格（仅返回当天数据）"""
        code = self._convert_ticker(ticker)
        url = f"http://hq.sinajs.cn/list={code}"

        resp = requests.get(
            url,
            headers={"Referer": "http://finance.sina.com.cn"},
            timeout=10
        )
        resp.encoding = "gbk"

        data = self._parse_response(resp.text)
        if not data:
            return []

        return [DailyPrice(
            ticker=ticker,
            date=date.today(),
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            volume=data["volume"],
            source="sina"
        )]

    def _convert_ticker(self, ticker: str) -> str:
        """转换ticker格式: 601808.SH → sh601808"""
        code, exchange = ticker.split(".")
        prefix = "sh" if exchange.upper() in ["SH", "SS"] else "sz"
        return f"{prefix}{code}"

    def _parse_response(self, text: str) -> dict | None:
        """解析新浪行情返回数据"""
        # 格式: var hq_str_sh601808="中海油服,20.42,20.45,20.30,..."
        match = re.search(r'="([^"]+)"', text)
        if not match:
            return None

        fields = match.group(1).split(",")
        if len(fields) < 10:
            return None

        return {
            "name": fields[0],
            "open": float(fields[1]),
            "close": float(fields[3]),  # 当前价
            "high": float(fields[4]),
            "low": float(fields[5]),
            "volume": float(fields[8]),
        }

    # 不支持财报数据
    def get_income_statements(self, *args, **kwargs):
        raise NotImplementedError("新浪源仅支持实时行情，不支持财报数据")

    def get_balance_sheets(self, *args, **kwargs):
        raise NotImplementedError("新浪源仅支持实时行情")

    def get_cash_flows(self, *args, **kwargs):
        raise NotImplementedError("新浪源仅支持实时行情")
```

---

### 2.5 报告语言去Agent化

**问题**：报告中出现"根据Buffett Agent分析..."，暴露内部实现。

**解决方案**：建立Agent名称到专业术语的映射表，在报告生成和Prompt中统一替换。

**映射表**：

```python
# src/agents/report_generator.py

AGENT_NAME_MAPPING = {
    "warren_buffett": "护城河与管理层分析",
    "ben_graham": "防御型投资标准检验",
    "valuation": "多方法估值模型",
    "fundamentals": "财务健康度评估",
    "contrarian": "反向视角风险分析",
    "sentiment": "市场情绪监测",
}

def humanize_agent_name(agent_name: str) -> str:
    """将内部Agent名称转换为专业投资术语"""
    return AGENT_NAME_MAPPING.get(agent_name, agent_name)
```

**Prompt修改**：

```python
# src/llm/prompts.py

# 修改前
REPORT_CH2_SYSTEM = """你是价值投资分析师。基于Buffett和Graham Agent的分析，撰写竞争力章节..."""

# 修改后
REPORT_CH2_SYSTEM = """你是价值投资分析师。基于护城河分析和防御型投资标准检验结果，撰写竞争力章节...

【重要约束】
- 不要在输出中提及"Agent"、"系统"、"代码"等内部概念
- 使用专业投资分析语言，例如：
  - "估值模型显示..." 而非 "Valuation Agent显示..."
  - "财务质量分析表明..." 而非 "Fundamentals Agent表明..."
  - "反向视角风险提示..." 而非 "Contrarian Agent质疑..."
"""
```

---

## 三、文件改动清单

| 文件 | 改动类型 | 改动内容 |
|------|----------|----------|
| `src/agents/ben_graham.py` | 修改 | 信号硬规则 + 代码强制覆盖LLM |
| `src/agents/valuation.py` | 修改 | 异常检测 + 中位数验证 + 降级模式 |
| `src/data/quality.py` | 修改 | 披露周期检测 + StalenessResult |
| `src/agents/report_generator.py` | 修改 | 硬阻断 + 降级报告 + 去Agent化 |
| `src/data/fetcher.py` | 修改 | 新增Tushare/Sina优先级 |
| `src/data/tushare_source.py` | **新增** | Tushare Pro适配器 |
| `src/data/sina_source.py` | **新增** | 新浪实时行情适配器 |
| `src/llm/prompts.py` | 修改 | 去Agent化表述 |

---

## 四、测试计划

### 4.1 Graham Agent测试用例

| 场景 | 输入 | 期望输出 |
|------|------|----------|
| 0/7通过 | criteria_passed=0 | signal=bearish, confidence=0.70 |
| 1/7通过，完整度0.5 | criteria_passed=1, completeness=0.5 | signal=bearish, confidence≈0.475 |
| 4/7通过，LLM返回bullish | criteria_passed=4, llm_signal=bullish | signal=neutral (被覆盖) |
| 7/7通过 | criteria_passed=7 | 由LLM决定 |

### 4.2 估值异常测试用例

| 场景 | 输入 | 期望输出 |
|------|------|----------|
| 负目标价 | target=-5.94 | exclude_from_weighted=True |
| 偏离当前价>80% | target=5, current=20 | exclude_from_weighted=True |
| 与中位数偏离>50% | target=5, median=15 | exclude_from_weighted=True |
| 仅1个有效方法 | valid_methods=1 | 降级模式，confidence=0.25 |

### 4.3 数据新鲜度测试用例

| 场景 | 输入 | 期望输出 |
|------|------|----------|
| 正常披露周期内 | last=2025-12-31, today=2026-03-08 | level=WARNING |
| 缺少应披露报告 | last=2025-06-30, today=2026-03-08 | level=CRITICAL |
| 新鲜数据 | last=2026-01-15, today=2026-03-08 | level=OK |

---

## 五、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| Tushare token限流 | 实现请求间隔，失败时fallback到下一源 |
| 新浪IP被封 | 实现退避重试，限制请求频率 |
| LLM返回格式不一致 | 代码强制覆盖是最后防线 |
| 披露周期计算边界情况 | 添加单元测试覆盖所有日期边界 |

---

## 六、实现优先级

1. **P0 阻断性**：Graham信号逻辑修复（影响报告正确性）
2. **P0 阻断性**：估值异常检测（影响目标价可靠性）
3. **P1 高优先**：数据新鲜度硬阻断
4. **P1 高优先**：Tushare数据源集成
5. **P2 正常**：新浪实时行情源
6. **P2 正常**：报告语言去Agent化

---

*文档版本: v1.0*
*最后更新: 2026-03-08*
