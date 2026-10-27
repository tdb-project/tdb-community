from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

from tdb.config import get_log_file

# Which MCP client wrote an entry, and at what protocol version: set by the MCP
# endpoint for the request it serves, unset on every other path.
mcp_caller: ContextVar[dict | None] = ContextVar("mcp_caller", default=None)


# TDB's own log lines carry caller-supplied text (source names, SQL fragments,
# driver errors). The handlers are plain text and not ours to choose, so a value
# with a line break could forge a log line. Escaping at record creation covers
# every logger, handler and call site; tracebacks (exc_info) are left as they are.
def _escape_line_breaks(value: str) -> str:
    return value.replace("\r", "\\r").replace("\n", "\\n")


def _escaped(value: object) -> object:
    if isinstance(value, str):
        return _escape_line_breaks(value)
    if isinstance(value, BaseException):
        return _escape_line_breaks(str(value))
    return value


def _escaping_record_factory(base):
    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        if record.name == "tdb" or record.name.startswith(("tdb.", "tdb_enterprise")):
            record.msg = _escaped(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_escaped(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _escaped(v) for k, v in record.args.items()}
        return record

    factory._tdb_escapes_line_breaks = True
    return factory


# Tests reload every tdb module; wrap the factory once, not once per reload.
if not getattr(logging.getLogRecordFactory(), "_tdb_escapes_line_breaks", False):
    logging.setLogRecordFactory(_escaping_record_factory(logging.getLogRecordFactory()))


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
            **(mcp_caller.get() or {}),
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
            **(mcp_caller.get() or {}),
            "ts": datetime.now(UTC).isoformat(),
        }
    )
