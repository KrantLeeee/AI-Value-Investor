"""Sentiment Agent — LLM-powered news sentiment analysis.

Uses DeepSeek (low-cost model) to classify recent news headlines
and compute an overall sentiment score for the ticker.

If no news data is available in DB, returns neutral signal with low confidence.
If LLM is unavailable, returns neutral with a note.

Integrates structured profit warning data from AKShare (业绩预告) for
forward-looking sentiment analysis.

P1-1: Tavily web search integration for financial news.
P1-2: Rule-based sentiment scoring for stability.
"""

import json
from datetime import date

from src.data.database import get_manual_docs, insert_agent_signal
from src.data.models import AgentSignal, ProfitWarning
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "sentiment"
NEWS_LOOKBACK_DAYS = 30

# P1-2: Rule-based sentiment keywords
POSITIVE_KEYWORDS = [
    "超预期", "增长", "上涨", "突破", "新高", "利好", "盈利", "回暖",
    "获得", "签署", "合作", "扩张", "提升", "强劲", "乐观", "创新高",
    "业绩预增", "大单", "中标", "收购", "分红", "回购",
]
NEGATIVE_KEYWORDS = [
    "下滑", "下降", "亏损", "暴跌", "利空", "调查", "处罚", "风险",
    "减持", "质押", "违规", "退市", "诉讼", "裁员", "停产", "业绩预减",
    "业绩首亏", "续亏", "警示", "ST", "暂停", "冻结",
]


def calculate_rule_based_sentiment(news_items: list) -> float:
    """
    P1-2: Calculate sentiment score using keyword matching.

    Returns:
        Score from 0.0 (very negative) to 1.0 (very positive)
        0.5 is neutral
    """
    if not news_items:
        return 0.5

    total_positive = 0
    total_negative = 0

    for item in news_items:
        # Handle both dict format (new) and string format (legacy/mocked)
        if isinstance(item, dict):
            text = f"{item.get('title', '')} {item.get('content', '')}".lower()
        else:
            text = str(item).lower()

        for keyword in POSITIVE_KEYWORDS:
            if keyword in text:
                total_positive += 1

        for keyword in NEGATIVE_KEYWORDS:
            if keyword in text:
                total_negative += 1

    # Normalize to 0-1 scale
    total = total_positive + total_negative
    if total == 0:
        return 0.5

    # positive_ratio: 0 (all negative) to 1 (all positive)
    positive_ratio = total_positive / total

    # Smooth towards 0.5 for low signal
    confidence = min(total / 10, 1.0)  # Confidence grows with more signals
    return 0.5 + (positive_ratio - 0.5) * confidence


def _get_news_from_db(ticker: str) -> list[dict]:
    """
    Retrieve recent news headlines from:
    1. manual_docs table (extracted_text from uploaded docs)
    2. In future: a `news` table (to be added when news fetching is implemented)
    Returns a list of {title, content, source} dicts.
    """
    docs = get_manual_docs(ticker)
    results = []
    for doc in docs:
        text = doc.get("extracted_text", "") or ""
        # Use first 200 chars per doc as headline proxy
        if text.strip():
            results.append({
                "title": text.strip()[:100],
                "content": text.strip()[:200],
                "source": "manual_doc",
            })
    return results


def _get_news_from_akshare(ticker: str, market: str) -> list[dict]:
    """Try to fetch fresh news headlines from AKShare (A-share only)."""
    if market != "a_share":
        return []
    try:
        import akshare as ak
        code = ticker.split(".")[0]
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            results = []
            for _, row in df.head(20).iterrows():
                results.append({
                    "title": str(row.get("新闻标题", "")),
                    "content": str(row.get("新闻内容", ""))[:200] if row.get("新闻内容") else "",
                    "source": "akshare",
                })
            return results
    except Exception as e:
        logger.debug("[Sentiment] AKShare news fetch failed for %s: %s", ticker, e)
    return []


def _get_news_from_tavily(query: str) -> list[dict]:
    """
    P1-1: Fetch financial news using Tavily API.

    Returns list of {title, content, url, source} dicts.
    """
    try:
        from src.data.tavily_source import TavilySource
        source = TavilySource()
        results = source.search_news(query, max_results=10, time_range="month")
        logger.info("[Sentiment] Tavily returned %d results for '%s'", len(results), query[:30])
        return results
    except ValueError as e:
        # TAVILY_API_KEY not set
        logger.debug("[Sentiment] Tavily not available: %s", e)
    except Exception as e:
        logger.warning("[Sentiment] Tavily search failed: %s", e)
    return []


