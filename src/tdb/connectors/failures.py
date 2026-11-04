"""
The audit `reason` for a query that failed once TDB tried to run it.

A failed query is an event an audit trail is asked about, like a refused one, so each
path records it. The error text stays in the application log: it can quote the data.
"""

from __future__ import annotations

import duckdb

from tdb.connectors.base import QueryError
from tdb.connectors.csv import SqlFileAccessError, SqlRefusedError


def failure_reason(exc: BaseException) -> str:
    if isinstance(exc, SqlRefusedError):
        return "sql_validation_failed"
    if isinstance(exc, SqlFileAccessError):
        return "sql_file_access"
    if isinstance(exc, PermissionError):
        return "path_outside_allowed_dir"
    # A schema lookup that finds no file raises DuckDB's IOException.
    if isinstance(exc, FileNotFoundError | duckdb.IOException):
        return "source_unavailable"
    if isinstance(exc, QueryError):
        return "query_failed"
    if isinstance(exc, ValueError):
        return "invalid_arguments"
    return "internal_error"
