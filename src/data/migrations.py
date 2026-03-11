"""Database migrations for schema updates."""

import sqlite3
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)


def migrate_v2_0_0(db_path: Path) -> None:
    """
    Migrate database to v2.0.0 schema.

    Adds: roic, rd_expense_ratio, receivables_turnover_days, gross_margin columns.
    Idempotent: Safe to run multiple times.
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("PRAGMA table_info(financial_metrics)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("gross_margin", "REAL"),
        ("roic", "REAL"),
        ("rd_expense_ratio", "REAL"),
        ("receivables_turnover_days", "REAL"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            conn.execute(f"ALTER TABLE financial_metrics ADD COLUMN {col_name} {col_type}")
            logger.info("Added column %s to financial_metrics", col_name)

    conn.commit()
    conn.close()


def run_all_migrations(db_path: Path) -> None:
    """Run all migrations in order."""
    migrate_v2_0_0(db_path)
    logger.info("All migrations completed for %s", db_path)