def _get_profit_warnings(ticker: str, market: str) -> list[ProfitWarning]:
    """Fetch structured profit warning data from AKShare."""
    if market != "a_share":
        return []
    try:
        from src.data.akshare_source import AKShareSource
        source = AKShareSource()
        return source.get_profit_warnings(ticker, market, limit=2)
    except Exception as e:
        logger.debug("[Sentiment] Profit warning fetch failed for %s: %s", ticker, e)
    return []


def _extract_profit_warning_info(warnings: list[ProfitWarning]) -> tuple[str | None, str | None]:
    """
    Extract structured profit warning type and details from ProfitWarning data.

    Returns:
        (warning_type, details): e.g., ("预增", "预计净利增长50%-80%")
    """
    if not warnings:
        return None, None

    # Get the most recent warning
    latest = warnings[0]
    warning_type = latest.warning_type

    # Build details string
    details_parts = []

    # Add change percentage range
    if latest.change_pct_min is not None and latest.change_pct_max is not None:
        if latest.change_pct_min == latest.change_pct_max:
            details_parts.append(f"预计增幅{latest.change_pct_min:.0f}%")
        else:
            details_parts.append(f"预计增幅{latest.change_pct_min:.0f}%~{latest.change_pct_max:.0f}%")
    elif latest.change_pct_max is not None:
        details_parts.append(f"预计增幅{latest.change_pct_max:.0f}%")
    elif latest.change_pct_min is not None:
        details_parts.append(f"预计增幅{latest.change_pct_min:.0f}%")

    # Add profit range (in 亿)
    if latest.profit_min is not None and latest.profit_max is not None:
        profit_min_yi = latest.profit_min / 1e8
        profit_max_yi = latest.profit_max / 1e8
        if abs(profit_min_yi - profit_max_yi) < 0.01:
            details_parts.append(f"预计净利润{profit_min_yi:.2f}亿")
        else:
            details_parts.append(f"预计净利润{profit_min_yi:.2f}~{profit_max_yi:.2f}亿")

    # Add report period
    details_parts.append(f"报告期:{latest.report_date}")

    details = "，".join(details_parts) if details_parts else None

    return warning_type, details


def _get_company_name(ticker: str) -> str | None:
    """
    Get company name from watchlist or company info for better news search.

    Returns:
        Company name string or None if not found
    """
    try:
        from src.data.fetcher import _COMPANY_INFO_FALLBACK
        info = _COMPANY_INFO_FALLBACK.get(ticker, {})
        name = info.get("name") or info.get("company_name")
        if name:
            return name
    except ImportError:
        pass

    # Try watchlist
    try:
        from src.utils.config import load_watchlist
        watchlist = load_watchlist()
        for item in watchlist:
            if item.get("ticker") == ticker:
                return item.get("name")
    except Exception:
        pass

    return None


def _validate_news_relevance(news_items: list[dict], ticker: str, company_name: str | None) -> tuple[list[dict], int]:
    """
    BUG-B FIX: Validate that fetched news actually relates to the target company.

    Args:
        news_items: List of news items from search
        ticker: Stock ticker (e.g., "603881.SH")
        company_name: Company name (e.g., "数据港")

    Returns:
        Tuple of (relevant_news_items, irrelevant_count)
    """
    if not news_items:
        return [], 0

    code = ticker.split(".")[0]
    relevant = []
    irrelevant_count = 0

    # Build relevance keywords
    keywords = [code]
    if company_name:
        keywords.append(company_name)
        # Add partial matches for company names (e.g., "数据港" from "上海数据港")
        if len(company_name) >= 4:
            keywords.append(company_name[:4])

    for item in news_items:
        text = f"{item.get('title', '')} {item.get('content', '')}".lower()

        # Check if any keyword appears in the news
        is_relevant = any(kw.lower() in text for kw in keywords)

        if is_relevant:
            relevant.append(item)
        else:
            irrelevant_count += 1
            logger.debug(
                "[Sentiment] Filtered irrelevant news: %s",
                item.get("title", "")[:50]
            )

    return relevant, irrelevant_count


