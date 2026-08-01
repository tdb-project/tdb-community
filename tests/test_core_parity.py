"""
Invariants that must hold in BOTH editions of the shared core.

**Counterpart: `tdb-enterprise/tests/test_core_parity.py`. Same IDs, same order.**

Why this file exists rather than a diff: `src/tdb/` is vendored into
tdb-enterprise (ADR-001) and *all fifteen files legitimately differ* — the
enterprise overlay genuinely extends licensing, RBAC, encryption and
multi-source behaviour. A raw diff is therefore useless as a drift signal,
because the real divergence hides inside expected divergence. That is exactly
how the row-cap fix landed here on 2026-06-01 and never reached enterprise
until 2026-07-29 — eight weeks in which the paid tier carried a defect this
edition had already fixed.

So each invariant is asserted **independently in each edition**, never by
comparing source. A missing invariant then shows up as a short list that does
not match, instead of a diff nobody can read.

Adding an invariant here means adding it to the counterpart in the same change.
If an invariant genuinely does not apply to one edition, keep the ID and skip
it with the reason — a silent gap is the thing this file exists to prevent.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.connectors.csv import CsvConnector
from tdb.engine.validator import validate_sql
from tdb.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-key-abc"}


def _csv(tmp_path: Path, n_rows: int) -> str:
    p = tmp_path / "parity.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "note"])
        for i in range(n_rows):
            w.writerow([i, "ok"])
    return str(p)


class TestP1RowCapBoundsTheFetch:
    """P1 — the cap bounds the fetch, not just the response."""

    def test_caller_supplied_limit_cannot_escape_the_cap(self, tmp_path: Path) -> None:
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 500)})
        result = c.execute("SELECT * FROM data LIMIT 99999", limit=10)
        assert len(result.rows) == 10
        assert result.truncated is True

    def test_result_under_the_cap_is_not_marked_truncated(
        self, tmp_path: Path
    ) -> None:
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 3)})
        result = c.execute("SELECT * FROM data", limit=10)
        assert len(result.rows) == 3
        assert result.truncated is False


class TestP2ValidatorReadsCodeNotData:
    """P2 — masking of literals/comments/identifiers, without opening a bypass."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM t WHERE status = 'update pending'",
            "SELECT id FROM t -- delete this later",
            'SELECT "delete" FROM t',
        ],
    )
    def test_keyword_in_data_is_allowed(self, sql: str) -> None:
        assert validate_sql(sql).is_valid

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'a'; DROP TABLE t",
            "SELECT 'abc ; DROP TABLE t",
            "SELECT 1 /*! ; DROP TABLE t */",
            "SELECT a[1; DROP TABLE t]",
        ],
    )
    def test_write_hidden_in_apparent_data_is_refused(self, sql: str) -> None:
        assert not validate_sql(sql).is_valid


class TestP3CsvPathConfinement:
    """P3 — a CSV outside TDB_ALLOWED_DATA_DIR is refused."""

    def test_path_outside_the_allowed_dir_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = _csv(tmp_path, 3)
        monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(allowed))
        c = CsvConnector(connection={"file_path": outside})
        assert c.path_is_allowed() is False


class TestP4RefusalsAreAudited:
    """P4 — every refusal writes an audit entry with action + reason."""

    def test_a_blocked_keyword_is_audited(self, tmp_path: Path) -> None:
        from tdb.config import get_log_file

        csv_path = tmp_path / "s.csv"
        csv_path.write_text("id\n1\n")
        created = client.post(
            "/v1/sources",
            headers=HEADERS,
            json={
                "name": "parity_src",
                "source_type": "csv",
                "connection": {"file_path": str(csv_path)},
            },
        )
        client.post(
            "/v1/query",
            headers=HEADERS,
            json={"source_id": created.json()["id"], "sql": "DROP TABLE data"},
        )
        entries = [
            json.loads(ln)
            for ln in Path(get_log_file()).read_text().splitlines()
            if ln
        ]
        denied = [e for e in entries if e.get("event") == "denied"]
        assert denied
        assert denied[-1]["action"] == "query"
        assert denied[-1]["reason"]


class TestP5ReadOnly:
    """P5 — non-SELECT is refused before it reaches a connector."""

    @pytest.mark.parametrize(
        "sql",
        ["DELETE FROM t", "UPDATE t SET a=1", "INSERT INTO t VALUES (1)", "TRUNCATE t"],
    )
    def test_writes_are_refused(self, sql: str) -> None:
        assert not validate_sql(sql).is_valid


class TestP6QueryTimeout:
    """P6 — a query cannot run forever."""

    @pytest.mark.skip(
        reason=(
            "Enterprise only, by design. Every timeout TDB applies is enforced by "
            "the source engine (statement_timeout, max_execution_time, the ODBC "
            "query timeout, STATEMENT_TIMEOUT_IN_SECONDS). Community's only source "
            "type is CSV/DuckDB, which has no server to ask — enforcing it here "
            "would need a watchdog thread calling interrupt(), i.e. new concurrency "
            "in the free tier for no current benefit. The ID is kept so this gap is "
            "visible rather than silent; see tdb-enterprise dev-day-20."
        )
    )
    def test_the_timeout_is_configurable_and_defaults_on(self) -> None:
        raise AssertionError("unreachable — skipped above")
