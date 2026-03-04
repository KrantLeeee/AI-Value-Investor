"""Backtester — pure Python factor backtest engine.

Methodology (from tech-design-v1.md §5.2):
  For each year-end in [start_year, end_year - hold_years]:
    1. Apply screening rules against historical DB data at that year-end
    2. Equal-weight buy all passing tickers at year-end price
    3. Sell at (year + hold_years) year-end price
    4. Compute per-position returns

Summary statistics:
  - Annualised return (CAGR)
  - Win rate (% of positions with positive return)
  - Max drawdown (largest peak-to-trough using annual snapshots)
  - Sharpe ratio (mean return / std dev × √hold_years)
  - Benchmark: simple buy-and-hold of all watchlist tickers (equal weight)

Usage:
    from src.strategy.backtester import run_factor_backtest
    results = run_factor_backtest(rule_name="安全边际", start=2020, end=2024, hold=3)
"""

import math
import statistics
from datetime import date

from src.data.database import get_connection, get_financial_metrics, get_income_statements, get_balance_sheets, get_cash_flows
from src.utils.config import load_watchlist, load_screening_rules
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _get_price_at_year_end(ticker: str, year: int) -> float | None:
    """
    Get closest available closing price to Dec 31 of the given year
    by querying daily_prices with a date range of the last 30 trading days.
    """
    with get_connection() as conn:
        row = conn.execute(
            """SELECT close FROM daily_prices
               WHERE ticker=?
                 AND date >= ? AND date <= ?
               ORDER BY date DESC LIMIT 1""",
            (ticker, f"{year}-12-01", f"{year}-12-31")
        ).fetchone()
    return _safe(row["close"]) if row else None


def _get_historical_snapshot(ticker: str, year: int) -> dict:
    """Build a financial snapshot using data available at year-end."""
    snap: dict = {"ticker": ticker}

    # Use annual period_end_date <= year-12-31 to simulate "available data at that time"
    with get_connection() as conn:
        m = conn.execute(
            """SELECT * FROM financial_metrics WHERE ticker=? AND date <= ?
               ORDER BY date DESC LIMIT 1""",
            (ticker, f"{year}-12-31")
        ).fetchone()
        i = conn.execute(
            """SELECT * FROM income_statements WHERE ticker=? AND period_end_date <= ?
               AND period_type='annual' ORDER BY period_end_date DESC LIMIT 1""",
            (ticker, f"{year}-12-31")
        ).fetchone()
        b = conn.execute(
            """SELECT * FROM balance_sheets WHERE ticker=? AND period_end_date <= ?
               AND period_type='annual' ORDER BY period_end_date DESC LIMIT 1""",
            (ticker, f"{year}-12-31")
        ).fetchone()
        cf = conn.execute(
            """SELECT * FROM cash_flows WHERE ticker=? AND period_end_date <= ?
               AND period_type='annual' ORDER BY period_end_date DESC LIMIT 1""",
            (ticker, f"{year}-12-31")
        ).fetchone()
        ag = conn.execute(
            """SELECT metrics_json FROM agent_signals WHERE ticker=?
               AND agent_name='valuation' AND created_at <= ?
               ORDER BY created_at DESC LIMIT 1""",
            (ticker, f"{year}-12-31 23:59:59")
        ).fetchone()

    for row in [m, i, b, cf]:
        if row:
            snap.update({k: v for k, v in dict(row).items()
                         if k not in ("id", "updated_at", "source", "ticker", "period_type")})

    # DCF intrinsic value from agent signals
    if ag:
        import json
        try:
            metrics = json.loads(ag["metrics_json"] or "{}")
            if metrics.get("dcf_per_share"):
                snap["dcf_intrinsic_value"] = _safe(metrics["dcf_per_share"])
        except Exception:
            pass

    snap["current_price"] = _get_price_at_year_end(ticker, year)

    # Derive ROE if missing
    if snap.get("roe") is None:
        ni = _safe(snap.get("net_income"))
        eq = _safe(snap.get("total_equity"))
        if ni and eq and eq != 0:
            snap["roe"] = ni / eq

    return snap


def _evaluate_rule_historical(ticker: str, rule: dict, snapshot: dict) -> bool:
    """
    Lightweight rule evaluation for backtesting.
    Uses same operator logic as screener but no DB calls (snapshot already built).
    """
    from src.strategy.screener import _apply_operator
    conditions = rule.get("conditions", [])
    logic      = rule.get("logic", "AND").upper()
    results = [_apply_operator(ticker, cond, snapshot) for cond in conditions]
    valid   = [r for r in results if r is not None]
    if not valid:
        return False
    return all(valid) if logic == "AND" else any(valid)


def _cagr(total_return: float, hold_years: int) -> float:
    """Compound Annual Growth Rate from total return."""
    if hold_years <= 0:
        return 0.0
    return (1 + total_return) ** (1 / hold_years) - 1


