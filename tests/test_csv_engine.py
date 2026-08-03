"""
The shared DuckDB engine behind the CSV connector.

The point of the change is performance, but the properties worth protecting are
correctness ones: sharing an engine across queries must not let sources see each
other's data, and must not start caching the file's contents.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tdb.connectors import csv as csv_mod
from tdb.connectors.csv import CsvConnector


@pytest.fixture(autouse=True)
def _fresh_engine() -> Any:
    csv_mod.close_engine()
    yield
    csv_mod.close_engine()


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


class TestEngineLifecycle:
    def test_one_engine_is_reused_across_queries(self, tmp_path: Path) -> None:
        src = _write(tmp_path / "a.csv", "id,v\n1,x\n2,y\n")
        conn = CsvConnector({"file_path": str(src)})
        conn.execute("SELECT * FROM data", limit=10)
        first = csv_mod._ENGINE
        conn.execute("SELECT * FROM data", limit=10)
        assert csv_mod._ENGINE is first is not None

    def test_one_engine_serves_different_sources(self, tmp_path: Path) -> None:
        """No per-source cache — so nothing grows with the number of sources."""
        a = _write(tmp_path / "a.csv", "id,v\n1,x\n")
        b = _write(tmp_path / "b.csv", "id,v\n1,y\n2,z\n")
        CsvConnector({"file_path": str(a)}).execute("SELECT * FROM data", limit=10)
        engine_after_a = csv_mod._ENGINE
        CsvConnector({"file_path": str(b)}).execute("SELECT * FROM data", limit=10)
        assert csv_mod._ENGINE is engine_after_a

    def test_close_engine_is_idempotent(self) -> None:
        csv_mod.close_engine()
        csv_mod.close_engine()
        assert csv_mod._ENGINE is None

    def test_engine_is_rebuilt_after_close(self, tmp_path: Path) -> None:
        src = _write(tmp_path / "a.csv", "id,v\n1,x\n")
        conn = CsvConnector({"file_path": str(src)})
        conn.execute("SELECT * FROM data", limit=10)
        csv_mod.close_engine()
        result = conn.execute("SELECT * FROM data", limit=10)
        assert result.rows == [{"id": 1, "v": "x"}]


class TestSourcesAreIsolated:
    def test_two_sources_both_called_data_do_not_collide(self, tmp_path: Path) -> None:
        a = _write(tmp_path / "a.csv", "id,who\n1,AAA\n2,AAA\n")
        b = _write(tmp_path / "b.csv", "id,who\n1,BBB\n")
        ca = CsvConnector({"file_path": str(a)})
        cb = CsvConnector({"file_path": str(b)})

        assert len(ca.execute("SELECT * FROM data", limit=10).rows) == 2
        assert len(cb.execute("SELECT * FROM data", limit=10).rows) == 1
        # a second time each, in case the first registration won permanently
        assert ca.execute("SELECT * FROM data", limit=10).rows[0]["who"] == "AAA"
        assert cb.execute("SELECT * FROM data", limit=10).rows[0]["who"] == "BBB"

    def test_concurrent_queries_on_different_sources_stay_isolated(
        self, tmp_path: Path
    ) -> None:
        """
        The registration is cursor-local. If it were engine-global, interleaved
        queries would read each other's file — silently, and only under load.
        """
        a = _write(tmp_path / "a.csv", "id,who\n1,AAA\n2,AAA\n")
        b = _write(tmp_path / "b.csv", "id,who\n1,BBB\n")
        errors: list[str] = []

        def hammer(path: Path, expected: str, rows: int) -> None:
            conn = CsvConnector({"file_path": str(path)})
            try:
                for _ in range(25):
                    result = conn.execute("SELECT * FROM data", limit=10)
                    if len(result.rows) != rows or result.rows[0]["who"] != expected:
                        errors.append(f"{expected} saw {result.rows}")
                        return
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{expected}: {exc}")

        threads = [
            threading.Thread(target=hammer, args=(a, "AAA", 2)),
            threading.Thread(target=hammer, args=(b, "BBB", 1)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestTheFileIsNotCached:
    """
    A shared engine must not become a shared *cache*. Materialising the CSV
    would be faster still and is deliberately not done — see `_engine`.
    """

    def test_appended_rows_are_visible(self, tmp_path: Path) -> None:
        src = _write(tmp_path / "a.csv", "id,v\n1,x\n")
        conn = CsvConnector({"file_path": str(src)})
        assert len(conn.execute("SELECT * FROM data", limit=10).rows) == 1
        _write(src, "id,v\n1,x\n2,y\n3,z\n")
        assert len(conn.execute("SELECT * FROM data", limit=10).rows) == 3

    def test_an_added_column_is_visible(self, tmp_path: Path) -> None:
        src = _write(tmp_path / "a.csv", "id,v\n1,x\n")
        conn = CsvConnector({"file_path": str(src)})
        assert conn.execute("SELECT * FROM data", limit=10).columns == ["id", "v"]
        _write(src, "id,v,extra\n1,x,q\n")
        assert conn.execute("SELECT * FROM data", limit=10).columns == [
            "id",
            "v",
            "extra",
        ]

    def test_schema_reflects_the_current_file(self, tmp_path: Path) -> None:
        src = _write(tmp_path / "a.csv", "id,v\n1,x\n")
        conn = CsvConnector({"file_path": str(src)})
        assert set(conn.get_schema()) == {"id", "v"}
        _write(src, "id,v,extra\n1,x,q\n")
        assert set(conn.get_schema()) == {"id", "v", "extra"}


class TestExistingBehaviourHolds:
    def test_row_cap_still_bounds_the_fetch(self, tmp_path: Path) -> None:
        rows = "\n".join(f"{i},v{i}" for i in range(50))
        src = _write(tmp_path / "a.csv", f"id,v\n{rows}\n")
        result = CsvConnector({"file_path": str(src)}).execute(
            "SELECT * FROM data LIMIT 9999", limit=10
        )
        assert len(result.rows) == 10
        assert result.truncated is True

    def test_missing_file_still_raises(self, tmp_path: Path) -> None:
        conn = CsvConnector({"file_path": str(tmp_path / "nope.csv")})
        with pytest.raises(FileNotFoundError):
            conn.execute("SELECT * FROM data", limit=10)

    def test_confinement_still_refuses_an_outside_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = _write(tmp_path / "secret.csv", "id\n1\n")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(allowed))
        conn = CsvConnector({"file_path": str(outside)})
        with pytest.raises(PermissionError):
            conn.execute("SELECT * FROM data", limit=10)
        with pytest.raises(PermissionError):
            conn.get_schema()
