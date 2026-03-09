"""Comparable Company Analysis Module.

Implements PROJECT_ROADMAP.md P2-⑧:
- Reads user-specified comparables from watchlist.yaml
- Auto-selects comparable companies using AKShare API
- Compares PE, PB, ROE, dividend yield
- Calculates percentile ranking vs industry peers

Comparison Metrics:
- P/E (TTM): Price to Earnings ratio
- P/B: Price to Book ratio
- ROE: Return on Equity
- Dividend Yield: Annual dividend / current price
"""

import yaml
from pathlib import Path
from typing import Optional

from src.data.database import get_financial_metrics, get_latest_prices
from src.agents.industry_classifier import classify_industry, get_industry_comparables
from src.utils.config import get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _safe(x) -> float | None:
    """Safe float conversion."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get_comparables_from_watchlist(ticker: str) -> list[str]:
    """
    Get user-specified comparable companies from watchlist.yaml.

    Args:
        ticker: Target stock ticker

    Returns:
        List of comparable tickers, empty if not specified
    """
    watchlist_path = get_project_root() / "config" / "watchlist.yaml"

    try:
        with open(watchlist_path, "r", encoding="utf-8") as f:
            watchlist = yaml.safe_load(f)

        # Search for ticker in all markets
        for market, stocks in watchlist.get("watchlist", {}).items():
            for stock in stocks:
                if stock.get("ticker") == ticker:
                    comparables = stock.get("comparables", [])
                    if comparables:
                        logger.info(
                            f"[Comparables] Found {len(comparables)} user-specified comparables for {ticker}"
                        )
                        return comparables
                    else:
                        logger.debug(f"[Comparables] No user comparables specified for {ticker}")
                        return []

        logger.debug(f"[Comparables] {ticker} not found in watchlist")
        return []

    except Exception as e:
        logger.error(f"[Comparables] Failed to read watchlist: {e}")
        return []


def _fetch_sector_stocks_via_akshare(sector: str, target_ticker: str) -> list[dict]:
    """
    Fetch stocks in the same sector via AKShare API.

    Uses ak.stock_board_industry_cons_em() to get constituents of an industry board.

    Args:
        sector: Industry sector name (e.g., "银行", "石油服务")
        target_ticker: Target ticker to exclude from results

    Returns:
        List of dicts with {ticker, name, market_cap, pe, pb} for each stock
    """
    try:
        import akshare as ak

        # Map common sector names to AKShare industry board names
        sector_mapping = {
            "银行": "银行",
            "保险": "保险",
            "石油服务": "油服工程",
            "石油": "石油开采",
            "能源": "油气开采",
            "消费": "食品饮料",
            "食品饮料": "食品饮料",
            "白酒": "白酒",
            "医药": "医药商业",
            "生物制药": "生物制品",
            "科技": "软件开发",
            "软件": "软件开发",
            "半导体": "半导体及元件",
            "自动化": "工业自动化",
            "机械": "通用机械",
        }

        board_name = sector_mapping.get(sector, sector)

        # Get all industry boards first
        try:
            boards_df = ak.stock_board_industry_name_em()
            if boards_df is None or boards_df.empty:
                logger.warning("[Comparables] Failed to get industry boards from AKShare")
                return []

            # Find best matching board
            board_match = None
            for _, row in boards_df.iterrows():
                if board_name in row.get("板块名称", ""):
                    board_match = row.get("板块名称")
                    break

            if not board_match:
                # Try partial match
                for _, row in boards_df.iterrows():
                    if any(kw in row.get("板块名称", "") for kw in board_name[:2]):
                        board_match = row.get("板块名称")
                        break

            if not board_match:
                logger.debug(f"[Comparables] No matching board found for sector: {sector}")
                return []

            # Get constituents of the matched board
            cons_df = ak.stock_board_industry_cons_em(symbol=board_match)
            if cons_df is None or cons_df.empty:
                return []

            # Parse results
            results = []
            target_code = target_ticker.split(".")[0]

            for _, row in cons_df.iterrows():
                code = str(row.get("代码", ""))
                if code == target_code:
                    continue  # Skip target

                # Determine market suffix
                if code.startswith("6"):
                    full_ticker = f"{code}.SH"
                elif code.startswith(("0", "3")):
                    full_ticker = f"{code}.SZ"
                else:
                    continue

                results.append({
                    "ticker": full_ticker,
                    "name": row.get("名称", ""),
                    "market_cap": _safe(row.get("总市值")),
                    "pe": _safe(row.get("市盈率-动态")),
                    "pb": _safe(row.get("市净率")),
                })

            logger.info(
                f"[Comparables] Found {len(results)} stocks in {board_match} via AKShare"
            )
            return results

        except Exception as e:
            logger.warning(f"[Comparables] AKShare industry board query failed: {e}")
            return []

    except ImportError:
        logger.debug("[Comparables] AKShare not installed, skipping dynamic selection")
        return []
    except Exception as e:
        logger.warning(f"[Comparables] AKShare API error: {e}")
        return []


def _select_by_market_cap_similarity(
    stocks: list[dict],
    target_market_cap: Optional[float],
    limit: int = 5,
) -> list[str]:
    """
    Select comparable stocks by market cap similarity.

    Selects stocks with market cap within 0.3x-3x of target.
    Falls back to closest by market cap if not enough matches.

    Args:
        stocks: List of stock dicts with market_cap field
        target_market_cap: Target company's market cap
        limit: Number of comparables to return

    Returns:
        List of ticker strings
    """
    if not stocks:
        return []

    if not target_market_cap or target_market_cap <= 0:
        # Fallback: just return first N stocks by market cap descending
        sorted_stocks = sorted(
            stocks,
            key=lambda x: x.get("market_cap") or 0,
            reverse=True,
        )
        return [s["ticker"] for s in sorted_stocks[:limit]]

    # Filter stocks within 0.3x-3x of target market cap
    min_cap = target_market_cap * 0.3
    max_cap = target_market_cap * 3.0

    similar_stocks = [
        s for s in stocks
        if s.get("market_cap") and min_cap <= s["market_cap"] <= max_cap
    ]

    if len(similar_stocks) >= limit:
        # Sort by closeness to target market cap
        similar_stocks.sort(
            key=lambda x: abs(x["market_cap"] - target_market_cap)
        )
        return [s["ticker"] for s in similar_stocks[:limit]]

    # Not enough in range, fall back to closest by market cap
    stocks_with_cap = [s for s in stocks if s.get("market_cap")]
    stocks_with_cap.sort(
        key=lambda x: abs(x["market_cap"] - target_market_cap)
    )
    return [s["ticker"] for s in stocks_with_cap[:limit]]


def _get_target_market_cap(ticker: str) -> Optional[float]:
    """Get target company's market cap from database."""
    metric_rows = get_financial_metrics(ticker, limit=1)
    if metric_rows:
        return _safe(metric_rows[0].get("market_cap"))
    return None