def run(
    ticker: str,
    market: str,
    use_llm: bool = True,
    use_tavily: bool = True,
) -> AgentSignal:
    """
    Run the Sentiment Agent.
    Returns AgentSignal and persists to DB.

    Integrates:
    1. News headlines from Tavily (P1-1), AKShare or manual docs
    2. Structured profit warning data from AKShare (业绩预告)
    3. Rule-based sentiment scoring (P1-2)
    """
    # BUG-B FIX: Get company name for better search and relevance validation
    company_name = _get_company_name(ticker)

    # P1-1: Try Tavily first for financial news (better quality)
    news_items = []
    news_source = "none"
    irrelevant_news_count = 0

    if use_tavily:
        # BUG-B FIX: Include company name in search query for better precision
        code = ticker.split('.')[0]
        if company_name:
            query = f"{company_name} {code} 财报 业绩"
        else:
            query = f"{code} 财报 业绩 股票"

        raw_news = _get_news_from_tavily(query)

        # BUG-B FIX: Validate news relevance - filter out sector-level news
        if raw_news:
            news_items, irrelevant_news_count = _validate_news_relevance(
                raw_news, ticker, company_name
            )
            if news_items:
                news_source = "tavily"
            elif irrelevant_news_count > 0:
                logger.warning(
                    "[Sentiment] %s: All %d news items filtered as irrelevant (likely sector news)",
                    ticker, irrelevant_news_count
                )

    # Fallback to AKShare
    if not news_items:
        news_items = _get_news_from_akshare(ticker, market)
        if news_items:
            news_source = "akshare"

    # Last resort: manual docs
    if not news_items:
        news_items = _get_news_from_db(ticker)
        if news_items:
            news_source = "manual_docs"

    # Extract headlines for LLM (backwards compatibility)
    # Handle both dict format (new) and string format (legacy/mocked)
    news_headlines = []
    for item in news_items:
        if isinstance(item, dict):
            if item.get("title"):
                news_headlines.append(item["title"])
        elif isinstance(item, str) and item:
            news_headlines.append(item)

    # Fetch structured profit warning data
    profit_warnings = _get_profit_warnings(ticker, market)
    profit_warning_type, profit_warning_details = _extract_profit_warning_info(profit_warnings)

    # P1-2: Calculate rule-based sentiment score
    rule_based_score = calculate_rule_based_sentiment(news_items)

    # BUG-B FIX: Enhanced data availability status
    # "available" = relevant news found
    # "irrelevant" = news found but not about this specific company
    # "insufficient" = no news found at all
    if news_items:
        data_status = "available"
    elif irrelevant_news_count > 0:
        data_status = "irrelevant"  # Had news but none were about the target company
    else:
        data_status = "insufficient"

    metrics_snapshot: dict = {
        "news_count": len(news_headlines),
        "news_days": NEWS_LOOKBACK_DAYS,
        "news_source": news_source,
        "data_status": data_status,
        "profit_warning": profit_warning_type,
        "profit_warning_details": profit_warning_details,
        "rule_based_score": round(rule_based_score, 3),
        "company_name": company_name,  # BUG-B: Track company name used for search
        "irrelevant_news_filtered": irrelevant_news_count,  # BUG-B: Track filtered news
    }

    logger.info(
        "[Sentiment] %s: profit_warning=%s, rule_score=%.2f, source=%s, status=%s",
        ticker, profit_warning_type, rule_based_score, news_source, data_status
    )

    # Handle case: no relevant news available
    if not news_items:
        # BUG-B FIX: Distinguish between "no news" and "news exists but irrelevant"
        if data_status == "irrelevant":
            reasoning = (
                f"⚠️ 情绪数据不可用：搜索到 {irrelevant_news_count} 条新闻，"
                f"但均为行业/板块资讯，未发现与{company_name or ticker}直接相关的个股新闻。"
                "情绪分析结果不可靠，建议通过其他渠道验证。"
            )
            confidence = 0.10  # Even lower confidence for irrelevant data
        else:
            reasoning = "⚠️ 情绪数据不可用：近期新闻数据不足，无法进行有效的情绪分析。建议关注后续公告和新闻动态。"
            confidence = 0.15

        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="neutral",
            confidence=confidence,
            reasoning=reasoning,
            metrics=metrics_snapshot,
        )
        insert_agent_signal(agent_signal)
        logger.info("[Sentiment] %s: %s, returning neutral with low confidence", ticker, data_status)
        return agent_signal

    # P1-2: Use rule-based score as baseline (0.0-1.0 → -1.0 to 1.0 for compatibility)
    rule_sentiment_score = (rule_based_score - 0.5) * 2  # Convert to -1 to 1 range

    signal, confidence, reasoning = "neutral", 0.40, "LLM 分析暂不可用"

    if use_llm:
        try:
            from src.llm.router import call_llm, LLMError
            from src.llm.prompts import SENTIMENT_SYSTEM_PROMPT, SENTIMENT_USER_TEMPLATE

            # Format headlines as numbered list
            news_list_text = "\n".join(
                f"{i+1}. {h}" for i, h in enumerate(news_headlines[:20])
            )

            user_msg = SENTIMENT_USER_TEMPLATE.format(
                ticker=ticker,
                analysis_date=str(date.today()),
                news_count=len(news_headlines),
                news_days=NEWS_LOOKBACK_DAYS,
                news_list=news_list_text,
            )

            llm_text = call_llm("news_sentiment", SENTIMENT_SYSTEM_PROMPT, user_msg)

            try:
                parsed = json.loads(llm_text)
                signal     = parsed.get("signal", "neutral").lower()
                confidence = float(parsed.get("confidence", 0.5))
                reasoning  = parsed.get("reasoning", llm_text)
                sentiment_score = float(parsed.get("sentiment_score", 0.0))

                # BUG-FIX: Validate signal against sentiment_score threshold
                # sentiment_score > 0.3 → bullish allowed
                # sentiment_score < -0.3 → bearish allowed
                # Otherwise → force neutral (prevent false bullish/bearish on weak scores)
                BULLISH_THRESHOLD = 0.3
                BEARISH_THRESHOLD = -0.3

                if signal == "bullish" and sentiment_score < BULLISH_THRESHOLD:
                    logger.warning(
                        "[Sentiment] %s: signal=%s but score=%.2f < %.2f threshold, forcing neutral",
                        ticker, signal, sentiment_score, BULLISH_THRESHOLD
                    )
                    signal = "neutral"
                    reasoning += f" (原始信号bullish，但情绪得分{sentiment_score:.2f}低于阈值{BULLISH_THRESHOLD}，降级为neutral)"
                elif signal == "bearish" and sentiment_score > BEARISH_THRESHOLD:
                    logger.warning(
                        "[Sentiment] %s: signal=%s but score=%.2f > %.2f threshold, forcing neutral",
                        ticker, signal, sentiment_score, BEARISH_THRESHOLD
                    )
                    signal = "neutral"
                    reasoning += f" (原始信号bearish，但情绪得分{sentiment_score:.2f}高于阈值{BEARISH_THRESHOLD}，降级为neutral)"

                metrics_snapshot.update({
                    "sentiment_score":  sentiment_score,
                    "positive_count":   parsed.get("positive_count", 0),
                    "negative_count":   parsed.get("negative_count", 0),
                    "neutral_count":    parsed.get("neutral_count", 0),
                    "key_events":       parsed.get("key_events", []),
                    "risks":            parsed.get("risks", []),
                })
            except Exception:
                # Parse signal from prose - but force neutral since we can't reliably
                # determine sentiment_score from prose alone (BUG-FIX: prevent false bullish)
                text_lower = llm_text.lower()
                has_positive = any(w in text_lower for w in ["positive", "看多", "正面", "bullish"])
                has_negative = any(w in text_lower for w in ["negative", "看空", "负面", "bearish"])

                # Without proper sentiment_score, we cannot meet threshold requirements
                # Force neutral signal with weak confidence
                signal = "neutral"
                confidence = 0.35
                reasoning = llm_text + " (JSON解析失败，无法获取可靠情绪得分，信号降级为neutral)"

                # Record detected keywords for debugging but don't use for signal
                metrics_snapshot["sentiment_score"] = 0.0
                metrics_snapshot["prose_detected_positive"] = has_positive
                metrics_snapshot["prose_detected_negative"] = has_negative

        except Exception as e:
            logger.warning("[Sentiment] LLM call failed: %s", e)
            # P1-2: Fall back to rule-based scoring when LLM fails
            if rule_sentiment_score > 0.3:
                signal, confidence = "bullish", 0.35
                reasoning = f"LLM不可用，基于关键词规则分析（正面偏向）。获取到 {len(news_headlines)} 条新闻。"
            elif rule_sentiment_score < -0.3:
                signal, confidence = "bearish", 0.35
                reasoning = f"LLM不可用，基于关键词规则分析（负面偏向）。获取到 {len(news_headlines)} 条新闻。"
            else:
                signal, confidence = "neutral", 0.30
                reasoning = f"LLM不可用，基于关键词规则分析（中性）。获取到 {len(news_headlines)} 条新闻。"

    agent_signal = AgentSignal(
        ticker=ticker,
        agent_name=AGENT_NAME,
        signal=signal,
        confidence=round(confidence, 3),
        reasoning=reasoning,
        metrics=metrics_snapshot,
    )
    insert_agent_signal(agent_signal)
    logger.info("[Sentiment] %s: signal=%s news=%d", ticker, signal, len(news_headlines))
    return agent_signal
