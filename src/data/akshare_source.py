"""AKShare data source adapter — primary source for A-share and HK data.

Source reference: akshare-main/akshare/stock_fundamental/stock_finance_ths.py
Key facts from source code:
  - stock_financial_benefit_ths: income statement (利润表)
    * indicator options: "按报告期" | "按单季度" | "按年度"
    * "按年度": 报告期 column is INTEGER year (2024, 2023...)
    * "按报告期": 报告期 column is "YYYY-MM-DD" string
    * Numeric columns contain "亿"/"万" unit suffixes
    * Core metrics prefixed with "*": "*净利润", "*营业总收入", "*营业总成本"
  - stock_financial_debt_ths: balance sheet (资产负债表)
    * Same column format, core metrics: "*资产合计", "*负债合计", "*所有者权益合计"
  - stock_financial_cash_ths: cash flow (现金流量表)
    * Core metrics: "*经营活动产生的现金流量净额", "*投资活动产生的现金流量净额"
  - stock_financial_analysis_indicator_em: requires symbol WITH market suffix
    * symbol format: "601808.SH" (not "601808")
    * returns raw numeric floats, NO unit suffixes
"""

from datetime import date

import pandas as pd

from src.data.base_source import BaseDataSource
from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    FinancialMetrics,
    IncomeStatement,
    MarketType,
    NewsItem,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Ticker format helpers ──────────────────────────────────────────────────────

def _clean_ticker(ticker: str, market: MarketType) -> str:
    """Convert standard ticker to AKShare format.
    a_share: "601808.SH" → "601808"
    hk:      "0883.HK"  → "00883" (5-digit, zero-padded)
    """
    if market == "a_share":
        return ticker.split(".")[0]
    elif market == "hk":
        code = ticker.upper().replace(".HK", "")
        return code.zfill(5)
    return ticker


def _ticker_with_suffix(ticker: str) -> str:
    """Return the ticker WITH market suffix, for EM APIs.
    "601808.SH" stays as is; "601808" remains bare (should always have suffix).
    """
    return ticker  # our tickers always carry .SH/.SZ/.HK suffix


# ── Number parsing ─────────────────────────────────────────────────────────────

def _parse_cn_number(val) -> float | None:
    """
    Parse Chinese financial number strings from THS API.
    Handles unit suffixes: '亿' (×1e8), '万' (×1e4), plain floats, False, '--'.
    """
    if val is None or val is False:
        return None
    s = str(val).strip().replace(",", "").replace("，", "")
    if s in ("--", "-", "—", "", "None", "nan", "False"):
        return None
    try:
        if s.endswith("亿"):
            return float(s[:-1]) * 1e8
        if s.endswith("万"):
            return float(s[:-1]) * 1e4
        f = float(s)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    """Parse a plain numeric value (from EM API, already numeric)."""
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").strip())
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def _parse_period_date(val, indicator: str) -> date | None:
    """
    Parse the 报告期 column to a date.
    - "按年度": val is INTEGER year like 2024 → convert to Dec 31
    - "按报告期" / "按单季度": val is "2024-12-31" string
    """
    if val is None:
        return None
    s = str(val).strip()
    if s in ("", "nan", "None"):
        return None
    try:
        if indicator == "按年度":
            # integer year → year-end date
            year = int(float(s))
            return date(year, 12, 31)
        else:
            return pd.to_datetime(s).date()
    except (ValueError, TypeError):
        return None


# ── AKShare Source ─────────────────────────────────────────────────────────────