def auto_select_comparables(ticker: str, sector: str, limit: int = 5) -> list[str]:
    """
    Auto-select comparable companies using industry profile or AKShare API.

    Fallback order:
    1. Industry profile comparables from industry_profiles.yaml
    2. AKShare API dynamic selection (same sector, similar market cap)

    Args:
        ticker: Target stock ticker
        sector: Industry sector
        limit: Number of comparables to return

    Returns:
        List of comparable tickers
    """
    # First, try to get comparables from industry profile
    industry = classify_industry(sector)
    industry_comps = get_industry_comparables(industry)

    if industry_comps:
        # Filter out the target ticker and take up to limit
        comp_tickers = [
            c["ticker"] for c in industry_comps
            if c.get("ticker") != ticker
        ][:limit]

        if comp_tickers:
            logger.info(
                f"[Comparables] Using {len(comp_tickers)} comparables from "
                f"{industry} industry profile"
            )
            return comp_tickers

    # Fallback: AKShare dynamic selection
    # Fetch all stocks in the same sector
    sector_stocks = _fetch_sector_stocks_via_akshare(sector, ticker)

    if sector_stocks:
        # Get target's market cap for similarity matching
        target_market_cap = _get_target_market_cap(ticker)

        # Select by market cap similarity
        selected = _select_by_market_cap_similarity(
            sector_stocks, target_market_cap, limit
        )

        if selected:
            logger.info(
                f"[Comparables] Selected {len(selected)} comparables via AKShare "
                f"(sector={sector}, market_cap_similarity)"
            )
            return selected

    logger.debug(
        f"[Comparables] No comparables found for {ticker}, "
        f"returning empty list"
    )
    return []


