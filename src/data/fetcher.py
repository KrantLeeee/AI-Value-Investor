"""
Fetcher — multi-source data orchestrator with priority-based fallback.

Priority chains per market:
  a_share: AKShare → BaoStock → FMP → manual
  hk:      AKShare → yfinance → FMP → manual
  us:      yfinance → FMP → manual
"""

import time
from datetime import date, timedelta

from src.data.akshare_source import AKShareSource
from src.data.baostock_source import BaoStockSource
from src.data.base_source import BaseDataSource
from src.data.qveris_source import QVerisSource, fetch_company_basics as _qveris_company_basics
from src.data.database import (
    get_balance_sheets,
    get_cash_flows,
    get_financial_metrics,
    get_income_statements,
    get_latest_prices,
    upsert_balance_sheets,
    upsert_cash_flows,
    upsert_daily_prices,
    upsert_financial_metrics,
    upsert_income_statements,
)
from src.data.fmp_source import FMPSource
from src.data.models import MarketType
from src.data.yfinance_source import YFinanceSource
from src.utils.logger import get_logger, log_event

logger = get_logger(__name__)

# Retry configuration for network calls
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # seconds

# Fallback source priority per market
# NOTE: FMP is excluded from a_share — their free/starter tier does not support
# Chinese A-share financial statements (returns 403). FMP only for HK/US.
# QVeris iFinD is added as tertiary A-share fallback for missing fields.
_SOURCE_PRIORITY: dict[MarketType, list[str]] = {
    "a_share": ["akshare", "baostock", "qveris"],  # qveris as tertiary fallback
    "hk":      ["akshare", "yfinance", "fmp"],
    "us":      ["yfinance", "fmp"],
}


def _get_source(name: str) -> BaseDataSource:
    sources = {
        "akshare":  AKShareSource,
        "baostock": BaoStockSource,
        "yfinance": YFinanceSource,
        "fmp":      FMPSource,
        "qveris":   QVerisSource,
    }
    return sources[name]()


def _with_retry(fn, *args, **kwargs):
    """Call fn with retries and exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning("Retry %d/%d after %ds — %s", attempt + 1, MAX_RETRIES, delay, e)
                time.sleep(delay)
            else:
                logger.error("All %d retries exhausted: %s", MAX_RETRIES, e)
                raise


class Fetcher:
    """
    Coordinates data fetching from multiple sources with fallback logic.
    Reads from and writes to the local SQLite database.
    """

    def __init__(self, default_years: int = 3):
        self.default_years = default_years

    def _fetch_with_fallback(self, market: MarketType, method: str, ticker: str, **kwargs):
        """Try each source in priority order; return first successful result."""
        priority = _SOURCE_PRIORITY.get(market, ["yfinance", "fmp"])
        for source_name in priority:
            source = _get_source(source_name)
            try:
                fn = getattr(source, method)
                result = _with_retry(fn, ticker, market, **kwargs)
                if result:
                    logger.debug("[Fetcher] %s via %s: got %d records",
                                 method, source_name, len(result))
                    return result, source_name
            except Exception as e:
                logger.warning("[Fetcher] %s failed via %s: %s", method, source_name, e)
                continue
        logger.error("[Fetcher] All sources failed for %s.%s", ticker, method)
        return [], None

    def fetch_prices(
        self,
        ticker: str,
        market: MarketType,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> int:
        """
        Fetch and persist daily price data. Returns number of new rows saved.
        Defaults to last `default_years` years if dates not specified.
        """
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=self.default_years * 365)

        t0 = time.monotonic()
        prices, source = self._fetch_with_fallback(
            market, "get_daily_prices", ticker,
            start_date=start_date, end_date=end_date,
        )
        count = upsert_daily_prices(prices)
        duration_ms = int((time.monotonic() - t0) * 1000)

        log_event("data_fetch_completed", {
            "ticker": ticker, "data_type": "prices",
            "source": source, "record_count": count, "duration_ms": duration_ms,
        })
        logger.info("[Fetcher] %s prices: %d rows from %s (%dms)", ticker, count, source, duration_ms)
        return count

    def fetch_financials(
        self,
        ticker: str,
        market: MarketType,
        limit: int = 10,
        period_type: str = "annual",
    ) -> dict[str, int]:
        """Fetch all 3 financial statements + metrics. Returns row counts per type."""
        counts: dict[str, int] = {}

        # Income statements
        stmts, _ = self._fetch_with_fallback(
            market, "get_income_statements", ticker,
            period_type=period_type, limit=limit,
        )
        counts["income"] = upsert_income_statements(stmts)

        # Balance sheets
        sheets, _ = self._fetch_with_fallback(
            market, "get_balance_sheets", ticker,
            period_type=period_type, limit=limit,
        )
        counts["balance"] = upsert_balance_sheets(sheets)

        # Cash flows
        flows, _ = self._fetch_with_fallback(
            market, "get_cash_flows", ticker,
            period_type=period_type, limit=limit,
        )
        counts["cashflow"] = upsert_cash_flows(flows)

        # Key metrics
        metrics, _ = self._fetch_with_fallback(
            market, "get_financial_metrics", ticker, limit=limit,
        )
        counts["metrics"] = upsert_financial_metrics(metrics)

        logger.info("[Fetcher] %s financials: %s", ticker, counts)
        return counts

    def fetch_all(
        self,
        ticker: str,
        market: MarketType,
        start_date: date | None = None,
        end_date: date | None = None,
        years: int | None = None,
    ) -> dict:
        """Convenience method: fetch prices + all financials for a ticker."""
        if years:
            start_date = date.today() - timedelta(days=years * 365)

        price_count = self.fetch_prices(ticker, market, start_date, end_date)
        financial_counts = self.fetch_financials(ticker, market)

        return {
            "ticker": ticker,
            "market": market,
            "prices": price_count,
            **financial_counts,
        }

    def fetch_watchlist(
        self,
        watchlist: dict | None = None,
        years: int | None = None,
    ) -> list[dict]:
        """
        Fetch all tickers in watchlist config.
        watchlist format: {"a_share": [...], "hk": [...], "us": [...]}
        """
        from src.utils.config import load_watchlist

        if watchlist is None:
            watchlist = load_watchlist()

        results = []
        for market, items in watchlist.get("watchlist", {}).items():
            for item in items:
                ticker = item["ticker"] if isinstance(item, dict) else item
                try:
                    result = self.fetch_all(ticker, market, years=years or self.default_years)
                    results.append(result)
                except Exception as e:
                    logger.error("[Fetcher] watchlist fetch failed for %s: %s", ticker, e)
                    results.append({"ticker": ticker, "market": market, "error": str(e)})

        return results

    def fetch_company_basics(self, ticker: str, market: MarketType) -> dict | None:
        """
        Fetch company basic information from QVeris iFinD.
        Returns dict with company_name, main_business, etc. or None.
        Only available for A-share tickers.
        """
        if market != "a_share":
            return None
        basics = _qveris_company_basics(ticker)
        if basics:
            logger.info("[Fetcher] %s company basics: %s", ticker, basics.get("company_name"))
        return basics

    def get_data_summary(self, ticker: str) -> dict:
        """Return a summary of locally available data for a ticker."""
        prices = get_latest_prices(ticker, limit=1)
        income = get_income_statements(ticker, limit=1)
        metrics = get_financial_metrics(ticker, limit=1)
        return {
            "ticker": ticker,
            "latest_price_date": prices[0]["date"] if prices else None,
            "latest_income_date": income[0]["period_end_date"] if income else None,
            "has_metrics": len(metrics) > 0,
        }
