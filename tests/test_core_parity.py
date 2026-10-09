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

    def test_result_under_the_cap_is_not_marked_truncated(self, tmp_path: Path) -> None:
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
            json.loads(ln) for ln in Path(get_log_file()).read_text().splitlines() if ln
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


class TestP7TruncatedMeansRowsWereWithheld:
    """P7 — `truncated:false` is a completeness claim (e2e pass, 2026-08-11).

    The docs promise that `truncated:false` means "you received the whole
    result". For the product's whole life that was false on the most ordinary
    path: SQL with no LIMIT of its own was injected with `LIMIT <limit>`, the
    source returned exactly `limit` rows, and the len>limit sentinel check
    could never fire — a 5-row table queried with limit=2 returned 2 rows and
    `truncated:false`. An AI agent following the documented warning box then
    treats a cut result as complete and silently loses rows. The injection now
    passes limit+1 so the sentinel row can come back.
    """

    def test_injected_limit_still_reports_truncation(self, tmp_path: Path) -> None:
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 5)})
        result = c.execute("SELECT * FROM data", limit=2)
        assert len(result.rows) == 2
        assert result.truncated is True

    def test_exact_fit_is_not_marked_truncated(self, tmp_path: Path) -> None:
        """limit == row count must stay false — the sentinel row must be the
        row BEYOND the ceiling, not the last row of a complete result."""
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 4)})
        result = c.execute("SELECT * FROM data", limit=4)
        assert len(result.rows) == 4
        assert result.truncated is False


