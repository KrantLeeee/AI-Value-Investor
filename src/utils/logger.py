"""Structured logging utilities."""

import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

_console = Console(stderr=True)


def get_logger(name: str) -> logging.Logger:
    """Return a Rich-formatted logger for the given module name."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(
            console=_console,
            show_time=True,
            show_path=False,
            markup=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(
    event_name: str,
    fields: dict[str, Any],
    level: str = "info",
) -> None:
    """Append a structured log event to output/logs/events.jsonl."""
    import json
    from datetime import datetime

    from src.utils.config import get_output_dir

    log_dir = get_output_dir("logs")
    log_file = log_dir / "events.jsonl"
    entry = {"event": event_name, "ts": datetime.now().isoformat(), **fields}
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
