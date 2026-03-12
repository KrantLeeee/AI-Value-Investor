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
    ProfitWarning,
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
    Handles unit suffixes: '万亿' (×1e12), '亿' (×1e8), '万' (×1e4), '千' (×1e3), '百' (×1e2), plain floats, False, '--'.

    Note: Order matters - check longer suffixes first to handle edge cases like "万亿".
    """
    if val is None or val is False:
        return None
    s = str(val).strip().replace(",", "").replace("，", "")
    if s in ("--", "-", "—", "", "None", "nan", "False"):
        return None
    try:
        # Check in order: 万亿 → 亿 → 万 → 千 → 百 (longest first)
        if s.endswith("万亿"):
            return float(s[:-2]) * 1e12
        if s.endswith("亿"):
            return float(s[:-1]) * 1e8
        if s.endswith("万"):
            return float(s[:-1]) * 1e4
        if s.endswith("千"):
            return float(s[:-1]) * 1e3
        if s.endswith("百"):
            return float(s[:-1]) * 1e2
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

        # Try to get current shares outstanding from stock info
        shares_outstanding = None
        try:
            info_df = ak.stock_individual_info_em(symbol=code)
            if info_df is not None and not info_df.empty:
                # info_df has columns 'item' and 'value', find '总股本'
                shares_row = info_df[info_df['item'] == '总股本']
                if not shares_row.empty:
                    shares_outstanding = _safe_float(shares_row['value'].iloc[0])
        except Exception as e:
            logger.debug("[AKShare] Failed to get shares_outstanding for %s: %s", ticker, e)

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

                # Calculate shares_outstanding from net_income / eps if not available
                calc_shares = None
                if shares_outstanding:
                    calc_shares = shares_outstanding
                elif net_income and eps and abs(eps) > 0.001:
                    # EPS is in yuan, net_income is in yuan, so shares = net_income / eps
                    calc_shares = net_income / eps

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
                    shares_outstanding=calc_shares,
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

        Note: Financial institutions (banks/insurance) have different balance sheet
        structure and field names. They don't use "流动资产/流动负债" concepts.
        """
        if market != "a_share":
            return []
        import akshare as ak

        code = _clean_ticker(ticker, market)
        indicator = "按年度" if period_type == "annual" else "按报告期"
        results: list[BalanceSheet] = []

        # Check if this is a financial institution (bank/insurance)
        # Financial institutions have different balance sheet structures
        is_financial = code.startswith(("601318", "601628", "601398", "601939",  # Insurance & Big banks
                                         "601166", "600036", "601288", "601229",  # Joint-stock banks
                                         "601988", "601328", "601818", "600016",  # More banks
                                         "601601", "600000", "601169", "002142",  # More banks
                                         "601128", "601838"))  # More banks

        try:
            df = ak.stock_financial_debt_ths(symbol=code, indicator=indicator)
            if df is None or df.empty:
                return []
            df = df.sort_values("报告期", ascending=False).iloc[:limit]

            for _, row in df.iterrows():
                p_date = _parse_period_date(row.get("报告期"), indicator)
                if p_date is None:
                    continue

                # Helper to get value from multiple possible column names
                def _get_val(*cols):
                    for col in cols:
                        v = _parse_cn_number(row.get(col))
                        if v is not None:
                            return v
                    return None

                # Total assets - try multiple field names
                total_assets = _get_val(
                    "*资产合计", "资产合计", "*资产总计", "资产总计",
                    "资产总额", "*资产总额"
                )
                total_liab = _get_val(
                    "*负债合计", "负债合计", "*负债总计", "负债总计",
                    "负债总额", "*负债总额"
                )
                total_equity = _get_val(
                    "*所有者权益（或股东权益）合计", "所有者权益（或股东权益）合计",
                    "*股东权益合计", "股东权益合计",
                    "*归属于母公司所有者权益合计", "归属于母公司所有者权益合计",
                    "*归属于母公司股东权益合计", "归属于母公司股东权益合计"
                )

                # For financial institutions, current_assets/current_liabilities N/A
                if is_financial:
                    current_assets = None
                    current_liab = None
                else:
                    # API returns "流动资产" as empty string, actual data is in "流动资产合计"
                    current_assets = _get_val("流动资产合计", "流动资产", "*流动资产合计")
                    current_liab = _get_val("流动负债合计", "流动负债", "*流动负债合计")

                cash = _get_val("货币资金", "*货币资金", "现金及现金等价物")
                st_debt = _parse_cn_number(row.get("短期借款"))
                lt_debt = _parse_cn_number(row.get("长期借款"))
                # Include other debt items for more complete calculation
                lt_payables = _parse_cn_number(row.get("长期应付款合计") or row.get("长期应付款"))

                # For financial institutions, debt calculation is different
                if is_financial:
                    # Insurance/banks: use total liabilities as proxy for total debt
                    total_debt = total_liab
                else:
                    # Calculate total_debt: if all components are None/False, company has no debt (0.0)
                    # If any component has positive value, sum them
                    debt_components = [st_debt, lt_debt, lt_payables]
                    debt_values = [d for d in debt_components if d is not None and d > 0]
                    # If we have liabilities data but no debt items, company has 0 debt
                    if total_liab is not None:
                        total_debt = sum(debt_values) if debt_values else 0.0
                    else:
                        total_debt = sum(debt_values) if debt_values else None

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

            logger.info("[AKShare] %s balance(%s): %d rows (financial=%s)", ticker, indicator, len(results), is_financial)
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

        # Get current price for PE/PB calculation
        # First try from database (most reliable), then fall back to API
        current_price = None
        try:
            from src.data.database import get_latest_prices
            prices = get_latest_prices(ticker, limit=1)
            if prices:
                current_price = _safe_float(prices[0].get('close'))
        except Exception as e:
            logger.debug("[AKShare] Failed to get price from DB for %s: %s", ticker, e)

        # Fallback to API if DB has no price
        if current_price is None:
            try:
                code = _clean_ticker(ticker, market)
                info_df = ak.stock_individual_info_em(symbol=code)
                if info_df is not None and not info_df.empty:
                    price_row = info_df[info_df['item'] == '最新']
                    if not price_row.empty:
                        current_price = _safe_float(price_row['value'].iloc[0])
            except Exception as e:
                logger.debug("[AKShare] Failed to get current price from API for %s: %s", ticker, e)

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
                # Actual EM API column names (confirmed via testing):
                #   ROEJQ = ROE加权, ZZCJLL = 总资产净利率
                #   XSJLL = 销售净利率, XSMLL = 销售毛利率
                #   TOTALOPERATEREVETZ = 营收YoY, PARENTNETPROFITTZ = 净利YoY
                #   EPSJB = 基本每股收益, BPS = 每股净资产
                def _col(*names):
                    for n in names:
                        if n in df.columns:
                            return _safe_float(row.get(n))
                    return None

                # Calculate PE and PB ratios
                eps = _col("EPSJB", "EPS")
                bps = _col("BPS")
                pe_ratio = None
                pb_ratio = None
                if current_price and eps and abs(eps) > 0.001:
                    pe_ratio = round(current_price / eps, 2)
                if current_price and bps and abs(bps) > 0.001:
                    pb_ratio = round(current_price / bps, 2)

                results.append(FinancialMetrics(
                    ticker=ticker,
                    date=p_date,
                    pe_ratio=pe_ratio,
                    pb_ratio=pb_ratio,
                    roe=_col("ROEJQ", "ROE_WEIGHTAVG", "ROE_AVG", "ROE"),
                    roa=_col("ZZCJLL", "ROAGP", "ROA"),
                    operating_margin=_col("XSJLL", "OPERATE_PROFIT_RATIO"),
                    gross_margin=_col("XSMLL", "GROSS_PROFIT_MARGIN"),
                    revenue_growth=_col("TOTALOPERATEREVETZ", "REVENUE_YOY", "REVENUE_GROW_RATE"),
                    net_income_growth=_col("PARENTNETPROFITTZ", "NETPROFIT_YOY", "NETPROFIT_GROW_RATE"),
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

    # ── Profit Warnings (业绩预告) ─────────────────────────────────────────────

    def get_profit_warnings(
        self, ticker: str, market: MarketType, limit: int = 4,
    ) -> list[ProfitWarning]:
        """
        East Money 业绩预告: stock_yjyg_em (A-share only).
        Fetches profit warning announcements for a specific stock.

        Returns recent profit warnings sorted by report date (newest first).
        """
        if market != "a_share":
            return []
        import akshare as ak

        code = _clean_ticker(ticker, market)
        results: list[ProfitWarning] = []

        try:
            # Try fetching all profit warnings and filter by ticker
            # stock_yjyg_em returns all A-share profit warnings by date
            # We need to filter by code
            df = ak.stock_yjyg_em(date="")  # Empty date = most recent
            if df is None or df.empty:
                logger.debug("[AKShare] No profit warnings available from stock_yjyg_em")
                return []

            # Filter by stock code - the API returns '股票代码' column
            if "股票代码" in df.columns:
                df = df[df["股票代码"] == code]
            elif "代码" in df.columns:
                df = df[df["代码"] == code]

            if df.empty:
                logger.debug("[AKShare] No profit warnings found for %s", ticker)
                return []

            # Sort by report period (newest first) and take limit
            if "报告期" in df.columns:
                df = df.sort_values("报告期", ascending=False).head(limit)

            for _, row in df.iterrows():
                try:
                    # Parse report date
                    report_date_str = str(row.get("报告期", ""))
                    if report_date_str and report_date_str != "nan":
                        report_date = pd.to_datetime(report_date_str).date()
                    else:
                        continue

                    # Parse publish date
                    publish_date_str = str(row.get("公告日期", "") or row.get("发布日期", ""))
                    if publish_date_str and publish_date_str != "nan":
                        publish_date = pd.to_datetime(publish_date_str).date()
                    else:
                        publish_date = report_date

                    # Parse warning type
                    warning_type = str(row.get("业绩变动类型", "") or row.get("预告类型", "") or "不确定")

                    # Parse change percentages
                    change_min = _safe_float(row.get("预计增幅下限", "") or row.get("增幅下限", ""))
                    change_max = _safe_float(row.get("预计增幅上限", "") or row.get("增幅上限", ""))

                    # Parse profit values
                    profit_min = _parse_cn_number(row.get("预计净利润下限", "") or row.get("净利润下限", ""))
                    profit_max = _parse_cn_number(row.get("预计净利润上限", "") or row.get("净利润上限", ""))
                    last_profit = _parse_cn_number(row.get("上年同期净利润", "") or row.get("去年同期", ""))

                    # Parse reason
                    reason = str(row.get("业绩变动原因", "") or row.get("变动原因", "") or "")
                    if reason == "nan":
                        reason = None

                    results.append(ProfitWarning(
                        ticker=ticker,
                        report_date=report_date,
                        publish_date=publish_date,
                        warning_type=warning_type,
                        change_pct_min=change_min,
                        change_pct_max=change_max,
                        profit_min=profit_min,
                        profit_max=profit_max,
                        last_year_profit=last_profit,
                        reason=reason if reason else None,
                        source=self.source_name,
                    ))
                except Exception as e:
                    logger.debug("[AKShare] Failed to parse profit warning row: %s", e)
                    continue

            logger.info("[AKShare] %s profit_warnings: %d rows", ticker, len(results))
        except Exception as e:
            logger.warning("[AKShare] get_profit_warnings failed for %s: %s", ticker, e)

        return results


# ── Standalone helper functions ─────────────────────────────────────────────────


def fetch_company_basic_info(stock_code: str) -> dict:
    """
    Fetch company basic information from AKShare.

    Args:
        stock_code: Stock code without market suffix (e.g., '600519')

    Returns:
        dict with established_date, registered_capital, employee_count
    """
    import akshare as ak

    try:
        info_df = ak.stock_individual_info_em(symbol=stock_code)
        info_dict = dict(zip(info_df['item'], info_df['value']))

        return {
            'established_date': info_dict.get('上市时间', '未知'),
            'registered_capital': info_dict.get('注册资本', '未知'),
            'employee_count': info_dict.get('员工人数', '未知'),
        }
    except Exception as e:
        logger.warning(f"Failed to fetch company info for {stock_code}: {e}")
        return {
            'established_date': '未知',
            'registered_capital': '未知',
            'employee_count': '未知',
        }