def _max_drawdown(returns: list[float]) -> float:
    """Max drawdown from a list of per-position returns (simplified)."""
    if not returns:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        if r > peak:
            peak = r
        dd = peak - r
        if dd > max_dd:
            max_dd = dd
    return max_dd


def run_factor_backtest(
    rule_name: str,
    start: int,
    end: int,
    hold: int,
) -> dict:
    """
    Run a year-end factor backtest for a named rule.

    Args:
        rule_name: Rule name matching config/screening_rules.yaml
        start:     First year to screen (screen at Dec 31 of this year)
        end:       Last year to screen
        hold:      Hold period in years

    Returns:
        Dict with performance statistics:
          - positions: list of individual position records
          - n_positions: total positions taken
          - win_rate: % of positions with positive return
          - avg_return: average per-position return
          - cagr: annualised compound return
          - sharpe: Sharpe ratio (no risk-free rate)
          - max_drawdown: max peak-to-trough
    """
    rules = load_screening_rules()
    rule = next((r for r in rules if r.get("name") == rule_name), None)
    if rule is None:
        raise ValueError(f"Rule '{rule_name}' not found in screening_rules.yaml. "
                         f"Available: {[r.get('name') for r in rules]}")

    watchlist = load_watchlist()
    all_tickers = []
    for market_key, items in watchlist.get("watchlist", {}).items():
        for item in items:
            t = item.get("ticker") if isinstance(item, dict) else str(item)
            all_tickers.append(t)

    if not all_tickers:
        logger.warning("[Backtest] No tickers in watchlist")
        return {"error": "Empty watchlist"}

    positions = []
    logger.info("[Backtest] Rule='%s' | %d-%d | hold=%dy | %d tickers",
                rule_name, start, end, hold, len(all_tickers))

    for screen_year in range(start, end - hold + 1):
        selected: list[str] = []
        for ticker in all_tickers:
            try:
                snap = _get_historical_snapshot(ticker, screen_year)
                if _evaluate_rule_historical(ticker, rule, snap):
                    selected.append(ticker)
            except Exception as e:
                logger.debug("[Backtest] %s/%d snapshot error: %s", ticker, screen_year, e)

        if not selected:
            logger.info("[Backtest] %d: no tickers passed rule '%s'", screen_year, rule_name)
            continue

        logger.info("[Backtest] %d: %d tickers selected → %s", screen_year, len(selected), selected)

        for ticker in selected:
            buy_price  = _get_price_at_year_end(ticker, screen_year)
            sell_price = _get_price_at_year_end(ticker, screen_year + hold)

            if buy_price is None or sell_price is None:
                logger.debug("[Backtest] %s: missing price data for %d→%d",
                             ticker, screen_year, screen_year + hold)
                ret = None
            else:
                ret = (sell_price - buy_price) / buy_price

            positions.append({
                "ticker":      ticker,
                "buy_year":    screen_year,
                "sell_year":   screen_year + hold,
                "buy_price":   buy_price,
                "sell_price":  sell_price,
                "return":      ret,
                "cagr":        _cagr(ret, hold) if ret is not None else None,
            })

    # ── Summary statistics ────────────────────────────────────────────────────
    valid_positions = [p for p in positions if p["return"] is not None]
    returns = [p["return"] for p in valid_positions]

    if not returns:
        return {
            "rule": rule_name, "start": start, "end": end, "hold": hold,
            "positions": positions,
            "n_screened": len(positions),
            "n_with_price_data": 0,
            "error": "No price data available for selected tickers. Run `invest fetch --all` first.",
        }

    wins        = [r for r in returns if r > 0]
    win_rate    = len(wins) / len(returns) if returns else 0
    avg_return  = statistics.mean(returns)
    std_return  = statistics.stdev(returns) if len(returns) >= 2 else 0
    sharpe      = (avg_return / std_return) if std_return > 0 else 0
    max_dd      = _max_drawdown(returns)

    # Annualised stats
    cagrs = [p["cagr"] for p in valid_positions if p["cagr"] is not None]
    avg_cagr = statistics.mean(cagrs) if cagrs else 0

    summary = {
        "rule":             rule_name,
        "start":            start,
        "end":              end,
        "hold":             hold,
        "n_screened":       len(positions),
        "n_with_price_data": len(valid_positions),
        "win_rate":         round(win_rate * 100, 1),
        "avg_return_pct":   round(avg_return * 100, 2),
        "avg_cagr_pct":     round(avg_cagr * 100, 2),
        "sharpe":           round(sharpe, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "best_position":    max(valid_positions, key=lambda p: p["return"]) if valid_positions else None,
        "worst_position":   min(valid_positions, key=lambda p: p["return"]) if valid_positions else None,
        "positions":        positions,
    }

    logger.info(
        "[Backtest] Rule='%s' | Positions=%d | WinRate=%.0f%% | AvgReturn=%.1f%% | CAGR=%.1f%% | Sharpe=%.2f",
        rule_name, len(valid_positions), win_rate * 100, avg_return * 100, avg_cagr * 100, sharpe
    )
    return summary
