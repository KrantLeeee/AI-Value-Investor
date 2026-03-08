"""SQLite database layer — schema creation and CRUD operations."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from src.data.models import (
    BalanceSheet,
    CashFlow,
    DailyPrice,
    FinancialMetrics,
    IncomeStatement,
    ManualDoc,
    AgentSignal,
    ScreeningSignal,
)
from src.utils.config import get_db_path
from src.utils.logger import get_logger

logger = get_logger(__name__)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS daily_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    market      TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    volume      INTEGER,
    source      TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS income_statements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    period_end_date     TEXT NOT NULL,
    period_type         TEXT NOT NULL,
    revenue             REAL,
    cost_of_revenue     REAL,
    gross_profit        REAL,
    operating_income    REAL,
    net_income          REAL,
    ebitda              REAL,
    eps                 REAL,
    eps_diluted         REAL,
    shares_outstanding  REAL,
    source              TEXT NOT NULL,
    updated_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, period_end_date, period_type)
);

CREATE TABLE IF NOT EXISTS balance_sheets (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker               TEXT NOT NULL,
    period_end_date      TEXT NOT NULL,
    period_type          TEXT NOT NULL,
    total_assets         REAL,
    total_liabilities    REAL,
    total_equity         REAL,
    current_assets       REAL,
    current_liabilities  REAL,
    cash_and_equivalents REAL,
    total_debt           REAL,
    book_value_per_share REAL,
    source               TEXT NOT NULL,
    updated_at           TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, period_end_date, period_type)
);

CREATE TABLE IF NOT EXISTS cash_flows (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker               TEXT NOT NULL,
    period_end_date      TEXT NOT NULL,
    period_type          TEXT NOT NULL,
    operating_cash_flow  REAL,
    capital_expenditure  REAL,
    free_cash_flow       REAL,
    dividends_paid       REAL,
    depreciation         REAL,
    source               TEXT NOT NULL,
    updated_at           TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, period_end_date, period_type)
);

CREATE TABLE IF NOT EXISTS financial_metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT NOT NULL,
    date             TEXT NOT NULL,
    pe_ratio         REAL,
    pb_ratio         REAL,
    ps_ratio         REAL,
    roe              REAL,
    roa              REAL,
    debt_to_equity   REAL,
    current_ratio    REAL,
    dividend_yield   REAL,
    operating_margin REAL,
    revenue_growth   REAL,
    net_income_growth REAL,
    fcf_per_share    REAL,
    market_cap       REAL,
    enterprise_value REAL,
    source           TEXT NOT NULL,
    updated_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS manual_docs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker         TEXT NOT NULL,
    file_name      TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    doc_type       TEXT DEFAULT 'other',
    extracted_text TEXT,
    text_length    INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'pending',
    created_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(ticker, file_name)
);

CREATE TABLE IF NOT EXISTS agent_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    signal      TEXT NOT NULL,
    confidence  REAL,
    reasoning   TEXT,
    metrics_json TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scan_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date        TEXT NOT NULL,
    tickers_scanned  INTEGER DEFAULT 0,
    signals_found    INTEGER DEFAULT 0,
    email_sent       INTEGER DEFAULT 0,
    duration_ms      INTEGER,
    error_message    TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    action      TEXT NOT NULL,
    amount      REAL NOT NULL,
    shares      REAL,
    price       REAL,
    date        TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON daily_prices(ticker, date);
CREATE INDEX IF NOT EXISTS idx_income_ticker ON income_statements(ticker, period_end_date);
CREATE INDEX IF NOT EXISTS idx_balance_ticker ON balance_sheets(ticker, period_end_date);
CREATE INDEX IF NOT EXISTS idx_cashflow_ticker ON cash_flows(ticker, period_end_date);
CREATE INDEX IF NOT EXISTS idx_metrics_ticker ON financial_metrics(ticker, date);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON agent_signals(ticker, created_at);
"""


