from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from tdb.config import get_log_file


def get_logger(name: str) -> logging.Logger:
    """Return a standard Python logger with the given name."""
    return logging.getLogger(name)


def log_query(source_id: str, sql: str, rows_returned: int, key_hint: str = "") -> None:
    entry = {
        "event": "query",
        "source_id": source_id,
        "sql": sql,
        "rows_returned": rows_returned,
        "key_hint": key_hint,
        "ts": datetime.now(UTC).isoformat(),
    }
    with open(get_log_file(), "a") as f:
        f.write(json.dumps(entry) + "\n")
