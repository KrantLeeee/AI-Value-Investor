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
from src.data.sina_source import SinaRealtimeSource
from src.data.tushare_source import TushareSource
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

# ── Company Info Fallback ──────────────────────────────────────────────────────
# Used when QVeris credits are exhausted. Maps ticker → company name and industry.
# This provides essential context for LLM report generation.
_COMPANY_INFO_FALLBACK: dict[str, dict] = {
    # Major A-share stocks - Tech
    "002230.SZ": {"name": "科大讯飞", "industry": "人工智能/软件", "main_business": "智能语音与人工智能核心技术研发"},
    "300124.SZ": {"name": "汇川技术", "industry": "工业自动化", "main_business": "工业自动化控制产品研发与销售"},
    "688169.SH": {"name": "石头科技", "industry": "消费电子/智能家居", "main_business": "智能清洁机器人研发与销售"},
    "603881.SH": {"name": "数据港", "industry": "数据中心/IDC", "main_business": "数据中心服务与运营"},
    # Financial sector
    "601318.SH": {"name": "中国平安", "industry": "保险/金融", "main_business": "综合金融服务（保险、银行、投资）", "is_financial": True},
    "601166.SH": {"name": "兴业银行", "industry": "银行", "main_business": "银行业务", "is_financial": True},
    "600036.SH": {"name": "招商银行", "industry": "银行", "main_business": "银行业务", "is_financial": True},
    "601398.SH": {"name": "工商银行", "industry": "银行", "main_business": "银行业务", "is_financial": True},
    "601628.SH": {"name": "中国人寿", "industry": "保险", "main_business": "人寿保险", "is_financial": True},
    # Consumer
    "600519.SH": {"name": "贵州茅台", "industry": "白酒", "main_business": "茅台酒及系列酒生产销售"},
    "000858.SZ": {"name": "五粮液", "industry": "白酒", "main_business": "白酒生产与销售"},
    "000568.SZ": {"name": "泸州老窖", "industry": "白酒", "main_business": "白酒生产与销售"},
    # Energy
    "600028.SH": {"name": "中国石化", "industry": "石油化工", "main_business": "石油化工生产与销售"},
    "601857.SH": {"name": "中国石油", "industry": "石油天然气", "main_business": "石油天然气勘探开发"},
    "601808.SH": {"name": "中海油服", "industry": "油田服务", "main_business": "海洋石油技术服务"},
    # Manufacturing
    "000630.SZ": {"name": "铜陵有色", "industry": "有色金属", "main_business": "铜及相关产品生产销售"},
    "600362.SH": {"name": "江西铜业", "industry": "有色金属", "main_business": "铜采选冶炼加工"},
    # Real Estate
    "000002.SZ": {"name": "万科A", "industry": "房地产", "main_business": "房地产开发与物业服务"},
    "001979.SZ": {"name": "招商蛇口", "industry": "房地产", "main_business": "房地产开发与园区运营"},
    # Healthcare
    "600276.SH": {"name": "恒瑞医药", "industry": "医药", "main_business": "创新药研发与销售"},
    "300760.SZ": {"name": "迈瑞医疗", "industry": "医疗器械", "main_business": "医疗器械研发与销售"},
    # Auto
    "002594.SZ": {"name": "比亚迪", "industry": "新能源汽车", "main_business": "新能源汽车及电池生产"},
    "600104.SH": {"name": "上汽集团", "industry": "汽车", "main_business": "汽车研发生产销售"},
    # Utilities
    "600900.SH": {"name": "长江电力", "industry": "电力/公用事业", "main_business": "水力发电及电力销售"},
    "601985.SH": {"name": "中国核电", "industry": "电力/公用事业", "main_business": "核电发电及销售"},
}

# Retry configuration for network calls
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # seconds