@contextmanager
def get_connection(db_path: Path | None = None):
    """Context manager for SQLite connection with row factory."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Create all tables and indexes if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
    logger.info("Database initialised at %s", db_path or get_db_path())


# ── Upsert helpers ────────────────────────────────────────────────────────────

def upsert_daily_prices(prices: list[DailyPrice]) -> int:
    if not prices:
        return 0
    sql = """
        INSERT INTO daily_prices (ticker, market, date, open, high, low, close, volume, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            close=excluded.close, open=excluded.open, high=excluded.high,
            low=excluded.low, volume=excluded.volume, source=excluded.source,
            updated_at=datetime('now')
    """
    rows = [
        (p.ticker, p.market, str(p.date), p.open, p.high, p.low,
         p.close, p.volume, p.source)
        for p in prices
    ]
    with get_connection() as conn:
        conn.executemany(sql, rows)
    return len(rows)


def upsert_income_statements(stmts: list[IncomeStatement]) -> int:
    if not stmts:
        return 0
    sql = """
        INSERT INTO income_statements
            (ticker, period_end_date, period_type, revenue, cost_of_revenue, gross_profit,
             operating_income, net_income, ebitda, eps, eps_diluted, shares_outstanding, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, period_end_date, period_type) DO UPDATE SET
            revenue=excluded.revenue, net_income=excluded.net_income,
            operating_income=excluded.operating_income, ebitda=excluded.ebitda,
            eps=excluded.eps, source=excluded.source, updated_at=datetime('now')
    """
    rows = [
        (s.ticker, str(s.period_end_date), s.period_type, s.revenue, s.cost_of_revenue,
         s.gross_profit, s.operating_income, s.net_income, s.ebitda,
         s.eps, s.eps_diluted, s.shares_outstanding, s.source)
        for s in stmts
    ]
    with get_connection() as conn:
        conn.executemany(sql, rows)
    return len(rows)


def upsert_balance_sheets(sheets: list[BalanceSheet]) -> int:
    if not sheets:
        return 0
    sql = """
        INSERT INTO balance_sheets
            (ticker, period_end_date, period_type, total_assets, total_liabilities, total_equity,
             current_assets, current_liabilities, cash_and_equivalents, total_debt,
             book_value_per_share, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, period_end_date, period_type) DO UPDATE SET
            total_assets=excluded.total_assets, total_equity=excluded.total_equity,
            total_liabilities=excluded.total_liabilities, source=excluded.source,
            updated_at=datetime('now')
    """
    rows = [
        (s.ticker, str(s.period_end_date), s.period_type, s.total_assets, s.total_liabilities,
         s.total_equity, s.current_assets, s.current_liabilities, s.cash_and_equivalents,
         s.total_debt, s.book_value_per_share, s.source)
        for s in sheets
    ]
    with get_connection() as conn:
        conn.executemany(sql, rows)
    return len(rows)


def upsert_cash_flows(flows: list[CashFlow]) -> int:
    if not flows:
        return 0
    sql = """
        INSERT INTO cash_flows
            (ticker, period_end_date, period_type, operating_cash_flow, capital_expenditure,
             free_cash_flow, dividends_paid, depreciation, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, period_end_date, period_type) DO UPDATE SET
            operating_cash_flow=excluded.operating_cash_flow,
            free_cash_flow=excluded.free_cash_flow, source=excluded.source,
            updated_at=datetime('now')
    """
    rows = [
        (f.ticker, str(f.period_end_date), f.period_type, f.operating_cash_flow,
         f.capital_expenditure, f.free_cash_flow, f.dividends_paid, f.depreciation, f.source)
        for f in flows
    ]
    with get_connection() as conn:
        conn.executemany(sql, rows)
    return len(rows)


def upsert_financial_metrics(metrics: list[FinancialMetrics]) -> int:
    if not metrics:
        return 0
    sql = """
        INSERT INTO financial_metrics
            (ticker, date, pe_ratio, pb_ratio, ps_ratio, roe, roa, debt_to_equity,
             current_ratio, dividend_yield, operating_margin, gross_margin, revenue_growth,
             net_income_growth, fcf_per_share, market_cap, enterprise_value, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            pe_ratio=excluded.pe_ratio, roe=excluded.roe, roa=excluded.roa,
            operating_margin=excluded.operating_margin, gross_margin=excluded.gross_margin,
            revenue_growth=excluded.revenue_growth, net_income_growth=excluded.net_income_growth,
            source=excluded.source, updated_at=datetime('now')
    """
    rows = [
        (m.ticker, str(m.date), m.pe_ratio, m.pb_ratio, m.ps_ratio, m.roe, m.roa,
         m.debt_to_equity, m.current_ratio, m.dividend_yield, m.operating_margin,
         m.gross_margin, m.revenue_growth, m.net_income_growth, m.fcf_per_share,
         m.market_cap, m.enterprise_value, m.source)
        for m in metrics
    ]
    with get_connection() as conn:
        conn.executemany(sql, rows)
    return len(rows)


def upsert_manual_doc(doc: ManualDoc) -> None:
    sql = """
        INSERT INTO manual_docs (ticker, file_name, file_path, doc_type, extracted_text,
                                  text_length, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, file_name) DO UPDATE SET
            extracted_text=excluded.extracted_text, status=excluded.status,
            text_length=excluded.text_length
    """
    with get_connection() as conn:
        conn.execute(sql, (doc.ticker, doc.file_name, doc.file_path, doc.doc_type,
                           doc.extracted_text, doc.text_length, doc.status))


def insert_agent_signal(signal: AgentSignal) -> None:
    sql = """
        INSERT INTO agent_signals (ticker, agent_name, signal, confidence, reasoning, metrics_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    with get_connection() as conn:
        conn.execute(sql, (signal.ticker, signal.agent_name, signal.signal,
                           signal.confidence, signal.reasoning,
                           json.dumps(signal.metrics, ensure_ascii=False)))


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_latest_prices(ticker: str, limit: int = 252) -> list[dict]:
    sql = "SELECT * FROM daily_prices WHERE ticker=? ORDER BY date DESC LIMIT ?"
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker, limit)).fetchall()
    return [dict(r) for r in rows]


def get_income_statements(ticker: str, limit: int = 10,
                           period_type: str = "annual") -> list[dict]:
    # Deduplicate logic depends on period_type:
    # - annual: Keep latest period_end_date per year (handles duplicate annual reports)
    # - quarterly: Keep latest per year-quarter (preserves Q1-Q4)
    if period_type == "annual":
        partition_key = "strftime('%Y', period_end_date)"
    else:  # quarterly
        partition_key = "strftime('%Y-%m', period_end_date)"

    sql = f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {partition_key}
                       ORDER BY period_end_date DESC
                   ) as rn
            FROM income_statements
            WHERE ticker=? AND period_type=?
        )
        SELECT * FROM ranked WHERE rn=1
        ORDER BY period_end_date DESC LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker, period_type, limit)).fetchall()
    return [dict(r) for r in rows]