class TestP8CtesAreAcceptedAndStillReadOnly:
    """
    P8 — a read-only CTE runs; a data-modifying one is still refused (§7 item 8).

    `WITH … SELECT` is standard, read-only, and the natural shape for the
    analytical queries this product is sold for — and it was refused for the
    product's whole life by a prefix guard that was doing no write-protection
    work: the blocked-keyword scan runs first and ignores the opening token, so
    every writing CTE was already gone before the prefix was consulted.

    The two halves are one invariant on purpose. Accepting `WITH` without the
    refusal half would be a widened write surface; asserting the refusal without
    the acceptance is what the suite already did, and it pinned the limitation
    as intent. See decisions/cte-support-in-validate-sql.md.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH a AS (SELECT 1) SELECT * FROM a",
            "with a as (select 1) select * from a",
            "/* leading comment */ SELECT 1",
        ],
    )
    def test_read_only_shapes_are_accepted(self, sql: str) -> None:
        assert validate_sql(sql).is_valid

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x",
            "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
            "WITH x AS (SELECT 1) UPDATE t SET a = 1",
        ],
    )
    def test_a_writing_cte_is_refused(self, sql: str) -> None:
        assert not validate_sql(sql).is_valid

    def test_a_cte_runs_end_to_end_and_is_still_capped(self, tmp_path: Path) -> None:
        """
        The validator half is worthless if the connector then mishandles it.
        Accepting the statement must not cost the row cap or `truncated`.
        """
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 5)})
        result = c.execute("WITH a AS (SELECT * FROM data) SELECT * FROM a", limit=2)
        assert len(result.rows) == 2
        assert result.truncated is True


class TestP9SqlCannotReachFilesBeyondTheSource:
    """
    P9 — SQL reaches the registered CSV and its data directory, nothing else.

    `TDB_ALLOWED_DATA_DIR` only ever checked the *registered* path. DuckDB
    resolves file paths written inside the SQL too (`read_csv`, `read_text`,
    `COPY … TO`), so a plain SELECT on any registered source could read any file
    the process could, confinement set or not. The engine now refuses file
    access outside the data directory and locks its configuration, and the
    validator refuses a second statement — the only route to a write, since a
    single SELECT or WITH cannot write a file.
    """

    @staticmethod
    def _layout(tmp_path: Path) -> tuple[Path, Path]:
        data = tmp_path / "data"
        other = tmp_path / "other"
        data.mkdir()
        other.mkdir()
        (data / "ok.csv").write_text("id,v\n1,a\n")
        secret = other / "secret.csv"
        secret.write_text("k,v\nP9-SECRET,1\n")
        return data / "ok.csv", secret

    @pytest.mark.parametrize("confined", [True, False])
    def test_a_file_outside_the_data_dir_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confined: bool
    ) -> None:
        from tdb.connectors.csv import SqlFileAccessError, close_engine

        ok, secret = self._layout(tmp_path)
        if confined:
            monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(ok.parent))
        else:
            monkeypatch.delenv("TDB_ALLOWED_DATA_DIR", raising=False)
        close_engine()
        c = CsvConnector(connection={"file_path": str(ok)})
        for sql in (
            f"SELECT * FROM read_csv('{secret}')",
            f"SELECT content FROM read_text('{secret}')",
            f"SELECT * FROM read_csv('{ok.parent}/../other/secret.csv')",
        ):
            with pytest.raises(SqlFileAccessError):
                c.execute(sql, limit=10)
        assert c.execute("SELECT * FROM data", limit=10).rows == [{"id": 1, "v": "a"}]

    def test_sql_cannot_change_the_engine_configuration(self, tmp_path: Path) -> None:
        # Driven at the engine, not the connector: the connector now refuses a
        # second statement before it runs, and the lock has to hold even if
        # something ever reaches the engine past that check.
        from tdb.connectors.csv import _data_root, _engine, close_engine

        ok, _ = self._layout(tmp_path)
        close_engine()
        cur = _engine(_data_root(str(ok))).cursor()
        try:
            with pytest.raises(Exception, match="(?i)configuration"):
                cur.execute("SET enable_external_access = true")
        finally:
            cur.close()

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; SELECT 2",
            "SELECT 1 AS a; COPY (SELECT 1) TO '/tmp/x.csv'",
            "SELECT 1;SET threads = 1",
            "SELECT 1; ATTACH 'x.db'",
            "SELECT 1 /* ; */ ; SELECT 2",
        ],
    )
    def test_a_second_statement_is_refused(self, sql: str) -> None:
        result = validate_sql(sql)
        assert not result.is_valid
        assert "one statement" in result.reason.lower()

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1;",
            "SELECT 1;  \n",
            "SELECT 1; -- trailing comment",
            "SELECT ';' AS semi",
            "SELECT 1 /* ; SELECT 2 */",
        ],
    )
    def test_a_trailing_or_quoted_semicolon_is_not_a_second_statement(
        self, sql: str
    ) -> None:
        assert validate_sql(sql).is_valid

    def test_the_api_refuses_and_audits_without_leaking_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tdb.connectors.csv import close_engine

        ok, secret = self._layout(tmp_path)
        log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("TDB_LOG_FILE", str(log))
        monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(ok.parent))
        close_engine()
        reg = client.post(
            "/v1/sources",
            headers=HEADERS,
            json={
                "name": "p9",
                "source_type": "csv",
                "connection": {"file_path": str(ok)},
            },
        )
        try:
            assert reg.status_code == 201, reg.text
            sid = reg.json()["id"]
            r = client.post(
                "/v1/query",
                headers=HEADERS,
                json={"source_id": sid, "sql": f"SELECT * FROM read_csv('{secret}')"},
            )
            assert r.status_code == 403, r.text
            assert "P9-SECRET" not in r.text
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            assert any(
                e.get("event") == "denied" and e.get("reason") == "sql_file_access"
                for e in entries
            ), entries
        finally:
            client.delete(f"/v1/sources/{reg.json().get('id', '')}", headers=HEADERS)


class TestP10ATrailingSemicolonRuns:
    """
    P10 — `SELECT …;` runs. Every connector that appends its row cap appended it
    *after* the `;` (`SELECT 1; LIMIT 6`), so a trailing terminator — which most
    SQL clients add by habit — was a syntax error on every release. The
    validator accepted it; execution then failed.
    """

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT 1;", "SELECT 1"),
            ("SELECT 1 ;  \n", "SELECT 1"),
            ("SELECT 1; -- note", "SELECT 1"),
            ("SELECT 1; /* note */", "SELECT 1"),
            ("SELECT ';'", "SELECT ';'"),
            ("SELECT 'a;'  ;", "SELECT 'a;'"),
            ("SELECT 1", "SELECT 1"),
            ("SELECT 1 -- ends in a comment", "SELECT 1 -- ends in a comment"),
        ],
    )
    def test_only_a_trailing_terminator_is_removed(
        self, sql: str, expected: str
    ) -> None:
        from tdb.engine.validator import strip_trailing_semicolon

        assert strip_trailing_semicolon(sql) == expected

    @pytest.mark.parametrize("tail", [";", "; -- note", " LIMIT 50;"])
    def test_a_csv_query_ending_in_a_semicolon_runs_and_is_capped(
        self, tmp_path: Path, tail: str
    ) -> None:
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 5)})
        result = c.execute(f"SELECT * FROM data{tail}", limit=2)
        assert len(result.rows) == 2
        assert result.truncated is True


class TestP11EveryEngineReadsTheSqlTheValidatorRead:
    """
    P11 — the validator and the engine agree on where literals and comments end.

    The 0.7.x scanner masked string literals the ANSI way only. PostgreSQL and
    DuckDB read `$$…$$` as a string, nest block comments and honour backslashes
    in `E'…'`, so a quote *inside* one of those opened a string in the scanner
    and hid the SQL after it — including a second statement, which DuckDB then
    executed. The validator now masks once per dialect and must pass in all of
    them, and the CSV connector asks DuckDB's own parser for exactly one SELECT.
    """

    SMUGGLED = (
        "SELECT $$'$$ AS a; SELECT 42 AS b; SELECT 'x' AS c",
        "SELECT $q$'$q$ AS a; SELECT 42 AS b; SELECT 'x' AS c",
        "SELECT E'\\'' AS a; SELECT 42 AS b; SELECT 'x' AS c",
        "SELECT 1 /* /* */ ' */ ; SELECT 42 AS b; SELECT 'a' AS c",
    )

    @pytest.mark.parametrize("sql", SMUGGLED)
    def test_the_validator_refuses_sql_an_engine_reads_as_several_statements(
        self, sql: str
    ) -> None:
        assert not validate_sql(sql).is_valid

    @pytest.mark.parametrize("sql", SMUGGLED)
    def test_the_csv_connector_refuses_it_without_the_validator(
        self, tmp_path: Path, sql: str
    ) -> None:
        from tdb.connectors.csv import SqlRefusedError

        c = CsvConnector(connection={"file_path": _csv(tmp_path, 3)})
        with pytest.raises(SqlRefusedError):
            c.execute(sql, limit=10)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'update pending' AS note FROM data",
            "SELECT $$it's$$ AS a",
            "SELECT 'C:\\x' AS p",
            "SELECT 1 /* outer /* inner */ still a comment */",
            "SELECT 1 AS a;",
        ],
    )
    def test_read_only_sql_using_those_forms_still_runs(
        self, tmp_path: Path, sql: str
    ) -> None:
        assert validate_sql(sql).is_valid
        c = CsvConnector(connection={"file_path": _csv(tmp_path, 3)})
        assert c.execute(sql, limit=10).rows

    def test_past_the_validator_the_api_refuses_with_400_and_audits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The validator is stubbed out so this exercises the connector layer on
        # its own through the real route: if the text scan is ever wrong again,
        # the caller must still get a refusal, and the audit log must say so.
        import tdb.routers.query as query_router
        from tdb.engine.validator import ValidationResult

        log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("TDB_LOG_FILE", str(log))
        monkeypatch.setattr(
            query_router, "validate_sql", lambda sql: ValidationResult(is_valid=True)
        )
        reg = client.post(
            "/v1/sources",
            headers=HEADERS,
            json={
                "name": "p11",
                "source_type": "csv",
                "connection": {"file_path": _csv(tmp_path, 3)},
            },
        )
        try:
            assert reg.status_code == 201, reg.text
            r = client.post(
                "/v1/query",
                headers=HEADERS,
                json={"source_id": reg.json()["id"], "sql": self.SMUGGLED[0]},
            )
            assert r.status_code == 400, r.text
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            assert any(
                e.get("event") == "denied"
                and e.get("reason") == "sql_validation_failed"
                for e in entries
            )
        finally:
            client.delete(f"/v1/sources/{reg.json()['id']}", headers=HEADERS)
