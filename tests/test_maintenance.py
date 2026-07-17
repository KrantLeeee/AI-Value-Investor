import sqlite3
from datetime import date, timedelta

from src.data import maintenance
from src.data.database import init_db


def _count(db_path, table):
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_safe_maintenance_deletes_only_low_risk_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    old_day = str(date.today() - timedelta(days=45))
    old_price_day = str(date.today() - timedelta(days=3000))

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO agent_signals
              (ticker, agent_name, signal, confidence, created_at)
            VALUES ('000001.SZ', 'valuation', 'neutral', 0.5, ?)
            """,
            (old_day,),
        )
        conn.execute(
            """
            INSERT INTO scan_logs
              (scan_date, tickers_scanned, signals_found, created_at)
            VALUES (?, 1, 0, ?)
            """,
            (old_day, old_day),
        )
        conn.execute(
            """
            INSERT INTO daily_prices
              (ticker, market, date, close, source)
            VALUES ('000001.SZ', 'a_share', ?, 10.0, 'test')
            """,
            (old_price_day,),
        )
        conn.commit()

    monkeypatch.setattr(maintenance, "get_db_path", lambda: db_path)
    monkeypatch.setattr(maintenance, "MAINTENANCE_DIR", tmp_path)
    monkeypatch.setattr(maintenance, "LATEST_MAINTENANCE_PATH", tmp_path / "latest.json")

    dry_run = maintenance.run_database_maintenance(dry_run=True, include_core=False)
    assert dry_run["total_candidates"] == 2
    assert _count(db_path, "agent_signals") == 1
    assert _count(db_path, "scan_logs") == 1

    result = maintenance.run_database_maintenance(dry_run=False, include_core=False)
    assert result["total_deleted"] == 2
    assert _count(db_path, "agent_signals") == 0
    assert _count(db_path, "scan_logs") == 0
    assert _count(db_path, "daily_prices") == 1