class AKShareSource(BaseDataSource):
    source_name = "akshare"

    def supports_market(self, market: MarketType) -> bool:
        return market in ("a_share", "hk")

    def health_check(self) -> bool:
        try:
            import akshare as ak
            ak.tool_trade_date_hist_sina()
            return True
        except Exception as e:
            logger.warning("[AKShare] health_check failed: %s", e)
            return False

    # ── Prices ────────────────────────────────────────────────────────────────

    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        import akshare as ak

        code = _clean_ticker(ticker, market)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        results: list[DailyPrice] = []

        try:
            if market == "a_share":
                df = ak.stock_zh_a_hist(
                    symbol=code, period="daily",
                    start_date=start_str, end_date=end_str,
                    adjust="qfq",
                )
                for _, row in df.iterrows():
                    results.append(DailyPrice(
                        ticker=ticker, market=market,
                        date=pd.to_datetime(row["日期"]).date(),
                        open=_safe_float(row["开盘"]),
                        high=_safe_float(row["最高"]),
                        low=_safe_float(row["最低"]),
                        close=float(row["收盘"]),
                        volume=int(row["成交量"]),
                        source=self.source_name,
                    ))
            elif market == "hk":
                df = ak.stock_hk_daily(symbol=code, adjust="qfq")
                # Convert date column to date objects to avoid comparison errors with strings/objects
                df["date"] = pd.to_datetime(df["date"]).dt.date
                df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
                for _, row in df.iterrows():
                    results.append(DailyPrice(
                        ticker=ticker, market=market,
                        date=pd.to_datetime(row["date"]).date(),
                        open=_safe_float(row["open"]),
                        high=_safe_float(row["high"]),
                        low=_safe_float(row["low"]),
                        close=float(row["close"]),
                        volume=int(row.get("volume", 0)),
                        source=self.source_name,
                    ))
            logger.info("[AKShare] %s: fetched %d price rows", ticker, len(results))
        except Exception as e:
            logger.warning("[AKShare] get_daily_prices failed for %s: %s", ticker, e)

        return results

    # ── Income Statement ───────────────────────────────────────────────────────

    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        """
        THS 同花顺 利润表: stock_financial_benefit_ths
        indicator mapping: annual → "按年度", quarterly → "按报告期"
        Column names (with * prefix are core metrics, already confirmed):
          *净利润, *营业总收入, *营业总成本, *归属于母公司所有者的净利润
          一、营业总收入, 二、营业总成本, 三、营业利润, 四、利润总额
          销售费用, 管理费用, 研发费用, 财务费用, （一）基本每股收益
        """
        if market != "a_share":
            return []
        import akshare as ak

        code = _clean_ticker(ticker, market)
        indicator = "按年度" if period_type == "annual" else "按报告期"
        results: list[IncomeStatement] = []

        try:
            df = ak.stock_financial_benefit_ths(symbol=code, indicator=indicator)
            if df is None or df.empty:
                return []
            # Sort newest first, take limit rows
            df = df.sort_values("报告期", ascending=False).iloc[:limit]

            for _, row in df.iterrows():
                p_date = _parse_period_date(row.get("报告期"), indicator)
                if p_date is None:
                    continue

                revenue = _parse_cn_number(row.get("*营业总收入") or row.get("一、营业总收入"))
                net_income = _parse_cn_number(row.get("*净利润") or row.get("五、净利润"))
                op_cost = _parse_cn_number(row.get("*营业总成本") or row.get("二、营业总成本"))
                op_profit = _parse_cn_number(row.get("三、营业利润"))
                cogs = _parse_cn_number(row.get("其中：营业成本"))
                gross = (revenue - cogs) if (revenue and cogs) else None
                eps = _parse_cn_number(row.get("（一）基本每股收益"))
                # net_attr = net income attributable to parent
                net_attr = _parse_cn_number(
                    row.get("*归属于母公司所有者的净利润") or row.get("归属于母公司所有者的净利润")
                )

                results.append(IncomeStatement(
                    ticker=ticker,
                    period_end_date=p_date,
                    period_type=period_type,
                    revenue=revenue,
                    cost_of_revenue=cogs,
                    gross_profit=gross,
                    operating_income=op_profit,
                    net_income=net_income or net_attr,
                    eps=eps,
                    source=self.source_name,
                ))

            logger.info("[AKShare] %s income(%s): %d rows", ticker, indicator, len(results))
        except Exception as e:
            logger.warning("[AKShare] get_income_statements failed for %s: %s", ticker, e)

        return results

    # ── Balance Sheet ──────────────────────────────────────────────────────────

    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        """
        THS 同花顺 资产负债表: stock_financial_debt_ths
        Core columns (with * prefix):
          *资产合计, *负债合计, *所有者权益（或股东权益）合计, *归属于母公司所有者权益合计
          流动资产, 流动负债, 货币资金, 短期借款, 长期借款
        """
        if market != "a_share":
            return []
        import akshare as ak

        code = _clean_ticker(ticker, market)
        indicator = "按年度" if period_type == "annual" else "按报告期"
        results: list[BalanceSheet] = []

        try:
            df = ak.stock_financial_debt_ths(symbol=code, indicator=indicator)
            if df is None or df.empty:
                return []
            df = df.sort_values("报告期", ascending=False).iloc[:limit]

            for _, row in df.iterrows():
                p_date = _parse_period_date(row.get("报告期"), indicator)
                if p_date is None:
                    continue

                total_assets = _parse_cn_number(row.get("*资产合计"))
                total_liab = _parse_cn_number(row.get("*负债合计"))
                total_equity = _parse_cn_number(
                    row.get("*所有者权益（或股东权益）合计")
                )
                current_assets = _parse_cn_number(row.get("流动资产"))
                current_liab = _parse_cn_number(row.get("流动负债"))
                cash = _parse_cn_number(row.get("货币资金"))
                st_debt = _parse_cn_number(row.get("短期借款"))
                lt_debt = _parse_cn_number(row.get("长期借款"))
                total_debt = (
                    (st_debt or 0) + (lt_debt or 0)
                ) if (st_debt or lt_debt) else None

                # Book value per share not available from THS debt table directly
                results.append(BalanceSheet(
                    ticker=ticker,
                    period_end_date=p_date,
                    period_type=period_type,
                    total_assets=total_assets,
                    total_liabilities=total_liab,
                    total_equity=total_equity,
                    current_assets=current_assets,
                    current_liabilities=current_liab,
                    cash_and_equivalents=cash,
                    total_debt=total_debt,
                    source=self.source_name,
                ))

            logger.info("[AKShare] %s balance(%s): %d rows", ticker, indicator, len(results))
        except Exception as e:
            logger.warning("[AKShare] get_balance_sheets failed for %s: %s", ticker, e)

        return results

    # ── Cash Flow ──────────────────────────────────────────────────────────────

    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        """
        THS 同花顺 现金流量表: stock_financial_cash_ths
        Core columns (with * prefix):
          *经营活动产生的现金流量净额
          *投资活动产生的现金流量净额
          *筹资活动产生的现金流量净额
          *期末现金及现金等价物余额
          *现金及现金等价物净增加额
        """
        if market != "a_share":
            return []
        import akshare as ak

        code = _clean_ticker(ticker, market)
        indicator = "按年度" if period_type == "annual" else "按报告期"
        results: list[CashFlow] = []

        try:
            df = ak.stock_financial_cash_ths(symbol=code, indicator=indicator)
            if df is None or df.empty:
                return []
            df = df.sort_values("报告期", ascending=False).iloc[:limit]

            for _, row in df.iterrows():
                p_date = _parse_period_date(row.get("报告期"), indicator)
                if p_date is None:
                    continue

                op_cf = _parse_cn_number(row.get("*经营活动产生的现金流量净额"))
                inv_cf = _parse_cn_number(row.get("*投资活动产生的现金流量净额"))
                fin_cf = _parse_cn_number(row.get("*筹资活动产生的现金流量净额"))
                # FCF (approximation) = operating CF + investing CF
                # Note: investing CF is typically negative (capex) for healthy companies
                fcf = (
                    (op_cf + inv_cf)
                    if (op_cf is not None and inv_cf is not None)
                    else op_cf
                )

                results.append(CashFlow(
                    ticker=ticker,
                    period_end_date=p_date,
                    period_type=period_type,
                    operating_cash_flow=op_cf,
                    free_cash_flow=fcf,
                    source=self.source_name,
                ))

            logger.info("[AKShare] %s cashflow(%s): %d rows", ticker, indicator, len(results))
        except Exception as e:
            logger.warning("[AKShare] get_cash_flows failed for %s: %s", ticker, e)

        return results

    # ── Financial Metrics ──────────────────────────────────────────────────────

    def get_financial_metrics(
        self, ticker: str, market: MarketType, limit: int = 10,
    ) -> list[FinancialMetrics]:
        """
        East Money 财务分析主要指标: stock_financial_analysis_indicator_em
        IMPORTANT: symbol must include market suffix, e.g. "601808.SH" (not "601808").
        Returns raw numeric floats (no unit suffix). Key columns:
          REPORT_DATE, ROE (净资产收益率), ROA (总资产净利润率),
          NETPROFIT_MARGIN, GROSS_PROFIT_MARGIN, etc.
        Falls back silently if EM is unavailable (rate-limited).
        """
        if market != "a_share":
            return []
        import akshare as ak

        # EM API requires the full ticker with exchange suffix
        em_symbol = ticker  # e.g. "601808.SH" — already in our standard format
        results: list[FinancialMetrics] = []

        try:
            df = ak.stock_financial_analysis_indicator_em(symbol=em_symbol)
            if df is None or df.empty:
                logger.warning("[AKShare] EM API returned empty for %s (possibly rate-limited or delisted)", em_symbol)
                return []

            # Column names are English from EM API
            # Sort newest first
            date_col = "REPORT_DATE" if "REPORT_DATE" in df.columns else df.columns[0]
            df = df.sort_values(date_col, ascending=False).iloc[:limit]

            for _, row in df.iterrows():
                try:
                    if "REPORT_DATE" in df.columns:
                        p_date = pd.to_datetime(row["REPORT_DATE"]).date()
                    else:
                        p_date = pd.to_datetime(row.iloc[0]).date()
                except Exception:
                    continue

                # Map EM column names → our model fields
                def _col(*names):
                    for n in names:
                        if n in df.columns:
                            return _safe_float(row.get(n))
                    return None

                results.append(FinancialMetrics(
                    ticker=ticker,
                    date=p_date,
                    roe=_col("ROE_WEIGHTAVG", "ROE_AVG", "ROE"),
                    roa=_col("ROAGP", "ROA"),
                    operating_margin=_col("OPERATE_PROFIT_RATIO"),
                    revenue_growth=_col("REVENUE_YOY", "REVENUE_GROW_RATE"),
                    net_income_growth=_col("NETPROFIT_YOY", "NETPROFIT_GROW_RATE"),
                    source=self.source_name,
                ))

            logger.info("[AKShare] %s metrics: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[AKShare] get_financial_metrics failed for %s: %s", ticker, e)

        return results

    # ── News ──────────────────────────────────────────────────────────────────

    def get_news(
        self, ticker: str, market: MarketType, limit: int = 50,
    ) -> list[NewsItem]:
        """East Money stock news: stock_news_em (A-share only)."""
        if market != "a_share":
            return []
        import akshare as ak

        code = _clean_ticker(ticker, market)
        results: list[NewsItem] = []

        try:
            df = ak.stock_news_em(symbol=code)
            for _, row in df.iloc[:limit].iterrows():
                results.append(NewsItem(
                    ticker=ticker,
                    title=str(row.get("新闻标题", "")),
                    publish_date=pd.to_datetime(row.get("发布时间")),
                    url=str(row.get("新闻链接", "")),
                    source=self.source_name,
                ))
        except Exception as e:
            logger.warning("[AKShare] get_news failed for %s: %s", ticker, e)

        return results
