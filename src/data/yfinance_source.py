"""yfinance data source adapter — HK and US market data."""

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
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _to_yf_ticker(ticker: str, market: MarketType) -> str:
    """
    Convert to yfinance ticker format:
    - HK: 0883.HK stays, or 00883.HK → 0883.HK (strip leading zeros to max 4 digits)
    - US: AAPL stays
    - A-share: 601808.SS or 002415.SZ (yfinance uses .SS for Shanghai, .SZ for Shenzhen)
    """
    if market == "hk":
        code = ticker.replace(".HK", "").replace(".hk", "").lstrip("0") or "0"
        # yfinance wants 4-digit HK codes
        return f"{int(code):04d}.HK"
    if market == "a_share":
        parts = ticker.upper().split(".")
        if len(parts) == 2:
            code, exch = parts
            suffix = "SS" if exch == "SH" else "SZ"
            return f"{code}.{suffix}"
    return ticker  # US tickers unchanged


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        import numpy as np
        if isinstance(val, float) and (pd.isna(val) or np.isnan(val)):
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


class YFinanceSource(BaseDataSource):
    source_name = "yfinance"

    def supports_market(self, market: MarketType) -> bool:
        return market in ("hk", "us", "a_share")

    def health_check(self) -> bool:
        try:
            import yfinance as yf
            t = yf.Ticker("AAPL")
            _ = t.fast_info
            return True
        except Exception as e:
            logger.warning("[yfinance] health_check failed: %s", e)
            return False

    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        import yfinance as yf

        yf_ticker = _to_yf_ticker(ticker, market)
        results: list[DailyPrice] = []

        try:
            hist = yf.Ticker(yf_ticker).history(
                start=str(start_date),
                end=str(end_date),
                auto_adjust=True,
            )
            for idx, row in hist.iterrows():
                results.append(DailyPrice(
                    ticker=ticker,
                    market=market,
                    date=idx.date(),
                    open=_safe_float(row["Open"]),
                    high=_safe_float(row["High"]),
                    low=_safe_float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]) if row["Volume"] else None,
                    source=self.source_name,
                ))
            logger.info("[yfinance] %s: fetched %d price rows", ticker, len(results))
        except Exception as e:
            logger.warning("[yfinance] get_daily_prices failed for %s: %s", ticker, e)

        return results

    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        import yfinance as yf

        yf_ticker = _to_yf_ticker(ticker, market)
        results: list[IncomeStatement] = []

        try:
            t = yf.Ticker(yf_ticker)
            df = t.financials if period_type == "annual" else t.quarterly_financials
            if df is None or df.empty:
                return []

            for col in df.columns[:limit]:
                col_date = col.date() if hasattr(col, "date") else pd.to_datetime(col).date()
                row = df[col]
                results.append(IncomeStatement(
                    ticker=ticker,
                    period_end_date=col_date,
                    period_type=period_type,
                    revenue=_safe_float(row.get("Total Revenue")),
                    cost_of_revenue=_safe_float(row.get("Cost Of Revenue")),
                    gross_profit=_safe_float(row.get("Gross Profit")),
                    operating_income=_safe_float(row.get("Operating Income")),
                    net_income=_safe_float(row.get("Net Income")),
                    ebitda=_safe_float(row.get("EBITDA")),
                    eps=_safe_float(row.get("Basic EPS")),
                    eps_diluted=_safe_float(row.get("Diluted EPS")),
                    source=self.source_name,
                ))
        except Exception as e:
            logger.warning("[yfinance] get_income_statements failed for %s: %s", ticker, e)

        return results

    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        import yfinance as yf

        yf_ticker = _to_yf_ticker(ticker, market)
        results: list[BalanceSheet] = []

        try:
            t = yf.Ticker(yf_ticker)
            df = t.balance_sheet if period_type == "annual" else t.quarterly_balance_sheet
            if df is None or df.empty:
                return []

            for col in df.columns[:limit]:
                col_date = col.date() if hasattr(col, "date") else pd.to_datetime(col).date()
                row = df[col]
                total_assets = _safe_float(row.get("Total Assets"))
                total_liab = _safe_float(row.get("Total Liabilities Net Minority Interest"))
                total_equity = _safe_float(row.get("Stockholders Equity"))
                results.append(BalanceSheet(
                    ticker=ticker,
                    period_end_date=col_date,
                    period_type=period_type,
                    total_assets=total_assets,
                    total_liabilities=total_liab,
                    total_equity=total_equity,
                    current_assets=_safe_float(row.get("Current Assets")),
                    current_liabilities=_safe_float(row.get("Current Liabilities")),
                    cash_and_equivalents=_safe_float(row.get("Cash And Cash Equivalents")),
                    total_debt=_safe_float(row.get("Total Debt")),
                    source=self.source_name,
                ))
        except Exception as e:
            logger.warning("[yfinance] get_balance_sheets failed for %s: %s", ticker, e)

        return results

    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        import yfinance as yf

        yf_ticker = _to_yf_ticker(ticker, market)
        results: list[CashFlow] = []

        try:
            t = yf.Ticker(yf_ticker)
            df = t.cashflow if period_type == "annual" else t.quarterly_cashflow
            if df is None or df.empty:
                return []

            for col in df.columns[:limit]:
                col_date = col.date() if hasattr(col, "date") else pd.to_datetime(col).date()
                row = df[col]
                op_cf = _safe_float(row.get("Operating Cash Flow"))
                capex = _safe_float(row.get("Capital Expenditure"))
                fcf = _safe_float(row.get("Free Cash Flow"))
                if fcf is None and op_cf and capex:
                    fcf = op_cf + capex  # capex is negative in yfinance
                results.append(CashFlow(
                    ticker=ticker,
                    period_end_date=col_date,
                    period_type=period_type,
                    operating_cash_flow=op_cf,
                    capital_expenditure=abs(capex) if capex else None,
                    free_cash_flow=fcf,
                    dividends_paid=_safe_float(
                        row.get("Common Stock Dividend Paid") or row.get("Dividends Paid")
                    ),
                    depreciation=_safe_float(row.get("Depreciation And Amortization")),
                    source=self.source_name,
                ))
        except Exception as e:
            logger.warning("[yfinance] get_cash_flows failed for %s: %s", ticker, e)

        return results

    def get_financial_metrics(
        self, ticker: str, market: MarketType, limit: int = 10,
    ) -> list[FinancialMetrics]:
        """Pull key metrics from yfinance info dict (P/E, P/B, etc.)."""
        import yfinance as yf
        from datetime import datetime

        yf_ticker = _to_yf_ticker(ticker, market)
        results: list[FinancialMetrics] = []

        try:
            info = yf.Ticker(yf_ticker).info
            results.append(FinancialMetrics(
                ticker=ticker,
                date=date.today(),
                pe_ratio=_safe_float(info.get("trailingPE")),
                pb_ratio=_safe_float(info.get("priceToBook")),
                ps_ratio=_safe_float(info.get("priceToSalesTrailing12Months")),
                roe=_safe_float(info.get("returnOnEquity")),
                roa=_safe_float(info.get("returnOnAssets")),
                debt_to_equity=_safe_float(info.get("debtToEquity")),
                current_ratio=_safe_float(info.get("currentRatio")),
                dividend_yield=_safe_float(info.get("dividendYield")),
                operating_margin=_safe_float(info.get("operatingMargins")),
                revenue_growth=_safe_float(info.get("revenueGrowth")),
                market_cap=_safe_float(info.get("marketCap")),
                enterprise_value=_safe_float(info.get("enterpriseValue")),
                source=self.source_name,
            ))
        except Exception as e:
            logger.warning("[yfinance] get_financial_metrics failed for %s: %s", ticker, e)

        return results
