"""
TDB – CSV Connector

Uses DuckDB to run SQL directly against CSV files.
DuckDB loads the file into an in-process analytical engine – no server needed.

Connection config expected:
    {
        "file_path": "/absolute/or/relative/path/to/file.csv"
    }

The connector exposes the CSV as a table called `data`.
Users can also use the source's registered name as the table name —
we rewrite the SQL before execution.

Day-N upgrade ideas:
  - Support glob patterns  (e.g. /data/sales_*.csv)
  - Support gzipped CSVs
  - Cache the DuckDB relation for hot sources
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import duckdb

from tdb.config import get_allowed_data_dir
from tdb.connectors.base import BaseConnector, ConnectorResult


@dataclass
class CsvConnector(BaseConnector):
    """Read-only SQL access to a CSV file via DuckDB."""

    connection: dict[str, Any]
    _file_path: str = field(init=False)

    def __post_init__(self) -> None:
        fp = self.connection.get("file_path")
        if not fp:
            raise ValueError("CSV connector requires 'file_path' in connection config.")
        self._file_path = str(fp)

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def validate_connection(self) -> bool:
        """Return True if the CSV file exists and is readable."""
        return os.path.isfile(self._file_path) and os.access(self._file_path, os.R_OK)

    def path_is_allowed(self) -> bool:
        """
        Return True if ``file_path`` is within ``TDB_ALLOWED_DATA_DIR``.

        When that variable is unset, all paths are allowed (opt-in confinement).
        Symlinks and ``..`` are resolved before the comparison so they cannot be
        used to escape the allowed directory.
        """
        allowed = get_allowed_data_dir()
        if not allowed:
            return True
        allowed_real = os.path.realpath(allowed)
        target_real = os.path.realpath(self._file_path)
        return target_real == allowed_real or target_real.startswith(
            allowed_real + os.sep
        )

    def get_schema(self) -> dict[str, str]:
        """
        Return column-name → DuckDB type mapping.
        Example: {"id": "BIGINT", "name": "VARCHAR", "price": "DOUBLE"}
        """
        if not self.path_is_allowed():
            raise PermissionError("file_path is outside the allowed data directory")
        conn = duckdb.connect(":memory:")
        try:
            rel = conn.read_csv(self._file_path)
            return {col: str(dtype) for col, dtype in zip(rel.columns, rel.dtypes)}
        finally:
            conn.close()

    def execute(self, sql: str, limit: int = 100) -> ConnectorResult:
        """
        Run a SQL SELECT against the CSV.
        The table name `data` (or any alias) is mapped to the actual file.
        A LIMIT clause is injected if missing.
        """
        if not self.path_is_allowed():
            raise PermissionError("file_path is outside the allowed data directory")
        if not self.validate_connection():
            raise FileNotFoundError(
                f"CSV file not found or not readable: {self._file_path}"
            )

        sql_to_run = _inject_limit(sql, limit)

        conn = duckdb.connect(":memory:")
        try:
            # Register the CSV as a virtual table called 'data' via the
            # DuckDB relation API — no SQL string interpolation needed.
            conn.register("data", conn.read_csv(self._file_path))
            cursor = conn.execute(sql_to_run)
            columns = [desc[0] for desc in cursor.description]
            rows_raw = cursor.fetchall()
        finally:
            conn.close()

        # Always enforce the row cap, even when the SQL carries its own LIMIT.
        # The community edition guarantees "max `limit` rows per response" — a
        # user-supplied `LIMIT 99999` must never exceed it. _inject_limit only
        # adds a LIMIT when one is absent, so this slice is the real ceiling.
        truncated = len(rows_raw) > limit
        rows_raw = rows_raw[:limit]

        rows = [dict(zip(columns, row)) for row in rows_raw]
        return ConnectorResult(columns=columns, rows=rows, truncated=truncated)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_limit(sql: str, limit: int) -> str:
    """
    Append LIMIT <n> if the query does not already contain a LIMIT clause.
    This is a simple heuristic – good enough for Day-3.
    """
    normalised = sql.strip().upper()
    if "LIMIT" not in normalised:
        return f"{sql.strip()} LIMIT {limit}"
    return sql
