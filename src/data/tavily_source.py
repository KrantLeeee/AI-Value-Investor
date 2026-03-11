"""Tavily web search integration for P1-1.

Provides financial news search using Tavily's finance-optimized search.
"""

import os
from datetime import datetime
from typing import Literal

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TavilySource:
    """
    Tavily API client for financial news search.

    Requires TAVILY_API_KEY environment variable.
    """

    def __init__(self):
        from tavily import TavilyClient

        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise ValueError("TAVILY_API_KEY environment variable not set")

        self.client = TavilyClient(api_key=api_key)
        logger.info("TavilySource initialized")

    def search_news(
        self,
        query: str,
        max_results: int = 10,
        time_range: Literal["day", "week", "month", "year"] | None = "week",
        include_answer: bool = True,
    ) -> list[dict]:
        """
        Search for financial news.

        Args:
            query: Search query (e.g., "贵州茅台 业绩")
            max_results: Max number of results (1-20)
            time_range: Filter by recency
            include_answer: Include LLM-generated summary

        Returns:
            List of dicts: {title, url, content, score, published_date}
        """
        try:
            response = self.client.search(
                query=query,
                topic="finance",  # Optimized for financial content
                max_results=min(max_results, 20),
                time_range=time_range,
                include_answer=include_answer,
            )

            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                    "score": item.get("score", 0.0),
                    "source": "tavily",
                    "fetched_at": datetime.now().isoformat(),
                })

            # Store LLM summary if available
            if include_answer and response.get("answer"):
                self._last_summary = response["answer"]

            logger.info("Tavily search: %d results for '%s'", len(results), query[:50])
            return results

        except Exception as e:
            logger.error("Tavily search failed: %s", e)
            return []

    def get_last_summary(self) -> str | None:
        """Return LLM-generated summary from last search."""
        return getattr(self, "_last_summary", None)
