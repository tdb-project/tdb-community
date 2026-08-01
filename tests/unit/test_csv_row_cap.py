"""
The row cap must bound the fetch, not just the response.

`_inject_limit` appends `LIMIT <n>` only when the statement does not already
contain the token, and that check is a substring test over the whole
uppercased SQL. So both of these reach DuckDB uncapped:

    SELECT * FROM data LIMIT 99999          -- caller asked for more
    SELECT * FROM data WHERE note = 'no limit'  -- caller asked for nothing

Slicing the rows afterwards makes the *response* correct, which is what the
API tests in test_persistence_mcp.py already assert. It does nothing about the
rows the connector materialised to produce that response — on this harness,
30.9 MB of Python objects for a query that returns ten rows. `execute()`
therefore reads at most `limit + 1` rows, and the extra one is what marks the
result truncated.
"""

from __future__ import annotations

import csv
import tracemalloc
from pathlib import Path

from tdb.connectors.csv import CsvConnector

_LARGE = 200_000


def _csv(tmp_path: Path, n_rows: int) -> str:
    p = tmp_path / "big.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name"])
        for i in range(n_rows):
            w.writerow([i, f"row-{i}"])
    return str(p)


def _connector(path: str) -> CsvConnector:
    return CsvConnector(connection={"file_path": path})


def test_caller_supplied_limit_does_not_escape_the_cap(tmp_path: Path) -> None:
    result = _connector(_csv(tmp_path, 5000)).execute(
        "SELECT * FROM data LIMIT 99999", limit=10
    )
    assert len(result.rows) == 10
    assert result.truncated is True


def test_query_merely_mentioning_the_word_is_still_capped(tmp_path: Path) -> None:
    result = _connector(_csv(tmp_path, 5000)).execute(
        "SELECT * FROM data WHERE name != 'no limit'", limit=10
    )
    assert len(result.rows) == 10
    assert result.truncated is True


def test_exactly_at_the_cap_is_not_marked_truncated(tmp_path: Path) -> None:
    """Off-by-one guard: n == limit is a complete result, not a cut one."""
    result = _connector(_csv(tmp_path, 10)).execute("SELECT * FROM data", limit=10)
    assert len(result.rows) == 10
    assert result.truncated is False


def test_the_fetch_itself_is_bounded(tmp_path: Path) -> None:
    """
    Measured on this harness: 0.01 MB bounded, 30.9 MB with the old
    fetchall(). The threshold clears both by a wide margin, so it fails on a
    regression rather than on allocator noise.
    """
    path = _csv(tmp_path, _LARGE)

    tracemalloc.start()
    try:
        result = _connector(path).execute(
            f"SELECT * FROM data LIMIT {_LARGE}", limit=10
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(result.rows) == 10
    assert result.truncated is True
    assert peak < 5_000_000, f"materialised the whole result set: {peak / 1e6:.1f} MB"
