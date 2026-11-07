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

Queries run on a DuckDB engine kept open for the file (see `_engine`) rather than a
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
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import duckdb

from tdb.config import get_allowed_data_dir
from tdb.connectors.base import BaseConnector, ConnectorResult, QueryError
from tdb.engine.validator import _mask_noncode, strip_trailing_semicolon

# One open engine holds about 5 MB and a thread, so idle ones are closed past this.
# A query that has to reopen one pays about 26 ms.
_MAX_ENGINES = 32


@dataclass
class _Slot:
    connection: duckdb.DuckDBPyConnection
    users: int = 0


_ENGINES: OrderedDict[str, _Slot] = OrderedDict()
_ENGINE_LOCK = threading.Lock()


class SqlFileAccessError(PermissionError):
    """SQL named a file other than the source's own."""


class SqlRefusedError(ValueError):
    """DuckDB's own parser read the SQL as something other than one SELECT."""


def _open_engine(path: str) -> duckdb.DuckDBPyConnection:
    """
    A DuckDB instance that can read *path* and no other file.

    DuckDB resolves paths written inside the SQL — `read_csv(...)`, `read_text(...)`,
    `glob(...)` — not only the file TDB registers, so `path_is_allowed()` alone confines
    registration and nothing else. External access is therefore off except for this one
    file, extension autoloading is off, and the configuration is locked, so a `SET` in a
    query cannot undo any of it. The order of the `SET`s matters: DuckDB refuses to
    change the allowed lists once external access is disabled.

    It used to allow the whole data directory, so a source could read every other file
    mounted there. An independent review found it; the file reads were reproduced on the
    published image (tdb-internal/spikes/finding2-file-engine).
    """
    engine = duckdb.connect(":memory:")
    try:
        engine.execute("SET allowed_paths = ?", [[path]])
        engine.execute("SET allowed_directories = []")
        engine.execute("SET autoinstall_known_extensions = false")
        engine.execute("SET autoload_known_extensions = false")
        engine.execute("SET enable_external_access = false")
        engine.execute("SET lock_configuration = true")
    except BaseException:
        engine.close()
        raise
    return engine


def _close_idle_engines() -> None:
    """Oldest first, down to the cap. Call with _ENGINE_LOCK held."""
    for path in [p for p, slot in _ENGINES.items() if slot.users == 0]:
        if len(_ENGINES) <= _MAX_ENGINES:
            return
        _ENGINES.pop(path).connection.close()


@contextmanager
def _engine(file_path: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """
    The DuckDB instance this file's queries run on, kept open between them.

    Opening one for each query was expensive in two compounding ways, measured on a
    5.4 MB 100k-row CSV:

    - **Instance lifecycle, 70-130 ms per query regardless of file size.**
    - **Thread oversubscription under concurrency.** DuckDB claims one thread per core
      by default, and every concurrent query had its *own* instance doing so. At 16
      concurrent queries on a 6-core host, p50 went 228 ms -> 50.9 s and throughput
      *fell* from 4.29 to 0.35 req/s. Load made the server slower in absolute terms.

    Keeping an instance per file fixes both. This edition has one source, so one engine
    at a time claims the threads.

    Nothing is cached *about the file itself*: the registration is a lazy view over
    `read_csv`, re-bound per query, so appended rows and added or removed columns are
    all picked up. Materialising the CSV into a table would be another ~6x faster and is
    deliberately not done — it would hold the file in RAM at ~2.3x its size, against the
    rule that the row cap must bound memory and not merely the response, and it goes
    stale on edits.
    """
    path = os.path.realpath(file_path)
    with _ENGINE_LOCK:
        slot = _ENGINES.get(path)
        if slot is None:
            slot = _ENGINES[path] = _Slot(_open_engine(path))
        _ENGINES.move_to_end(path)
        slot.users += 1
    try:
        yield slot.connection
    finally:
        with _ENGINE_LOCK:
            slot.users -= 1
            _close_idle_engines()


def close_engine() -> None:
    """Close every engine. Called at shutdown, and between tests."""
    with _ENGINE_LOCK:
        for slot in _ENGINES.values():
            slot.connection.close()
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

    @contextmanager
    def _cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A cursor on this file's engine."""
        with _engine(self._file_path) as engine:
            cur = engine.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def get_schema(self) -> dict[str, str]:
        """
        Return column-name → DuckDB type mapping.
        Example: {"id": "BIGINT", "name": "VARCHAR", "price": "DOUBLE"}
        """
        if not self.path_is_allowed():
            raise PermissionError("file_path is outside the allowed data directory")
        with self._cursor() as cur:
            rel = cur.read_csv(os.path.realpath(self._file_path))
            return {col: str(dtype) for col, dtype in zip(rel.columns, rel.dtypes)}

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

        with self._cursor() as cur:
            try:
                # Register the CSV as a virtual table called 'data' via the
                # DuckDB relation API — no SQL string interpolation needed.
                cur.register("data", cur.read_csv(os.path.realpath(self._file_path)))
                # The engine's parser has the last word on what will run. validate_sql()
                # scans text, and a text scanner that misjudges where a literal ends
                # can be shown one statement while DuckDB executes several — every
                # statement in the string, returning only the last result.
                statements = cur.extract_statements(sql_to_run)
                if len(statements) != 1 or (
                    statements[0].type != duckdb.StatementType.SELECT
                ):
                    raise SqlRefusedError("Only a single SELECT statement is allowed.")
                try:
                    cursor = cur.execute(sql_to_run)
                except duckdb.PermissionException as exc:
                    raise SqlFileAccessError(
                        "SQL may only read this source's own file."
                    ) from exc
                columns = [desc[0] for desc in cursor.description]
                # fetchmany, not fetchall: the community edition guarantees "max
                # `limit` rows per response", and _inject_limit only adds a LIMIT
                # when the token is absent — so a user-supplied `LIMIT 99999`, or a
                # query that merely contains the word, would otherwise turn every
                # row the query produced into Python objects before being sliced.
                rows_raw = cursor.fetchmany(limit + 1)
            except (duckdb.ProgrammingError, duckdb.DataError) as exc:
                # Parser, binder and catalog errors, and casts that fail on a row (which
                # surface during the fetch). File-access refusals are not among them.
                raise QueryError(str(exc)) from exc

        # One row beyond the ceiling is what distinguishes a cut result from a
        # complete one.
        truncated = len(rows_raw) > limit
        rows_raw = rows_raw[:limit]

        rows = [dict(zip(columns, row)) for row in rows_raw]
        return ConnectorResult(columns=columns, rows=rows, truncated=truncated)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_LIMIT_CLAUSE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def _inject_limit(sql: str, limit: int) -> str:
    """
    Append LIMIT <n> if the query does not already contain a LIMIT clause.
    Read on the masked text, so `credit_limit`, `'no limit'` or `-- limit` is
    not taken for one.
    """
    if not _LIMIT_CLAUSE.search(_mask_noncode(sql)):
        # On its own line: a trailing `-- comment` would otherwise swallow it.
        return f"{sql.strip()}\nLIMIT {limit}"
    return sql
