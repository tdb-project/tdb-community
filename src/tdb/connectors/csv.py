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

Queries run against a shared DuckDB engine (see `_engine`) rather than a fresh
in-memory instance per query. Building and tearing down an instance cost
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
from tdb.engine.validator import strip_trailing_semicolon

_ENGINES: dict[str, duckdb.DuckDBPyConnection] = {}
_ENGINE_LOCK = threading.Lock()


class SqlFileAccessError(PermissionError):
    """SQL named a file outside the source's data directory."""


def _data_root(file_path: str) -> str:
    """
    The directory SQL on this source may read files from: `TDB_ALLOWED_DATA_DIR`
    when set, otherwise the directory holding the registered CSV.
    """
    allowed = get_allowed_data_dir()
    root = allowed if allowed else os.path.dirname(os.path.realpath(file_path))
    return os.path.realpath(root)


def _engine(root: str) -> duckdb.DuckDBPyConnection:
    """
    The DuckDB instance every CSV query under *root* runs on.

    **It can reach no file outside *root*.** DuckDB resolves paths written
    inside the SQL — `read_csv('/etc/passwd')`, `read_text(...)` — not only the
    file TDB registers, so `path_is_allowed()` alone confined registration and
    nothing else: any SELECT could read any file the process could. External
    access is therefore off except for *root*, extension autoloading is off, and
    the configuration is locked, so a `SET` in a query cannot undo any of it.
    The order of the `SET`s matters: DuckDB refuses to change
    `allowed_directories` once external access is disabled.

    One engine per root rather than one per process: the allowed directory is
    fixed when the engine is locked. In Docker `TDB_ALLOWED_DATA_DIR` is set, so
    that is still exactly one engine.

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

    **One engine is enough for every source under a root.** Each query registers
    its file on its own cursor, and cursor registrations are isolated — two
    sources can both call their table `data` concurrently without seeing each
    other. So there is no per-source cache to bound, and nothing grows with the
    number of registered sources.

    Nothing is cached *about the file itself*: the registration is a lazy view
    over `read_csv`, re-bound per query, so appended rows and added or removed
    columns are all picked up. Materialising the CSV into a table would be
    another ~6x faster and is deliberately not done — it would hold the file in
    RAM at ~2.3x its size, against the rule that the row cap must bound memory
    and not merely the response, and it goes stale on edits.
    """
    with _ENGINE_LOCK:
        engine = _ENGINES.get(root)
        if engine is None:
            engine = duckdb.connect(":memory:")
            engine.execute("SET allowed_directories = ?", [[root + os.sep]])
            engine.execute("SET autoinstall_known_extensions = false")
            engine.execute("SET autoload_known_extensions = false")
            engine.execute("SET enable_external_access = false")
            engine.execute("SET lock_configuration = true")
            _ENGINES[root] = engine
        return engine


def close_engine() -> None:
    """Close every engine. Called at shutdown, and between tests."""
    with _ENGINE_LOCK:
        for engine in _ENGINES.values():
            engine.close()
        _ENGINES.clear()


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
        cur = _engine(_data_root(self._file_path)).cursor()
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

        # limit + 1, not limit: the fetch below reads one row beyond the
        # ceiling to set `truncated`, so the injected LIMIT must let that
        # sentinel row through. Injecting exactly `limit` capped the source at
        # the ceiling and made `truncated` unreachable on this path — a 5-row
        # table queried with limit=2 returned 2 rows and truncated:false,
        # while the docs promise the flag means "you got everything".
        sql_to_run = _inject_limit(strip_trailing_semicolon(sql), limit + 1)

        # A cursor on the shared engine, not a new engine. The registration is
        # cursor-local, so concurrent queries against different sources can each
        # call their own file 'data' without colliding.
        cur = _engine(_data_root(self._file_path)).cursor()
        try:
            # Register the CSV as a virtual table called 'data' via the
            # DuckDB relation API — no SQL string interpolation needed.
            cur.register("data", cur.read_csv(self._file_path))
            try:
                cursor = cur.execute(sql_to_run)
            except duckdb.PermissionException as exc:
                raise SqlFileAccessError(
                    "SQL may only read files in the source's data directory."
                ) from exc
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