def get_balance_sheets(ticker: str, limit: int = 10,
                        period_type: str = "annual") -> list[dict]:
    # Deduplicate logic depends on period_type
    if period_type == "annual":
        partition_key = "strftime('%Y', period_end_date)"
    else:  # quarterly
        partition_key = "strftime('%Y-%m', period_end_date)"

    sql = f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {partition_key}
                       ORDER BY period_end_date DESC
                   ) as rn
            FROM balance_sheets
            WHERE ticker=? AND period_type=?
        )
        SELECT * FROM ranked WHERE rn=1
        ORDER BY period_end_date DESC LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker, period_type, limit)).fetchall()
    return [dict(r) for r in rows]


def get_cash_flows(ticker: str, limit: int = 10,
                   period_type: str = "annual") -> list[dict]:
    # Deduplicate logic depends on period_type
    if period_type == "annual":
        partition_key = "strftime('%Y', period_end_date)"
    else:  # quarterly
        partition_key = "strftime('%Y-%m', period_end_date)"

    # Deduplicate by year/quarter: keep latest period_end_date per period
    sql = f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {partition_key}
                       ORDER BY period_end_date DESC
                   ) as rn
            FROM cash_flows
            WHERE ticker=? AND period_type=?
        )
        SELECT * FROM ranked WHERE rn=1
        ORDER BY period_end_date DESC LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker, period_type, limit)).fetchall()
    return [dict(r) for r in rows]


def get_financial_metrics(ticker: str, limit: int = 10) -> list[dict]:
    sql = "SELECT * FROM financial_metrics WHERE ticker=? ORDER BY date DESC LIMIT ?"
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker, limit)).fetchall()
    return [dict(r) for r in rows]


def get_manual_docs(ticker: str) -> list[dict]:
    """Return all successfully parsed manual docs for a ticker."""
    sql = """SELECT * FROM manual_docs WHERE ticker=? AND status IN ('success', 'partial')
             ORDER BY created_at DESC"""
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker,)).fetchall()
    return [dict(r) for r in rows]


def get_latest_agent_signals(ticker: str, days: int = 7) -> list[dict]:
    sql = """SELECT * FROM agent_signals WHERE ticker=?
             AND created_at >= datetime('now', ?)
             ORDER BY created_at DESC"""
    with get_connection() as conn:
        rows = conn.execute(sql, (ticker, f"-{days} days")).fetchall()
    return [dict(r) for r in rows]