def fetch_comparable_metrics(ticker: str) -> dict:
    """
    Fetch key valuation metrics for a single stock.

    Args:
        ticker: Stock ticker

    Returns:
        Dictionary with PE, PB, ROE, dividend_yield
    """
    # Get financial metrics
    metric_rows = get_financial_metrics(ticker, limit=1)
    price_rows = get_latest_prices(ticker, limit=1)

    if not metric_rows or not price_rows:
        logger.warning(f"[Comparables] No data for {ticker}")
        return {
            "ticker": ticker,
            "pe": None,
            "pb": None,
            "roe": None,
            "dividend_yield": None,
        }

    metrics = metric_rows[0]
    price = _safe(price_rows[0].get("close"))

    pe = _safe(metrics.get("pe_ttm"))
    pb = _safe(metrics.get("pb"))
    roe = _safe(metrics.get("roe"))

    # Calculate dividend yield if not stored
    dividend_yield = _safe(metrics.get("dividend_yield"))
    if dividend_yield is None:
        dividend_per_share = _safe(metrics.get("dividend_per_share"))
        if dividend_per_share and price and price > 0:
            dividend_yield = dividend_per_share / price

    return {
        "ticker": ticker,
        "pe": pe,
        "pb": pb,
        "roe": roe,
        "dividend_yield": dividend_yield,
    }


def calculate_percentile_rank(value: float, peer_values: list[float]) -> float:
    """
    Calculate percentile rank of a value among peers.

    Args:
        value: Target value
        peer_values: List of peer values (including target)

    Returns:
        Percentile (0-100), where higher is better for ROE/dividend,
        lower is better for PE/PB
    """
    if not peer_values or value is None:
        return 50.0  # Default to median if no comparison possible

    # Count how many peers have values below target
    valid_peers = [v for v in peer_values if v is not None]

    if not valid_peers:
        return 50.0

    below_count = sum(1 for v in valid_peers if v < value)
    percentile = (below_count / len(valid_peers)) * 100

    return percentile


def run_comparable_analysis(
    ticker: str,
    sector: str,
    user_comparables: Optional[list[str]] = None,
) -> dict:
    """
    Run comparable company analysis.

    Args:
        ticker: Target stock ticker
        sector: Industry sector
        user_comparables: Optional user-specified comparables

    Returns:
        Dictionary with:
        - target_metrics: Metrics for target stock
        - peer_metrics: List of metrics for comparables
        - percentiles: Percentile rankings for each metric
        - industry_median: Median values for industry
        - comparison_table: Formatted comparison table
    """
    # Get comparables list
    if user_comparables:
        comparables = user_comparables
        logger.info(f"[Comparables] Using {len(comparables)} user-specified comparables")
    else:
        # Try to read from watchlist first
        comparables = get_comparables_from_watchlist(ticker)
        if not comparables:
            # Auto-select if available
            comparables = auto_select_comparables(ticker, sector, limit=5)

    if not comparables:
        logger.warning(
            f"[Comparables] No comparables available for {ticker}, "
            f"skipping analysis"
        )
        return {
            "target_metrics": fetch_comparable_metrics(ticker),
            "peer_metrics": [],
            "percentiles": {},
            "industry_median": {},
            "comparison_table": None,
            "note": "No comparable companies available",
        }

    # Fetch metrics for target and peers
    target_metrics = fetch_comparable_metrics(ticker)
    peer_metrics = [fetch_comparable_metrics(comp) for comp in comparables]

    # Include target in peer group for percentile calculation
    all_metrics = [target_metrics] + peer_metrics

    # Calculate percentiles for each metric
    # For PE and PB: lower is better (invert percentile)
    # For ROE and dividend_yield: higher is better

    pe_values = [m["pe"] for m in all_metrics if m["pe"] is not None]
    pb_values = [m["pb"] for m in all_metrics if m["pb"] is not None]
    roe_values = [m["roe"] for m in all_metrics if m["roe"] is not None]
    div_values = [m["dividend_yield"] for m in all_metrics if m["dividend_yield"] is not None]

    percentiles = {}

    # PE and PB: lower is better, so invert percentile
    if target_metrics["pe"] is not None and pe_values:
        pe_pct = calculate_percentile_rank(target_metrics["pe"], pe_values)
        percentiles["pe"] = 100 - pe_pct  # Invert
    else:
        percentiles["pe"] = None

    if target_metrics["pb"] is not None and pb_values:
        pb_pct = calculate_percentile_rank(target_metrics["pb"], pb_values)
        percentiles["pb"] = 100 - pb_pct  # Invert
    else:
        percentiles["pb"] = None

    # ROE and dividend: higher is better
    if target_metrics["roe"] is not None and roe_values:
        percentiles["roe"] = calculate_percentile_rank(target_metrics["roe"], roe_values)
    else:
        percentiles["roe"] = None

    if target_metrics["dividend_yield"] is not None and div_values:
        percentiles["dividend_yield"] = calculate_percentile_rank(
            target_metrics["dividend_yield"], div_values
        )
    else:
        percentiles["dividend_yield"] = None

    # Calculate industry medians
    import statistics

    industry_median = {
        "pe": statistics.median(pe_values) if pe_values else None,
        "pb": statistics.median(pb_values) if pb_values else None,
        "roe": statistics.median(roe_values) if roe_values else None,
        "dividend_yield": statistics.median(div_values) if div_values else None,
    }

    # Generate comparison table
    comparison_table = _format_comparison_table(
        ticker, target_metrics, peer_metrics, percentiles, industry_median
    )

    logger.info(
        f"[Comparables] Analysis complete for {ticker}: "
        f"PE pct={percentiles.get('pe', 'N/A')}, "
        f"PB pct={percentiles.get('pb', 'N/A')}, "
        f"ROE pct={percentiles.get('roe', 'N/A')}"
    )

    return {
        "target_metrics": target_metrics,
        "peer_metrics": peer_metrics,
        "percentiles": percentiles,
        "industry_median": industry_median,
        "comparison_table": comparison_table,
        "note": None,
    }


