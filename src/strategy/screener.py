"""Factor Screener — pure Python, no LLM.

Reads config/screening_rules.yaml and evaluates all watchlist tickers
against each rule. Saves results to output/signals/ and returns
a list of ScreeningSignal objects.

Supported operators:
  gt / lt              Simple threshold comparison
  gte / lte            Greater/less than or equal
  lt_percentile        Value < Nth percentile of last N years
  lt_ratio             Value < reference_value × ratio  (e.g. price < DCF × 0.70)
  qoq_increase_gt      Quarter-over-quarter increase exceeds threshold
  positive_years_gte   At least N years of positive values in history

Usage:
    from src.strategy.screener import run_scan
    signals = run_scan(watchlist, notify=False)
"""

import json
from datetime import date, datetime
from pathlib import Path

from src.data.database import (
    get_connection,
    get_financial_metrics,
    get_income_statements,
    get_balance_sheets,
    get_cash_flows,
    get_latest_prices,
)
from src.data.models import ScreeningSignal
from src.utils.config import load_watchlist, load_screening_rules, get_project_root
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Operator implementations ──────────────────────────────────────────────────

def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        import math
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _apply_operator(ticker: str, condition: dict, snapshot: dict) -> bool | None:
    """
    Evaluate a single condition against the ticker's data snapshot.
    Returns True (passes), False (fails), or None (data missing → skip).
    """
    field    = condition["field"]
    operator = condition["operator"]
    value    = condition.get("value")
    params   = condition.get("params", {})

    current = _safe(snapshot.get(field))

    # ── Simple comparisons ────────────────────────────────────────────────────
    if operator == "gt":
        if current is None: return None
        return current > float(value)

    elif operator == "gte":
        if current is None: return None
        return current >= float(value)

    elif operator == "lt":
        if current is None: return None
        return current < float(value)

    elif operator == "lte":
        if current is None: return None
        return current <= float(value)

    # ── Historical percentile check ───────────────────────────────────────────
    elif operator == "lt_percentile":
        if current is None: return None
        pct       = float(params.get("percentile", 25))
        lookback  = int(params.get("lookback_years", 5))
        history   = _get_field_history(ticker, field, lookback)
        if len(history) < 2:
            return None  # not enough history
        import statistics
        sorted_h = sorted(history)
        idx = int(len(sorted_h) * pct / 100)
        threshold = sorted_h[max(0, idx - 1)]
        return current < threshold

    # ── Ratio check: current_price < dcf_intrinsic_value * ratio ─────────────
    elif operator == "lt_ratio":
        if current is None: return None
        reference_field = params.get("reference", "dcf_intrinsic_value")
        ratio           = float(params.get("ratio", 0.70))
        reference_val   = _safe(snapshot.get(reference_field))
        if reference_val is None: return None
        return current < reference_val * ratio

    # ── Quarter-over-quarter increase ─────────────────────────────────────────
    elif operator == "qoq_increase_gt":
        history = _get_field_history(ticker, field, 2)
        if len(history) < 2: return None
        cur_val, prev_val = history[0], history[1]
        if cur_val is None or prev_val is None or prev_val == 0: return None
        qoq_change = (cur_val - prev_val) / abs(prev_val)
        return qoq_change > float(value)

    # ── Positive years count ──────────────────────────────────────────────────
    elif operator == "positive_years_gte":
        min_years = int(value)
        history   = _get_field_history(ticker, field, 10)
        pos_count = sum(1 for v in history if v is not None and v > 0)
        return pos_count >= min_years

    else:
        logger.warning("[Screener] Unknown operator '%s' for field '%s'", operator, field)
        return None


