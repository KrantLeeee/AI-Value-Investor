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
from src.data.database import (
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
from src.data.qveris_source import QVerisSource
from src.data.qveris_source import fetch_company_basics as _qveris_company_basics
from src.data.sina_source import SinaRealtimeSource
from src.data.tushare_source import TushareSource
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
    "300896.SZ": {"name": "爱美客", "industry": "医美/化妆品", "main_business": "玻尿酸等医美产品研发与销售"},
    # Auto
    "002594.SZ": {"name": "比亚迪", "industry": "新能源汽车", "main_business": "新能源汽车及电池生产"},
    "600104.SH": {"name": "上汽集团", "industry": "汽车", "main_business": "汽车研发生产销售"},
    # Utilities
    "600900.SH": {"name": "长江电力", "industry": "电力/公用事业", "main_business": "水力发电及电力销售"},
    "601985.SH": {"name": "中国核电", "industry": "电力/公用事业", "main_business": "核电发电及销售"},
    # Advertising/Media
    "002027.SZ": {"name": "分众传媒", "industry": "广告/传媒", "main_business": "楼宇电梯广告媒体运营"},
    # Retail
    "601933.SH": {"name": "永辉超市", "industry": "零售/超市", "main_business": "连锁超市零售业务"},
    "601888.SH": {"name": "中国中免", "industry": "零售/免税", "main_business": "免税商品零售"},
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
    """Call fn with retries and exponential backoff.

    Certain exceptions are NOT retried because they will always fail:
    - NotImplementedError: Method not supported by source
    - ImportError: Required package not installed
    - AttributeError: Method doesn't exist on source
    """
    # Exceptions that should NOT be retried (permanent failures)
    NO_RETRY_EXCEPTIONS = (NotImplementedError, ImportError, AttributeError)

    for attempt in range(MAX_RETRIES):
        try:
            result = fn(*args, **kwargs)
            return result
        except NO_RETRY_EXCEPTIONS as e:
            # Don't retry these - they will always fail
            logger.debug("Permanent failure (no retry): %s", e)
            raise
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

        Priority (optimized for reliability and cost):
        1. Tavily Web Search (fast, accurate, uses web + LLM)
        2. Local fallback mapping (instant, curated list)
        3. AKShare API (free, but network-dependent)
        4. LLM Lookup (reliable, uses GPT-4o-mini or DeepSeek)
        5. QVeris iFinD (paid source, last resort due to quota limits)

        Returns dict with company_name, main_business, industry, etc. or None.
        Only available for A-share tickers.
        """
        if market != "a_share":
            return None

        import os

        # ── Priority 1: Tavily Web Search + LLM Parse (fast and accurate) ────────
        tavily_key = os.getenv("TAVILY_API_KEY")
        if tavily_key:
            try:
                parsed = self._tavily_search_company_info(ticker, tavily_key)
                if parsed and parsed.get("company_name"):
                    logger.info("[Fetcher] %s company basics from Tavily: %s",
                               ticker, parsed.get("company_name"))
                    return parsed
            except Exception as e:
                logger.debug("[Fetcher] Tavily web search failed for %s: %s", ticker, e)

        # ── Priority 2: Local fallback mapping (instant, curated) ──────────────
        fallback = _COMPANY_INFO_FALLBACK.get(ticker)
        if fallback:
            logger.info("[Fetcher] %s company basics from fallback: %s", ticker, fallback.get("name"))
            return {
                "company_name": fallback.get("name"),
                "main_business": fallback.get("main_business"),
                "industry": fallback.get("industry"),
                "concepts": fallback.get("industry"),
                "is_financial": fallback.get("is_financial", False),
                "source": "local_fallback",
            }

        # ── Priority 3: AKShare API (free, network-dependent) ──────────────────
        try:
            import akshare as ak
            code = ticker.split(".")[0]
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                info_dict = dict(zip(df["item"], df["value"]))
                company_name = info_dict.get("股票简称") or info_dict.get("公司名称")
                industry = info_dict.get("行业") or info_dict.get("所属行业")
                main_business = None

                # stock_individual_info_em doesn't have main_business field,
                # use stock_zyjs_ths (同花顺主营介绍) to get it
                try:
                    zyjs_df = ak.stock_zyjs_ths(symbol=code)
                    if zyjs_df is not None and not zyjs_df.empty:
                        main_business = zyjs_df["主营业务"].iloc[0] if "主营业务" in zyjs_df.columns else None
                        if not main_business and "经营范围" in zyjs_df.columns:
                            # Fallback to 经营范围 if 主营业务 is empty
                            main_business = str(zyjs_df["经营范围"].iloc[0])[:100]
                except Exception as e:
                    logger.debug("[Fetcher] AKShare stock_zyjs_ths failed for %s: %s", ticker, e)

                if company_name:
                    logger.info("[Fetcher] %s company basics from AKShare: %s", ticker, company_name)
                    return {
                        "company_name": company_name,
                        "main_business": main_business,
                        "industry": industry,
                        "concepts": industry,
                        "is_financial": industry and any(k in industry for k in ["银行", "保险", "证券", "金融"]),
                        "source": "akshare",
                    }
        except Exception as e:
            logger.debug("[Fetcher] AKShare stock_individual_info_em failed for %s: %s", ticker, e)

        # ── Priority 4: LLM Lookup (reliable, uses GPT-4o-mini or DeepSeek) ────
        openai_key = os.getenv("OPENAI_API_KEY")
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if openai_key or deepseek_key:
            try:
                parsed = self._llm_lookup_company_info(ticker)
                if parsed and parsed.get("company_name"):
                    logger.info("[Fetcher] %s company basics from LLM lookup: %s",
                               ticker, parsed.get("company_name"))
                    return parsed
            except Exception as e:
                logger.debug("[Fetcher] LLM lookup failed for %s: %s", ticker, e)

        # ── Priority 5: QVeris iFinD (paid, last resort due to quota) ──────────
        try:
            basics = _qveris_company_basics(ticker)
            if basics and basics.get("company_name"):
                basics["source"] = "qveris"
                logger.info("[Fetcher] %s company basics from QVeris: %s", ticker, basics.get("company_name"))
                return basics
        except Exception as e:
            logger.debug("[Fetcher] QVeris failed for %s: %s", ticker, e)

        logger.warning("[Fetcher] %s company basics not available (all sources exhausted)", ticker)
        return None

    def _tavily_search_company_info(self, ticker: str, api_key: str) -> dict | None:
        """
        Search company info using Tavily API directly with httpx.

        Bypasses the Tavily SDK which has SSL issues in some network environments.
        Uses Tavily's built-in LLM answer feature for structured extraction.
        """

        import httpx

        code = ticker.split(".")[0]
        query = f"{code} 股票 公司简称 主营业务 所属行业"

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 5,
                        "include_answer": True,
                    }
                )
                response.raise_for_status()
                data = response.json()

                # Get Tavily's LLM-generated answer
                answer = data.get("answer", "")
                results = data.get("results", [])

                logger.debug("[Fetcher] Tavily search for %s: answer=%s, results=%d",
                            ticker, answer[:100] if answer else "None", len(results))

                if answer:
                    # Use LLM to parse the Tavily answer into structured data
                    parsed = self._llm_parse_company_info(ticker, answer)
                    if parsed and parsed.get("company_name"):
                        parsed["source"] = "tavily"
                        return parsed

                # Fallback: try to extract from search results titles/content
                if results:
                    combined_text = " ".join([
                        r.get("title", "") + " " + r.get("content", "")[:200]
                        for r in results[:3]
                    ])
                    parsed = self._llm_parse_company_info(ticker, combined_text)
                    if parsed and parsed.get("company_name"):
                        parsed["source"] = "tavily"
                        return parsed

        except httpx.HTTPStatusError as e:
            logger.debug("[Fetcher] Tavily API error for %s: %s", ticker, e)
        except Exception as e:
            logger.debug("[Fetcher] Tavily search failed for %s: %s", ticker, e)

        return None

    def _llm_parse_company_info(self, ticker: str, text: str) -> dict | None:
        """
        Use LLM to parse company info from unstructured text.

        This is more reliable than regex-based parsing for Chinese text.
        """
        import json
        import os

        from src.utils.network import create_openai_client

        # Try OpenAI first, then DeepSeek
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = None
        model = "gpt-4o-mini"

        if not api_key:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            base_url = "https://api.deepseek.com/v1"
            model = "deepseek-chat"

        if not api_key:
            return None

        client = create_openai_client(api_key=api_key, base_url=base_url)

        prompt = f"""从以下文本中提取股票 {ticker} 的公司信息。

文本内容：
{text[:1000]}

请以JSON格式返回（不要添加其他文字）：
{{
    "company_name": "公司简称（如：碧水源、贵州茅台，2-4个字）",
    "industry": "所属行业（如：环保/水处理、白酒、电力设备）",
    "main_business": "主营业务简述（20字以内）",
    "is_financial": false
}}

注意：company_name 应该是股票简称，不是公司全称。如果无法确定某个字段，返回null。"""

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个准确的股票信息提取助手。只返回JSON，不要其他内容。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0,
            )

            content = response.choices[0].message.content or ""
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            data = json.loads(content)

            if data.get("company_name"):
                return {
                    "company_name": data.get("company_name"),
                    "main_business": data.get("main_business"),
                    "industry": data.get("industry"),
                    "concepts": data.get("industry"),
                    "is_financial": data.get("is_financial", False),
                }
        except Exception as e:
            logger.debug("[Fetcher] LLM parse failed for %s: %s", ticker, e)

        return None

    def _parse_company_info_from_text(self, ticker: str, text: str) -> dict | None:
        """Parse company info from web search text using simple heuristics.

        Note: This is a fallback method. Prefer _llm_parse_company_info for better accuracy.
        """
        if not text:
            return None

        # Common Chinese stock name patterns
        import re

        # Try to find company name (stock short name)
        company_name = None
        industry = None
        main_business = None

        # Pattern: "XXX（股票代码）" or "XXX（代码：XXX）"
        name_patterns = [
            r'([^\s,，。、]+)(?:股份|集团|公司)',  # Match company-like names
            r'简称[：:]\s*([^\s,，。、]+)',         # "简称：XXX"
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text)
            if match:
                company_name = match.group(1)
                break

        # Industry detection
        industry_keywords = {
            "医美": "医美/化妆品", "玻尿酸": "医美/化妆品", "化妆品": "医美/化妆品",
            "广告": "广告/传媒", "传媒": "广告/传媒", "媒体": "广告/传媒",
            "超市": "零售/超市", "零售": "零售/超市", "连锁": "零售/超市",
            "银行": "银行", "保险": "保险", "证券": "证券", "金融": "金融",
            "白酒": "白酒", "酒类": "白酒", "茅台": "白酒",
            "汽车": "汽车", "新能源": "新能源汽车",
            "医药": "医药", "医疗": "医疗器械",
            "房地产": "房地产", "地产": "房地产",
            "电力": "电力/公用事业", "发电": "电力/公用事业",
            "环保": "环保/水处理", "水处理": "环保/水处理", "污水": "环保/水处理",
        }

        for keyword, ind in industry_keywords.items():
            if keyword in text:
                industry = ind
                break

        if company_name or industry:
            return {
                "company_name": company_name,
                "main_business": main_business,
                "industry": industry,
                "concepts": industry,
                "is_financial": industry and any(k in (industry or "") for k in ["银行", "保险", "证券", "金融"]),
                "source": "web_search",
            }

        return None

    def _llm_lookup_company_info(self, ticker: str) -> dict | None:
        """
        Use LLM to look up company information based on stock code.

        Includes cross-validation with web search to catch LLM knowledge errors.
        """
        import json
        import os

        from src.utils.network import create_openai_client

        # Try OpenAI first, then DeepSeek
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = None
        model = "gpt-4o-mini"

        if not api_key:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            base_url = "https://api.deepseek.com/v1"
            model = "deepseek-chat"

        if not api_key:
            return None

        # Use network-aware client (bypasses proxy for LLM APIs)
        client = create_openai_client(api_key=api_key, base_url=base_url)

        prompt = f"""你是一个中国A股股票信息助手。请根据股票代码 {ticker} 提供以下信息：

请以JSON格式返回（不要添加其他文字）：
{{
    "company_name": "公司简称（如：贵州茅台）",
    "industry": "所属行业（如：白酒、医美/化妆品、广告/传媒）",
    "main_business": "主营业务简述（20字以内）",
    "is_financial": false
}}

如果无法确定某个字段，返回null。请确保信息准确。"""

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个准确的股票信息查询助手。只返回JSON，不要其他内容。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0,
            )

            content = response.choices[0].message.content or ""
            # Strip markdown code block if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            data = json.loads(content)
            llm_company_name = data.get("company_name")

            if llm_company_name:
                # ── Cross-validation: verify LLM result with web search ──────────
                tavily_key = os.getenv("TAVILY_API_KEY")
                if tavily_key:
                    try:
                        validated = self._validate_company_name_with_search(
                            ticker, llm_company_name, tavily_key
                        )
                        if not validated:
                            logger.warning(
                                "[Fetcher] LLM lookup for %s returned '%s' but failed validation",
                                ticker, llm_company_name
                            )
                            return None
                    except Exception as e:
                        logger.debug("[Fetcher] Validation failed for %s: %s", ticker, e)
                        # Continue without validation if search fails

                return {
                    "company_name": llm_company_name,
                    "main_business": data.get("main_business"),
                    "industry": data.get("industry"),
                    "concepts": data.get("industry"),
                    "is_financial": data.get("is_financial", False),
                    "source": "llm_lookup",
                }
        except Exception as e:
            logger.debug("[Fetcher] LLM JSON parse failed for %s: %s", ticker, e)

        return None

    def _validate_company_name_with_search(
        self, ticker: str, company_name: str, tavily_key: str
    ) -> bool:
        """
        Validate company name against web search results.

        Returns True if the company name appears in search results for the ticker.
        This helps catch LLM hallucinations about stock codes.
        """
        import httpx

        code = ticker.split(".")[0]
        query = f"{code} 股票 公司简称"

        try:
            with httpx.Client(timeout=15) as client:
                response = client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 3,
                        "include_answer": True,
                    }
                )
                response.raise_for_status()
                data = response.json()

                # Check if company name appears in answer or results
                answer = data.get("answer", "")
                results = data.get("results", [])

                # Combine all searchable text
                search_text = answer
                for r in results:
                    search_text += " " + r.get("title", "") + " " + r.get("content", "")

                # Check if company name is mentioned
                if company_name in search_text:
                    logger.debug("[Fetcher] Validated %s = %s via web search", ticker, company_name)
                    return True

                # Also check for partial matches (first 2 characters)
                if len(company_name) >= 2 and company_name[:2] in search_text:
                    logger.debug("[Fetcher] Partially validated %s = %s via web search", ticker, company_name)
                    return True

                logger.debug(
                    "[Fetcher] Company name '%s' not found in search results for %s",
                    company_name, ticker
                )
                return False

        except Exception as e:
            logger.debug("[Fetcher] Validation search failed for %s: %s", ticker, e)
            # Return True to not block on validation failures
            return True

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
