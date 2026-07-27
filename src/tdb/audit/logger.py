from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from tdb.config import get_log_file


def get_logger(name: str) -> logging.Logger:
    """Return a standard Python logger with the given name."""
    return logging.getLogger(name)


def _append(entry: dict) -> None:
    with open(get_log_file(), "a") as f:
        f.write(json.dumps(entry) + "\n")


def log_query(source_id: str, sql: str, rows_returned: int, key_hint: str = "") -> None:
    _append(
        {
            "event": "query",
            "source_id": source_id,
            "sql": sql,
            "rows_returned": rows_returned,
            "key_hint": key_hint,
            "ts": datetime.now(UTC).isoformat(),
        }
    )


def log_denial(
    action: str,
    reason: str,
    source_id: str = "",
    sql: str = "",
    key_hint: str = "",
) -> None:
    """Record an attempt that was refused — auth, authorization, or validation.

    Denials are audit events in their own right: a reviewer asking "who tried
    what and was turned away" is asking this file, not the app log.
    """
    _append(
        {
            "event": "denied",
            "action": action,
            "reason": reason,
            "source_id": source_id,
            "sql": sql,
            "key_hint": key_hint,
            "ts": datetime.now(UTC).isoformat(),
        }
    )
