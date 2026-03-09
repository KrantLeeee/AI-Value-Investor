"""Sentiment Agent — LLM-powered news sentiment analysis.

Uses DeepSeek (low-cost model) to classify recent news headlines
and compute an overall sentiment score for the ticker.

If no news data is available in DB, returns neutral signal with low confidence.
If LLM is unavailable, returns neutral with a note.

Integrates structured profit warning data from AKShare (业绩预告) for
forward-looking sentiment analysis.
"""

import json
from datetime import date

from src.data.database import get_manual_docs, insert_agent_signal
from src.data.models import AgentSignal, ProfitWarning
from src.utils.logger import get_logger

logger = get_logger(__name__)

AGENT_NAME = "sentiment"
NEWS_LOOKBACK_DAYS = 30


def _get_news_from_db(ticker: str) -> list[str]:
    """
    Retrieve recent news headlines from:
    1. manual_docs table (extracted_text from uploaded docs)
    2. In future: a `news` table (to be added when news fetching is implemented)
    Returns a list of headline/text strings.
    """
    docs = get_manual_docs(ticker)
    headlines = []
    for doc in docs:
        text = doc.get("extracted_text", "") or ""
        # Use first 200 chars per doc as headline proxy
        if text.strip():
            headlines.append(text.strip()[:200])
    return headlines


def _get_news_from_akshare(ticker: str, market: str) -> list[str]:
    """Try to fetch fresh news headlines from AKShare (A-share only)."""
    if market != "a_share":
        return []
    try:
        import akshare as ak
        code = ticker.split(".")[0]
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            headlines = df["新闻标题"].head(20).tolist()
            return [str(h) for h in headlines if h]
    except Exception as e:
        logger.debug("[Sentiment] AKShare news fetch failed for %s: %s", ticker, e)
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


def run(
    ticker: str,
    market: str,
    use_llm: bool = True,
) -> AgentSignal:
    """
    Run the Sentiment Agent.
    Returns AgentSignal and persists to DB.

    Integrates:
    1. News headlines from AKShare or manual docs
    2. Structured profit warning data from AKShare (业绩预告)
    """
    # Gather news from all available sources
    news_headlines = _get_news_from_akshare(ticker, market)
    if not news_headlines:
        news_headlines = _get_news_from_db(ticker)

    # Fetch structured profit warning data
    profit_warnings = _get_profit_warnings(ticker, market)
    profit_warning_type, profit_warning_details = _extract_profit_warning_info(profit_warnings)

    metrics_snapshot: dict = {
        "news_count": len(news_headlines),
        "news_days": NEWS_LOOKBACK_DAYS,
        "profit_warning": profit_warning_type,
        "profit_warning_details": profit_warning_details,
    }

    logger.info(
        "[Sentiment] %s: profit_warning=%s, details=%s",
        ticker, profit_warning_type, profit_warning_details
    )

    # Handle case: no news available
    if not news_headlines:
        agent_signal = AgentSignal(
            ticker=ticker,
            agent_name=AGENT_NAME,
            signal="neutral",
            confidence=0.20,
            reasoning="无可用新闻数据，情绪分析无法运行。保持中性默认信号。",
            metrics=metrics_snapshot,
        )
        insert_agent_signal(agent_signal)
        logger.info("[Sentiment] %s: no news data, returning neutral", ticker)
        return agent_signal

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
            reasoning = f"LLM 调用失败 ({e})。获取到 {len(news_headlines)} 条新闻但无法分析情绪。"
            signal, confidence = "neutral", 0.25

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
