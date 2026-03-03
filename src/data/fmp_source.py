"""FMP (Financial Modeling Prep) API adapter — global deep financial data."""

import time
from datetime import date

import requests

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
from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

FMP_BASE = "https://financialmodelingprep.com/api"


class FMPSource(BaseDataSource):
    """
    Financial Modeling Prep API ($15/month Starter plan).
    Used for: HK/US deep financials, news, analyst estimates.
    Falls back gracefully if API key is not configured.
    """

    source_name = "fmp"

    def __init__(self):
        self._api_key = get_settings().fmp_api_key

    def _available(self) -> bool:
        return bool(self._api_key)

    def _get(self, endpoint: str, params: dict | None = None) -> list | dict | None:
        if not self._available():
            return None
        url = f"{FMP_BASE}{endpoint}"
        p = {"apikey": self._api_key, **(params or {})}
        try:
            resp = requests.get(url, params=p, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("[FMP] request failed: %s — %s", endpoint, e)
            return None

    def supports_market(self, market: MarketType) -> bool:
        return True  # FMP covers all markets

    def health_check(self) -> bool:
        if not self._available():
            logger.info("[FMP] API key not configured — skipping")
            return False
        data = self._get("/v3/profile/AAPL")
        return bool(data)

    def _to_fmp_ticker(self, ticker: str, market: MarketType) -> str:
        """
        Convert to FMP format:
        - A-share: 601808.SH → 601808.SS (Shanghai) | 002415.SZ → 002415.SZ (same)
        - HK: 0883.HK stays
        - US: AAPL stays
        """
        if market == "a_share":
            parts = ticker.upper().split(".")
            if len(parts) == 2 and parts[1] == "SH":
                return f"{parts[0]}.SS"
        return ticker

    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        fmp_ticker = self._to_fmp_ticker(ticker, market)
        data = self._get(
            f"/v3/historical-price-full/{fmp_ticker}",
            {"from": str(start_date), "to": str(end_date)},
        )
        results: list[DailyPrice] = []
        if not data or "historical" not in data:
            return []
        for row in data["historical"]:
            try:
                results.append(DailyPrice(
                    ticker=ticker, market=market,
                    date=date.fromisoformat(row["date"]),
                    open=row.get("open"), high=row.get("high"),
                    low=row.get("low"), close=float(row["close"]),
                    volume=row.get("volume"),
                    source=self.source_name,
                ))
            except (KeyError, ValueError):
                continue
        logger.info("[FMP] %s: fetched %d price rows", ticker, len(results))
        return results

    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        fmp_ticker = self._to_fmp_ticker(ticker, market)
        period = "annual" if period_type == "annual" else "quarter"
        data = self._get(f"/v3/income-statement/{fmp_ticker}",
                         {"limit": limit, "period": period})
        results: list[IncomeStatement] = []
        if not data:
            return []
        for row in data:
            try:
                results.append(IncomeStatement(
                    ticker=ticker,
                    period_end_date=date.fromisoformat(row["date"]),
                    period_type=period_type,
                    revenue=row.get("revenue"),
                    cost_of_revenue=row.get("costOfRevenue"),
                    gross_profit=row.get("grossProfit"),
                    operating_income=row.get("operatingIncome"),
                    net_income=row.get("netIncome"),
                    ebitda=row.get("ebitda"),
                    eps=row.get("eps"),
                    eps_diluted=row.get("epsdiluted"),
                    shares_outstanding=row.get("weightedAverageShsOut"),
                    source=self.source_name,
                ))
            except (KeyError, ValueError):
                continue
        return results

    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        fmp_ticker = self._to_fmp_ticker(ticker, market)
        period = "annual" if period_type == "annual" else "quarter"
        data = self._get(f"/v3/balance-sheet-statement/{fmp_ticker}",
                         {"limit": limit, "period": period})
        results: list[BalanceSheet] = []
        if not data:
            return []
        for row in data:
            try:
                results.append(BalanceSheet(
                    ticker=ticker,
                    period_end_date=date.fromisoformat(row["date"]),
                    period_type=period_type,
                    total_assets=row.get("totalAssets"),
                    total_liabilities=row.get("totalLiabilities"),
                    total_equity=row.get("totalStockholdersEquity"),
                    current_assets=row.get("totalCurrentAssets"),
                    current_liabilities=row.get("totalCurrentLiabilities"),
                    cash_and_equivalents=row.get("cashAndCashEquivalents"),
                    total_debt=row.get("totalDebt"),
                    book_value_per_share=row.get("bookValuePerShare"),
                    source=self.source_name,
                ))
            except (KeyError, ValueError):
                continue
        return results

    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        fmp_ticker = self._to_fmp_ticker(ticker, market)
        period = "annual" if period_type == "annual" else "quarter"
        data = self._get(f"/v3/cash-flow-statement/{fmp_ticker}",
                         {"limit": limit, "period": period})
        results: list[CashFlow] = []
        if not data:
            return []
        for row in data:
            try:
                op_cf = row.get("operatingCashFlow")
                capex = row.get("capitalExpenditure")
                fcf = row.get("freeCashFlow")
                results.append(CashFlow(
                    ticker=ticker,
                    period_end_date=date.fromisoformat(row["date"]),
                    period_type=period_type,
                    operating_cash_flow=op_cf,
                    capital_expenditure=abs(capex) if capex else None,
                    free_cash_flow=fcf,
                    dividends_paid=row.get("dividendsPaid"),
                    depreciation=row.get("depreciationAndAmortization"),
                    source=self.source_name,
                ))
            except (KeyError, ValueError):
                continue
        return results

    def get_financial_metrics(
        self, ticker: str, market: MarketType, limit: int = 10,
    ) -> list[FinancialMetrics]:
        fmp_ticker = self._to_fmp_ticker(ticker, market)
        data = self._get(f"/v3/key-metrics/{fmp_ticker}", {"limit": limit})
        results: list[FinancialMetrics] = []
        if not data:
            return []
        for row in data:
            try:
                results.append(FinancialMetrics(
                    ticker=ticker,
                    date=date.fromisoformat(row["date"]),
                    pe_ratio=row.get("peRatio"),
                    pb_ratio=row.get("pbRatio"),
                    ps_ratio=row.get("priceToSalesRatio"),
                    roe=row.get("roe"),
                    roa=row.get("roa"),
                    debt_to_equity=row.get("debtToEquity"),
                    current_ratio=row.get("currentRatio"),
                    dividend_yield=row.get("dividendYield"),
                    operating_margin=row.get("operatingProfitMargin"),
                    revenue_growth=row.get("revenueGrowth"),
                    net_income_growth=row.get("netIncomeGrowth"),
                    fcf_per_share=row.get("freeCashFlowPerShare"),
                    market_cap=row.get("marketCap"),
                    enterprise_value=row.get("enterpriseValue"),
                    source=self.source_name,
                ))
            except (KeyError, ValueError):
                continue
        return results

    def get_news(
        self, ticker: str, market: MarketType, limit: int = 50,
    ) -> list[NewsItem]:
        from datetime import datetime
        fmp_ticker = self._to_fmp_ticker(ticker, market)
        data = self._get(f"/v3/stock_news", {"tickers": fmp_ticker, "limit": limit})
        results: list[NewsItem] = []
        if not data:
            return []
        for row in data:
            try:
                results.append(NewsItem(
                    ticker=ticker,
                    title=row.get("title", ""),
                    publish_date=datetime.fromisoformat(
                        row["publishedDate"].replace("Z", "+00:00")
                    ),
                    url=row.get("url"),
                    source=self.source_name,
                ))
            except (KeyError, ValueError):
                continue
        return results