# Fallback source priority per market
# NOTE: FMP is excluded from a_share — their free/starter tier does not support
# Chinese A-share financial statements (returns 403). FMP only for HK/US.
# QVeris iFinD is added as tertiary A-share fallback for missing fields.
# Tushare added as secondary priority for A-share (enterprise-grade data quality).
# Sina realtime added for price-only fallback (fast, free, no financials).
_SOURCE_PRIORITY: dict[MarketType, list[str]] = {
    "a_share": ["akshare", "tushare", "baostock", "sina_realtime", "qveris"],
    "hk":      ["akshare", "yfinance", "sina_realtime", "fmp"],
    "us":      ["yfinance", "fmp"],
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
        include_quarterly: bool = True,
    ) -> dict[str, int]:
        """Fetch all 3 financial statements + metrics. Returns row counts per type.

        Args:
            ticker: Stock ticker
            market: Market type
            limit: Number of records per period type
            period_type: Primary period type ("annual" or "quarterly")
            include_quarterly: If True, also fetch quarterly data for fresher data
        """
        counts: dict[str, int] = {}
        all_stmts = []
        all_sheets = []
        all_flows = []

        # Fetch annual data
        stmts, _ = self._fetch_with_fallback(
            market, "get_income_statements", ticker,
            period_type="annual", limit=limit,
        )
        all_stmts.extend(stmts)

        sheets, _ = self._fetch_with_fallback(
            market, "get_balance_sheets", ticker,
            period_type="annual", limit=limit,
        )
        all_sheets.extend(sheets)

        flows, _ = self._fetch_with_fallback(
            market, "get_cash_flows", ticker,
            period_type="annual", limit=limit,
        )
        all_flows.extend(flows)

        # Also fetch quarterly data for fresher data (A-share only)
        if include_quarterly and market == "a_share":
            q_stmts, _ = self._fetch_with_fallback(
                market, "get_income_statements", ticker,
                period_type="quarterly", limit=4,  # Last 4 quarters
            )
            all_stmts.extend(q_stmts)

            q_sheets, _ = self._fetch_with_fallback(
                market, "get_balance_sheets", ticker,
                period_type="quarterly", limit=4,
            )
            all_sheets.extend(q_sheets)

            q_flows, _ = self._fetch_with_fallback(
                market, "get_cash_flows", ticker,
                period_type="quarterly", limit=4,
            )
            all_flows.extend(q_flows)

        counts["income"] = upsert_income_statements(all_stmts)
        counts["balance"] = upsert_balance_sheets(all_sheets)
        counts["cashflow"] = upsert_cash_flows(all_flows)

        # Key metrics (always includes quarterly from EM API)
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
        Fetch company basic information.

        Priority:
        1. QVeris iFinD (paid source, comprehensive data)
        2. Local fallback mapping (for common A-share stocks when QVeris exhausted)

        Returns dict with company_name, main_business, industry, etc. or None.
        Only available for A-share tickers.
        """
        if market != "a_share":
            return None

        # Try QVeris first
        basics = _qveris_company_basics(ticker)
        if basics:
            logger.info("[Fetcher] %s company basics from QVeris: %s", ticker, basics.get("company_name"))
            return basics

        # Fallback to local mapping
        fallback = _COMPANY_INFO_FALLBACK.get(ticker)
        if fallback:
            logger.info("[Fetcher] %s company basics from fallback: %s", ticker, fallback.get("name"))
            return {
                "company_name": fallback.get("name"),
                "main_business": fallback.get("main_business"),
                "industry": fallback.get("industry"),
                "concepts": fallback.get("industry"),  # Use industry as concept fallback
                "is_financial": fallback.get("is_financial", False),
            }

        # Fallback to AKShare stock_individual_info_em (free, always available)
        try:
            import akshare as ak
            code = ticker.split(".")[0]
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                # Convert DataFrame to dict {item: value}
                info_dict = dict(zip(df["item"], df["value"]))
                company_name = info_dict.get("股票简称") or info_dict.get("公司名称")
                industry = info_dict.get("行业") or info_dict.get("所属行业")
                main_business = info_dict.get("经营范围", "")[:100] if info_dict.get("经营范围") else None

                if company_name:
                    logger.info("[Fetcher] %s company basics from AKShare: %s", ticker, company_name)
                    return {
                        "company_name": company_name,
                        "main_business": main_business,
                        "industry": industry,
                        "concepts": industry,
                        "is_financial": industry and any(k in industry for k in ["银行", "保险", "证券", "金融"]),
                    }
        except Exception as e:
            logger.debug("[Fetcher] AKShare stock_individual_info_em failed for %s: %s", ticker, e)

        logger.warning("[Fetcher] %s company basics not available (all sources exhausted)", ticker)
        return None

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
