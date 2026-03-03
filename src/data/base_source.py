"""Abstract base class for all data sources."""

from abc import ABC, abstractmethod
from datetime import date

from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    FinancialMetrics,
    IncomeStatement,
    MarketType,
    NewsItem,
)


class BaseDataSource(ABC):
    """
    All data source adapters must implement this interface.
    Ensures consistent data retrieval regardless of underlying API.
    """

    source_name: str = "base"

    @abstractmethod
    def supports_market(self, market: MarketType) -> bool:
        """Return True if this source covers the given market."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the data source is reachable."""

    @abstractmethod
    def get_daily_prices(
        self, ticker: str, market: MarketType,
        start_date: date, end_date: date,
    ) -> list[DailyPrice]:
        """Fetch OHLCV daily prices for the given ticker and date range."""

    @abstractmethod
    def get_income_statements(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[IncomeStatement]:
        """Fetch income statement records (most recent first)."""

    @abstractmethod
    def get_balance_sheets(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[BalanceSheet]:
        """Fetch balance sheet records (most recent first)."""

    @abstractmethod
    def get_cash_flows(
        self, ticker: str, market: MarketType,
        period_type: str = "annual", limit: int = 10,
    ) -> list[CashFlow]:
        """Fetch cash flow records (most recent first)."""

    def get_financial_metrics(
        self, ticker: str, market: MarketType, limit: int = 10,
    ) -> list[FinancialMetrics]:
        """
        Fetch pre-computed financial ratios.
        Default: return empty list (computed downstream by code).
        """
        return []

    def get_news(
        self, ticker: str, market: MarketType, limit: int = 50,
    ) -> list[NewsItem]:
        """Fetch recent news items. Default: empty list."""
        return []