def _get_field_history(ticker: str, field: str, years: int) -> list[float | None]:
    """Retrieve historical values for a field from the most appropriate table."""
    # Mapping from field name to DB table and column
    FIELD_MAP = {
        # financial_metrics fields
        "pe_ratio":        ("financial_metrics", "pe_ratio"),
        "pb_ratio":        ("financial_metrics", "pb_ratio"),
        "roe":             ("financial_metrics", "roe"),
        "roa":             ("financial_metrics", "roa"),
        "debt_to_equity":  ("financial_metrics", "debt_to_equity"),
        "current_ratio":   ("financial_metrics", "current_ratio"),
        "dividend_yield":  ("financial_metrics", "dividend_yield"),
        "operating_margin":("financial_metrics", "operating_margin"),
        # income_statements
        "revenue":         ("income_statements", "revenue"),
        "net_income":      ("income_statements", "net_income"),
        "eps":             ("income_statements", "eps"),
        "operating_income":("income_statements", "operating_income"),
        # balance_sheets
        "total_debt":      ("balance_sheets", "total_debt"),
        "total_equity":    ("balance_sheets", "total_equity"),
        # cash_flows
        "operating_cash_flow": ("cash_flows", "operating_cash_flow"),
        "free_cash_flow":      ("cash_flows", "free_cash_flow"),
    }
    if field not in FIELD_MAP:
        return []
    table, col = FIELD_MAP[field]
    limit = years + 2  # buffer for missing rows

    if table == "financial_metrics":
        rows = get_financial_metrics(ticker, limit=limit)
    elif table == "income_statements":
        rows = get_income_statements(ticker, limit=limit, period_type="annual")
    elif table == "balance_sheets":
        rows = get_balance_sheets(ticker, limit=limit, period_type="annual")
    elif table == "cash_flows":
        rows = get_cash_flows(ticker, limit=limit, period_type="annual")
    else:
        return []

    return [_safe(r.get(col)) for r in rows[:years]]


def _build_snapshot(ticker: str) -> dict:
    """Build a flat dict of the latest values for all screener-relevant fields."""
    snap: dict = {"ticker": ticker}

    metrics = get_financial_metrics(ticker, limit=1)
    if metrics:
        snap.update({k: v for k, v in metrics[0].items() if k not in ("id", "updated_at", "source")})

    income = get_income_statements(ticker, limit=1, period_type="annual")
    if income:
        snap.update({k: v for k, v in income[0].items() if k not in ("id", "updated_at", "source", "ticker", "period_type")})

    balance = get_balance_sheets(ticker, limit=1, period_type="annual")
    if balance:
        snap.update({k: v for k, v in balance[0].items() if k not in ("id", "updated_at", "source", "ticker", "period_type")})

    cashflow = get_cash_flows(ticker, limit=1, period_type="annual")
    if cashflow:
        snap.update({k: v for k, v in cashflow[0].items() if k not in ("id", "updated_at", "source", "ticker", "period_type")})

    # Current price
    prices = get_latest_prices(ticker, limit=1)
    if prices:
        snap["current_price"] = _safe(prices[0].get("close"))

    # DCF intrinsic value: pull from latest agent_signals if available
    with get_connection() as conn:
        row = conn.execute(
            """SELECT metrics_json FROM agent_signals
               WHERE ticker=? AND agent_name='valuation'
               ORDER BY created_at DESC LIMIT 1""",
            (ticker,)
        ).fetchone()
    if row:
        try:
            m = json.loads(row["metrics_json"] or "{}")
            if m.get("dcf_per_share"):
                snap["dcf_intrinsic_value"] = _safe(m["dcf_per_share"])
            if m.get("margin_of_safety"):
                snap["margin_of_safety"] = _safe(m["margin_of_safety"])
        except Exception:
            pass

    # Derive ROE from income+balance if not in metrics
    if snap.get("roe") is None:
        ni = _safe(snap.get("net_income"))
        eq = _safe(snap.get("total_equity"))
        if ni and eq and eq != 0:
            snap["roe"] = ni / eq  # as ratio, e.g. 0.077

    return snap


def _evaluate_rule(ticker: str, rule: dict, snapshot: dict) -> tuple[bool, list[str]]:
    """
    Evaluate a screening rule against a ticker.
    Returns (triggered: bool, matched_conditions: list[str]).
    """
    conditions = rule.get("conditions", [])
    logic      = rule.get("logic", "AND").upper()
    results: list[bool | None] = []

    for cond in conditions:
        result = _apply_operator(ticker, cond, snapshot)
        results.append(result)

    # Filter out None (data missing)
    valid = [r for r in results if r is not None]
    if not valid:
        return False, []

    if logic == "AND":
        triggered = all(valid) and len(valid) == len(results)  # all must pass, none can be missing
    elif logic == "OR":
        triggered = any(valid)
    else:
        triggered = False

    matched = [
        f"{conditions[i]['field']} {conditions[i]['operator']}"
        for i, r in enumerate(results) if r is True
    ]
    return triggered, matched


