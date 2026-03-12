"""P2-1: Industry Valuation Positioning.

Fetches PE/PB for industry peers and calculates percentile position.

Verified APIs (2026-03-11):
- ak.stock_zh_valuation_baidu: PE(TTM), PE(静), PB for individual stocks
"""

import akshare as ak

from src.utils.logger import get_logger

logger = get_logger(__name__)


def fetch_industry_valuations(stocks: list[dict]) -> list[dict]:
    """
    Fetch PE/PB valuations for a list of stocks.

    Args:
        stocks: List of {ticker, name} dicts

    Returns:
        List of {ticker, name, pe, pb} dicts
    """
    results = []

    for stock in stocks:
        ticker = stock["ticker"]
        # Clean ticker (remove .SH/.SZ suffix if present)
        clean_ticker = ticker.split(".")[0]

        try:
            pe_df = ak.stock_zh_valuation_baidu(
                symbol=clean_ticker, indicator="市盈率(TTM)", period="近一年"
            )
            pb_df = ak.stock_zh_valuation_baidu(
                symbol=clean_ticker, indicator="市净率", period="近一年"
            )

            pe = pe_df.iloc[-1]["value"] if pe_df is not None and len(pe_df) > 0 else None
            pb = pb_df.iloc[-1]["value"] if pb_df is not None and len(pb_df) > 0 else None

            results.append({
                "ticker": ticker,
                "name": stock.get("name", ""),
                "pe": pe,
                "pb": pb,
            })

        except Exception as e:
            logger.warning("Failed to fetch valuation for %s: %s", ticker, e)
            results.append({
                "ticker": ticker,
                "name": stock.get("name", ""),
                "pe": None,
                "pb": None,
            })

    return results


def calculate_percentile_position(
    pe: float | None,
    pb: float | None,
    peers: list[dict],
) -> dict:
    """
    Calculate PE/PB percentile position within industry peers.

    Args:
        pe: Target company's PE ratio
        pb: Target company's PB ratio
        peers: List of peer valuations with 'pe' and 'pb' keys

    Returns:
        Dict with pe_percentile, pb_percentile, industry medians
    """
    pe_values = sorted([p["pe"] for p in peers if p.get("pe") and p["pe"] > 0])
    pb_values = sorted([p["pb"] for p in peers if p.get("pb") and p["pb"] > 0])

    def _percentile(value: float | None, values: list[float]) -> float:
        if not values or value is None:
            return 50.0
        below = sum(1 for v in values if v < value)
        return round((below / len(values)) * 100, 1)

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        sorted_v = sorted(values)
        mid = len(sorted_v) // 2
        if len(sorted_v) % 2 == 0:
            return (sorted_v[mid - 1] + sorted_v[mid]) / 2
        return sorted_v[mid]

    return {
        "pe_percentile": _percentile(pe, pe_values),
        "pb_percentile": _percentile(pb, pb_values),
        "industry_pe_median": _median(pe_values),
        "industry_pb_median": _median(pb_values),
        "peer_count": len(peers),
    }


def get_industry_position(ticker: str) -> dict:
    """
    Get complete industry valuation positioning for a stock.

    Returns:
        Dict with percentiles, medians, and representative comparison
    """
    from src.data.industry_mapping import get_stock_industry, get_industry_representatives

    # Clean ticker
    clean_ticker = ticker.split(".")[0]

    # 1. Get stock's industry
    industry = get_stock_industry(clean_ticker)
    if not industry:
        return {"error": "Could not determine industry", "ticker": ticker}

    # 2. Get representative stocks
    representatives = get_industry_representatives(industry)
    if not representatives:
        return {"error": f"No representatives for industry: {industry}", "ticker": ticker}

    # 3. Fetch valuations for all peers
    valuations = fetch_industry_valuations(representatives)

    # 4. Get target stock's valuation
    target_val = next((v for v in valuations if v["ticker"].split(".")[0] == clean_ticker), None)
    if not target_val:
        # Fetch separately if not in representatives
        target_val = fetch_industry_valuations([{"ticker": clean_ticker, "name": ""}])[0]

    if target_val["pe"] is None and target_val["pb"] is None:
        return {"error": "Could not fetch target valuation", "ticker": ticker}

    # 5. Calculate percentile position
    position = calculate_percentile_position(
        pe=target_val["pe"],
        pb=target_val["pb"],
        peers=valuations,
    )

    # 6. Build comparison table (leaders, median, target, lowest)
    # BUG-D FIX: Exclude target from peer selections to avoid duplicates
    target_ticker_clean = clean_ticker

    # Filter out target from peers for comparison
    peers_without_target = [v for v in valuations if v["ticker"].split(".")[0] != target_ticker_clean]
    pe_sorted_peers = sorted([v for v in peers_without_target if v.get("pe") and v["pe"] > 0], key=lambda x: x["pe"])

    comparison = []
    seen_tickers = set()  # Track added tickers to avoid duplicates

    # Top 2 peers by market position (first in list assumed to be leaders)
    for v in peers_without_target[:3]:  # Check top 3 in case some lack PE
        if v.get("pe") and v["ticker"] not in seen_tickers:
            comparison.append({**v, "category": "行业代表"})
            seen_tickers.add(v["ticker"])
            if len([c for c in comparison if c["category"] == "行业代表"]) >= 2:
                break

    # Median (from peers excluding target)
    if pe_sorted_peers:
        mid_idx = len(pe_sorted_peers) // 2
        median_stock = pe_sorted_peers[mid_idx]
        if median_stock["ticker"] not in seen_tickers:
            comparison.append({**median_stock, "category": "行业中位"})
            seen_tickers.add(median_stock["ticker"])

    # Target (always add)
    comparison.append({**target_val, "category": "本标的"})
    seen_tickers.add(target_val["ticker"])

    # Lowest PE (from peers excluding target)
    if pe_sorted_peers and pe_sorted_peers[0]["ticker"] not in seen_tickers:
        comparison.append({**pe_sorted_peers[0], "category": "最低估值"})

    return {
        "industry": industry,
        "target_ticker": ticker,
        "target_pe": target_val["pe"],
        "target_pb": target_val["pb"],
        **position,
        "comparison_table": comparison,
    }
