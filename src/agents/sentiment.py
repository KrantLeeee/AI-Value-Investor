"""Sentiment Agent — LLM-powered news sentiment analysis.

Uses DeepSeek (low-cost model) to classify recent news headlines
and compute an overall sentiment score for the ticker.

If no news data is available in DB, returns neutral signal with low confidence.
If LLM is unavailable, returns neutral with a note.
"""

import json
from datetime import date

from src.data.database import get_manual_docs, insert_agent_signal
from src.data.models import AgentSignal
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


def run(
    ticker: str,
    market: str,
    use_llm: bool = True,
) -> AgentSignal:
    """
    Run the Sentiment Agent.
    Returns AgentSignal and persists to DB.
    """
    # Gather news from all available sources
    news_headlines = _get_news_from_akshare(ticker, market)
    if not news_headlines:
        news_headlines = _get_news_from_db(ticker)

    metrics_snapshot: dict = {
        "news_count": len(news_headlines),
        "news_days": NEWS_LOOKBACK_DAYS,
    }

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
                metrics_snapshot.update({
                    "sentiment_score":  parsed.get("sentiment_score", 0.0),
                    "positive_count":   parsed.get("positive_count", 0),
                    "negative_count":   parsed.get("negative_count", 0),
                    "neutral_count":    parsed.get("neutral_count", 0),
                    "key_events":       parsed.get("key_events", []),
                    "risks":            parsed.get("risks", []),
                })
            except Exception:
                # Parse signal from prose
                text_lower = llm_text.lower()
                if any(w in text_lower for w in ["positive", "看多", "正面", "bullish"]):
                    signal = "bullish"
                elif any(w in text_lower for w in ["negative", "看空", "负面", "bearish"]):
                    signal = "bearish"
                else:
                    signal = "neutral"
                confidence = 0.50
                reasoning = llm_text
                metrics_snapshot["sentiment_score"] = 0.1 if signal == "bullish" else -0.1 if signal == "bearish" else 0.0

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