def _save_signals(signals: list[ScreeningSignal], scan_date: str) -> Path:
    """Save screening signals to output/signals/{date}.json."""
    out_dir = get_project_root() / "output" / "signals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{scan_date}.json"

    data = [
        {
            "ticker":       s.ticker,
            "rule_name":    s.rule_name,
            "signal":       s.signal,
            "description":  s.description,
            "metrics":      s.metrics,
            "triggered_at": s.triggered_at.isoformat() if s.triggered_at else scan_date,
        }
        for s in signals
    ]
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[Screener] Saved %d signals to %s", len(data), out_path)
    return out_path


def run_scan(
    watchlist: dict | None = None,
    notify: bool = False,
) -> list[ScreeningSignal]:
    """
    Run the factor screener over all watchlist tickers.

    Args:
        watchlist: Optional dict from watchlist.yaml. If None, loads from config.
        notify:    If True, send email via email_sender (requires BREVO_API_KEY).

    Returns:
        List of ScreeningSignal objects where a rule was triggered.
    """
    if watchlist is None:
        watchlist = load_watchlist()

    rules = load_screening_rules()
    if not rules:
        logger.warning("[Screener] No rules found in screening_rules.yaml")
        return []

    scan_date = str(date.today())
    all_signals: list[ScreeningSignal] = []
    tickers_scanned = 0
    start_ts = datetime.now()

    # Flatten watchlist into (ticker, market) pairs
    ticket_list: list[tuple[str, str]] = []
    for market_key, items in watchlist.get("watchlist", {}).items():
        for item in items:
            t = item.get("ticker") if isinstance(item, dict) else str(item)
            ticket_list.append((t, market_key))

    logger.info("[Screener] Scanning %d tickers against %d rules...", len(ticket_list), len(rules))

    for ticker, market in ticket_list:
        tickers_scanned += 1
        try:
            snapshot = _build_snapshot(ticker)
        except Exception as e:
            logger.warning("[Screener] Failed to build snapshot for %s: %s", ticker, e)
            continue

        for rule in rules:
            rule_name = rule.get("name", "unnamed")
            try:
                triggered, matched = _evaluate_rule(ticker, rule, snapshot)
            except Exception as e:
                logger.warning("[Screener] Rule '%s' evaluation failed for %s: %s", rule_name, ticker, e)
                continue

            if triggered:
                sig = ScreeningSignal(
                    ticker=ticker,
                    rule_name=rule_name,
                    signal="alert" if "预警" in rule_name else "opportunity",
                    description=rule.get("description", ""),
                    metrics={
                        "pe_ratio":        _safe(snapshot.get("pe_ratio")),
                        "roe":             round(_safe(snapshot.get("roe")) * 100, 1) if _safe(snapshot.get("roe")) else None,
                        "current_price":   _safe(snapshot.get("current_price")),
                        "dcf_intrinsic":   _safe(snapshot.get("dcf_intrinsic_value")),
                        "margin_of_safety":_safe(snapshot.get("margin_of_safety")),
                        "matched_conditions": matched,
                        "market":          market,
                    },
                )
                all_signals.append(sig)
                logger.info("[Screener] ✓ %s triggered rule '%s'", ticker, rule_name)

    # Save to output/signals/
    signal_path = _save_signals(all_signals, scan_date)

    # Log scan to DB
    duration_ms = int((datetime.now() - start_ts).total_seconds() * 1000)
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO scan_logs (scan_date, tickers_scanned, signals_found, duration_ms)
                   VALUES (?, ?, ?, ?)""",
                (scan_date, tickers_scanned, len(all_signals), duration_ms)
            )
    except Exception as e:
        logger.debug("[Screener] Could not write scan_logs: %s", e)

    logger.info("[Screener] Done: %d/%d tickers triggered signals in %dms",
                len({s.ticker for s in all_signals}), tickers_scanned, duration_ms)

    # Send email notification
    if notify and all_signals:
        try:
            from src.notification.telegram_notifier import send_signal_alert
            send_signal_alert(all_signals, scan_date)
        except Exception as e:
            logger.warning("[Screener] Telegram notification failed: %s", e)
    elif notify and not all_signals:
        logger.info("[Screener] No signals found — skipping email notification")

    return all_signals
