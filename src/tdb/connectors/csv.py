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

Queries run against one process-wide DuckDB engine (see `_engine`) rather than a
fresh in-memory instance per query. Building and tearing down an instance cost
70-130 ms whatever the file size, and — because each instance claims a thread per
core — concurrent queries oversubscribed the CPU badly enough that throughput
*fell* as load rose. See `_engine` for the measurements.

Day-N upgrade ideas:
  - Support glob patterns  (e.g. /data/sales_*.csv)
  - Support gzipped CSVs
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

import duckdb

from tdb.config import get_allowed_data_dir
from tdb.connectors.base import BaseConnector, ConnectorResult

_ENGINE: duckdb.DuckDBPyConnection | None = None
_ENGINE_LOCK = threading.Lock()


def _engine() -> duckdb.DuckDBPyConnection:
    """
    The process-wide DuckDB instance every CSV query runs on.

    Previously each query opened `duckdb.connect(":memory:")` and closed it
    again. That was expensive in two compounding ways, measured on a 5.4 MB
    100k-row CSV:

    - **Instance lifecycle, 70-130 ms per query regardless of file size.**
      On a 1,000-row CSV the connect/close pair was more than half the total
      time — longer spent building an engine than using it.
    - **Thread oversubscription under concurrency.** DuckDB claims one thread
      per core by default, and every concurrent query had its *own* instance
      doing so. At 16 concurrent queries on a 6-core host that is 96 threads
      competing for 6 cores: p50 went 228 ms -> 50.9 s and throughput *fell*
      from 4.29 to 0.35 req/s. Load made the server slower in absolute terms.

    One shared instance fixes both: 105 ms at 1 worker (from 228 ms) and
    28 req/s at 16 workers (from 0.35), with total memory flat at ~5 MB
    instead of ~5 MB per in-flight query.

    **One engine is enough for every source.** Each query registers its file on
    its own cursor, and cursor registrations are isolated — two sources can both
    call their table `data` concurrently without seeing each other. So there is
    no per-source cache to bound and nothing that grows with the number of
    registered sources.

    Nothing is cached *about the file itself*: the registration is a lazy view
    over `read_csv`, re-bound per query, so appended rows and added or removed
    columns are all picked up. Materialising the CSV into a table would be
    another ~6x faster and is deliberately not done — it would hold the file in
    RAM at ~2.3x its size, against the rule that the row cap must bound memory
    and not merely the response, and it goes stale on edits.
    """
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = duckdb.connect(":memory:")
        return _ENGINE


def close_engine() -> None:
    """Close the shared engine. Called at shutdown, and between tests."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE.close()
            _ENGINE = None


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
        cur = _engine().cursor()
        try:
            rel = cur.read_csv(self._file_path)
            return {col: str(dtype) for col, dtype in zip(rel.columns, rel.dtypes)}
        finally:
            cur.close()

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

        # A cursor on the shared engine, not a new engine. The registration is
        # cursor-local, so concurrent queries against different sources can each
        # call their own file 'data' without colliding.
        cur = _engine().cursor()
        try:
            # Register the CSV as a virtual table called 'data' via the
            # DuckDB relation API — no SQL string interpolation needed.
            cur.register("data", cur.read_csv(self._file_path))
            cursor = cur.execute(sql_to_run)
            columns = [desc[0] for desc in cursor.description]
            # fetchmany, not fetchall: the community edition guarantees "max
            # `limit` rows per response", and _inject_limit only adds a LIMIT
            # when the token is absent — so a user-supplied `LIMIT 99999`, or a
            # query that merely contains the word, would otherwise turn every
            # row the query produced into Python objects before being sliced.
            rows_raw = cursor.fetchmany(limit + 1)
        finally:
            cur.close()

        # One row beyond the ceiling is what distinguishes a cut result from a
        # complete one.
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