def _format_comparison_table(
    ticker: str,
    target: dict,
    peers: list[dict],
    percentiles: dict,
    medians: dict,
) -> str:
    """
    Format comparison table as markdown.

    Args:
        ticker: Target stock ticker
        target: Target metrics
        peers: Peer metrics
        percentiles: Percentile rankings
        medians: Industry medians

    Returns:
        Markdown-formatted comparison table
    """
    lines = ["### 可比公司分析", ""]

    # Table header
    lines.append("| 公司 | P/E (TTM) | P/B | ROE | 股息率 |")
    lines.append("|:-----|:----------|:----|:----|:-------|")

    # Target row (highlighted)
    lines.append(
        f"| **{ticker}** (目标) | "
        f"{target['pe']:.2f} " if target['pe'] else "N/A | "
        f"{target['pb']:.2f} " if target['pb'] else "N/A | "
        f"{target['roe']*100:.1f}% " if target['roe'] else "N/A | "
        f"{target['dividend_yield']*100:.2f}% |" if target['dividend_yield'] else "N/A |"
    )

    # Peer rows
    for peer in peers:
        lines.append(
            f"| {peer['ticker']} | "
            f"{peer['pe']:.2f} " if peer['pe'] else "N/A | "
            f"{peer['pb']:.2f} " if peer['pb'] else "N/A | "
            f"{peer['roe']*100:.1f}% " if peer['roe'] else "N/A | "
            f"{peer['dividend_yield']*100:.2f}% |" if peer['dividend_yield'] else "N/A |"
        )

    # Industry median row
    lines.append(
        f"| **行业中位数** | "
        f"{medians['pe']:.2f} " if medians['pe'] else "N/A | "
        f"{medians['pb']:.2f} " if medians['pb'] else "N/A | "
        f"{medians['roe']*100:.1f}% " if medians['roe'] else "N/A | "
        f"{medians['dividend_yield']*100:.2f}% |" if medians['dividend_yield'] else "N/A |"
    )

    lines.append("")

    # Percentile summary
    lines.append("**相对估值排名** (百分位，0=最便宜/最差，100=最贵/最好):")
    lines.append("")

    if percentiles.get("pe") is not None:
        lines.append(f"- P/E: {percentiles['pe']:.0f}百分位 (越低越好)")
    if percentiles.get("pb") is not None:
        lines.append(f"- P/B: {percentiles['pb']:.0f}百分位 (越低越好)")
    if percentiles.get("roe") is not None:
        lines.append(f"- ROE: {percentiles['roe']:.0f}百分位 (越高越好)")
    if percentiles.get("dividend_yield") is not None:
        lines.append(f"- 股息率: {percentiles['dividend_yield']:.0f}百分位 (越高越好)")

    return "\n".join(lines)
