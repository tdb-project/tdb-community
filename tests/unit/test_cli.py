"""
CLI tests — `tdb serve`, `tdb register`, `tdb query`.

`src/tdb/cli/` had **no tests at all** until 2026-08-12: 117 statements at 0%
coverage, about a third of every uncovered statement in the codebase, behind a
declared `[project.scripts]` entry point and three commands listed as a shipped
feature. Two display bugs were sitting in it, both found by running the commands
by hand:

* NULL rendered as the string ``"None"``, indistinguishable from a column whose
  value genuinely is that text — and an empty CSV cell is the ordinary way a NULL
  arrives.
* An unrecognised ``--output`` silently fell through to the table branch, so
  ``-o jsn`` produced a table and exit 0 instead of an error.

**These tests drive the real application, not a mocked HTTP layer.**
``make_client`` is redirected onto a ``TestClient`` bound to the actual FastAPI
app, so a test exercises CLI → httpx → routers → registry → CSV connector end to
end. That is deliberate: the defect that motivated writing them lived in how the
CLI renders what the server actually returns, and a mocked response is written by
the same person holding the same assumption. ``tdb serve`` is the one command
that cannot work this way — it hands control to uvicorn — so only its arguments
are asserted.

(``TestClient`` rather than ``httpx.Client(transport=ASGITransport(...))``: ASGI
is an async protocol, so ``ASGITransport`` only backs an ``AsyncClient``, and the
CLI is synchronous.)

**Every `tdb*` module is resolved inside a fixture, never at import time.**
``tests/test_persistence_mcp.py`` deletes every ``tdb*`` entry from
``sys.modules`` and re-imports, and it collects before this file. A module-level
``from tdb.cli.main import app`` therefore holds an app whose function globals
belong to a discarded module, while ``monkeypatch.setattr`` lands on the fresh
one — so the patch silently does nothing and the CLI tries to reach
``http://localhost:8000``. This file passed in isolation and failed in the full
suite until the imports moved. Same trap as ``configured_app`` in tdb-enterprise;
it is not enterprise-specific.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

runner = CliRunner()

_API_KEY = "test-key-abc"


@pytest.fixture
def cli_app():
    """The CLI app, resolved now rather than at import time (see module docstring)."""
    import tdb.cli.main as cli_main

    return cli_main.app


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch):
    """The CLI app with its HTTP client pointed at the real FastAPI app."""
    import tdb.cli.main as cli_main
    from tdb.main import app as fastapi_app

    def _make_client() -> httpx.Client:
        return TestClient(
            fastapi_app,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {_API_KEY}"},
        )

    monkeypatch.setattr(cli_main, "make_client", _make_client)
    return cli_main.app


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(
        "id,country,amount\n1,UK,10.50\n2,US,25.00\n3,UK,7.25\n", encoding="utf-8"
    )
    return path


@pytest.fixture
def csv_with_gaps(tmp_path: Path) -> Path:
    """Row 2 has an empty cell; row 3 holds the literal text ``None``.

    The pair is the point: before the fix both rendered identically.
    """
    path = tmp_path / "gaps.csv"
    path.write_text("id,country\n1,UK\n2,\n3,None\n", encoding="utf-8")
    return path


@pytest.fixture
def registered(cli, csv_file: Path):
    """A wired CLI with `sales.csv` already registered."""
    result = runner.invoke(cli, ["register", str(csv_file), "--name", "sales"])
    assert result.exit_code == 0, result.output
    return cli


class TestRegister:
    def test_registers_a_csv(self, cli, csv_file: Path) -> None:
        result = runner.invoke(cli, ["register", str(csv_file), "--name", "sales"])

        assert result.exit_code == 0, result.output
        assert "Source registered" in result.output
        assert "sales" in result.output

    def test_a_missing_file_is_rejected_before_any_request(
        self, cli, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            cli, ["register", str(tmp_path / "nope.csv"), "--name", "x"]
        )

        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_tags_are_split_on_commas(self, cli, csv_file: Path) -> None:
        result = runner.invoke(
            cli,
            ["register", str(csv_file), "--name", "sales", "--tags", "eu, finance ,"],
        )

        assert result.exit_code == 0, result.output
        from tdb.registry import store

        assert store.list_sources()[0].tags == ["eu", "finance"], (
            "empty and whitespace-padded tags must not survive the split"
        )

    def test_a_duplicate_name_fails_with_the_server_message(
        self, registered, csv_file: Path
    ) -> None:
        result = runner.invoke(
            registered, ["register", str(csv_file), "--name", "sales"]
        )

        assert result.exit_code == 1
        assert "Registration failed" in result.output


class TestQueryOutput:
    def test_table_output_shows_the_rows(self, registered) -> None:
        result = runner.invoke(registered, ["query", "SELECT * FROM data"])

        assert result.exit_code == 0, result.output
        assert "3 row(s) returned" in result.output
        assert "UK" in result.output

    def test_json_output_parses(self, registered) -> None:
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data ORDER BY id", "-o", "json"]
        )

        assert result.exit_code == 0, result.output
        assert [r["id"] for r in json.loads(result.output)] == [1, 2, 3]

    def test_csv_output_has_a_header_and_one_line_per_row(self, registered) -> None:
        result = runner.invoke(
            registered,
            ["query", "SELECT id, country FROM data ORDER BY id", "-o", "csv"],
        )

        assert result.exit_code == 0, result.output
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert lines[0] == "id,country"
        assert len(lines) == 4

    def test_an_unknown_format_is_an_error_not_a_silent_table(self, registered) -> None:
        """``-o jsn`` used to print a table and exit 0, so a script asking for the
        wrong format got plausible-looking output instead of a failure."""
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "-o", "jsn"]
        )

        assert result.exit_code == 1
        assert "Unknown output format" in result.output
        assert "json" in result.output

    def test_an_empty_result_still_renders(self, registered) -> None:
        result = runner.invoke(registered, ["query", "SELECT * FROM data WHERE id=999"])

        assert result.exit_code == 0, result.output
        assert "0 row(s) returned" in result.output


class TestNullRendering:
    """The bug this file was written for."""

    def test_null_is_blank_not_the_word_none(self, cli, csv_with_gaps: Path) -> None:
        runner.invoke(cli, ["register", str(csv_with_gaps), "--name", "gaps"])

        result = runner.invoke(cli, ["query", "SELECT country FROM data WHERE id = 2"])

        assert result.exit_code == 0, result.output
        assert "1 row(s) returned" in result.output
        assert "None" not in result.output, (
            "a NULL rendered as the string 'None' — indistinguishable from a "
            "column that genuinely contains that text"
        )

    def test_the_literal_text_none_is_not_suppressed(
        self, cli, csv_with_gaps: Path
    ) -> None:
        """The other half of the pair, in the same format. Blanking NULL must not
        blank the *text* — that would trade one indistinguishable case for
        another, in the opposite direction."""
        runner.invoke(cli, ["register", str(csv_with_gaps), "--name", "gaps"])

        result = runner.invoke(cli, ["query", "SELECT country FROM data WHERE id = 3"])

        assert result.exit_code == 0, result.output
        assert "None" in result.output

    def test_json_is_the_format_that_tells_them_apart(
        self, cli, csv_with_gaps: Path
    ) -> None:
        """Table and CSV both render NULL blank, so neither can distinguish it
        from an empty string. JSON can, and `_cell`'s docstring sends readers
        there — so this pins the claim rather than leaving it as prose."""
        runner.invoke(cli, ["register", str(csv_with_gaps), "--name", "gaps"])

        result = runner.invoke(
            cli, ["query", "SELECT id, country FROM data", "-o", "json"]
        )

        by_id = {r["id"]: r["country"] for r in json.loads(result.output)}
        assert by_id[2] is None
        assert by_id[3] == "None"


class TestQueryErrors:
    def test_no_sources_registered(self, cli) -> None:
        result = runner.invoke(cli, ["query", "SELECT 1"])

        assert result.exit_code == 1
        assert "No sources registered" in result.output

    def test_a_write_statement_is_refused_by_the_server(self, registered) -> None:
        result = runner.invoke(registered, ["query", "DELETE FROM data"])

        assert result.exit_code == 1
        assert "Query failed" in result.output

    def test_invalid_sql_reports_the_server_error(self, registered) -> None:
        result = runner.invoke(registered, ["query", "SELECT nosuchcolumn FROM data"])

        assert result.exit_code == 1

    def test_limit_is_sent_as_given_and_the_server_rejects_it(
        self, registered, monkeypatch
    ) -> None:
        """The CLI no longer caps; the ceiling is the server's.

        It used to send ``min(limit, 1000)``, so ``--limit 5000`` quietly
        returned 1,000 rows that looked like the whole answer. A cap in the
        client could never be authoritative anyway — REST and MCP callers never
        run this code — and here it is enforced by ``QueryRequest.limit``'s
        ``le=1_000``, which *is* the tier boundary.
        """
        seen: dict = {}
        real = httpx.Client.post

        def spy(self, url, **kwargs):
            if url == "/v1/query":
                seen.update(kwargs.get("json", {}))
            return real(self, url, **kwargs)

        monkeypatch.setattr(httpx.Client, "post", spy)
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "--limit", "5000"]
        )

        assert seen["limit"] == 5000, "the CLI must not clamp before sending"
        assert result.exit_code == 1
        assert "Query failed (422)" in result.output

    def test_the_422_is_legible_not_a_python_list(self, registered) -> None:
        """A 422 ``detail`` is a *list of dicts*, not a string.

        Rendering it raw hands the caller a Python repr instead of a reason.
        This is the one thing the no-cap change had to bring with it.
        """
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "--limit", "5000"]
        )

        assert "[{" not in result.output, "the raw list repr leaked into output"
        assert "limit" in result.output
        assert "1000" in result.output.replace(",", "")


class TestSourceSelection:
    """``--source`` exists for enterprise, where many sources are normal.

    Community enforces one source at a time (`registry/store.py:42` raises once
    a source exists), so the *ambiguous* branch is unreachable here — it is
    asserted in the enterprise suite instead. What matters on this side is that
    the option works and that the single-source default is unchanged.
    """

    def test_source_by_name_is_accepted(self, registered) -> None:
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "--source", "sales"]
        )

        assert result.exit_code == 0, result.output
        assert "3 row(s) returned" in result.output

    def test_source_by_uuid_is_accepted(self, registered) -> None:
        """The server resolves either form, so the CLI sends the string as given
        and does no resolution of its own."""
        from tdb.registry import store

        sid = store.list_sources()[0].id
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "--source", sid]
        )

        assert result.exit_code == 0, result.output

    def test_an_explicit_source_skips_the_sources_listing(
        self, registered, monkeypatch
    ) -> None:
        """Naming the source means there is nothing to look up — one fewer round
        trip, and it works with a key that cannot list sources."""
        calls: list[str] = []
        real = httpx.Client.get

        def spy(self, url, **kwargs):
            calls.append(str(url))
            return real(self, url, **kwargs)

        monkeypatch.setattr(httpx.Client, "get", spy)
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "--source", "sales"]
        )

        assert result.exit_code == 0, result.output
        assert "/v1/sources" not in calls

    def test_omitting_source_still_works_with_one_registered(self, registered) -> None:
        result = runner.invoke(registered, ["query", "SELECT id FROM data"])

        assert result.exit_code == 0, result.output

    def test_an_unknown_source_is_refused_by_the_server(self, registered) -> None:
        result = runner.invoke(
            registered, ["query", "SELECT id FROM data", "--source", "nope"]
        )

        assert result.exit_code == 1
        assert "Query failed" in result.output


class TestMissingApiKey:
    """No wiring here, deliberately: these must fail inside the real
    ``make_client``, before any request is built."""

    def test_register_without_a_key_explains_how_to_set_one(
        self, cli_app, monkeypatch: pytest.MonkeyPatch, csv_file: Path
    ) -> None:
        monkeypatch.setenv("TDB_API_KEYS", "")

        result = runner.invoke(cli_app, ["register", str(csv_file), "--name", "x"])

        assert result.exit_code == 1
        assert "TDB_API_KEYS" in result.output

    def test_query_without_a_key_explains_how_to_set_one(
        self, cli_app, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TDB_API_KEYS", "")

        result = runner.invoke(cli_app, ["query", "SELECT 1"])

        assert result.exit_code == 1
        assert "TDB_API_KEYS" in result.output


class TestServe:
    """``serve`` hands control to uvicorn, so this is the one command whose
    collaborator has to be replaced. Only the arguments are asserted — the app
    itself is covered by every other test in this file."""

    def test_passes_host_and_port_to_uvicorn(
        self, cli_app, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        import uvicorn

        monkeypatch.setattr(
            uvicorn, "run", lambda target, **kw: captured.update(target=target, **kw)
        )

        result = runner.invoke(
            cli_app, ["serve", "--host", "0.0.0.0", "--port", "9999"]
        )

        assert result.exit_code == 0, result.output
        assert captured["target"] == "tdb.main:app"
        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 9999
        assert captured["reload"] is False

    def test_reload_flag_is_forwarded(
        self, cli_app, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        import uvicorn

        monkeypatch.setattr(
            uvicorn, "run", lambda target, **kw: captured.update(target=target, **kw)
        )

        runner.invoke(cli_app, ["serve", "--reload"])

        assert captured["reload"] is True


class TestErrorDetail:
    """`_error_detail` renders whatever shape the server sent.

    FastAPI returns a string `detail` for an `HTTPException` and a **list of
    validation dicts** for a 422. The CLI used to call `.get("detail")` and
    interpolate the result, so the 422 path printed a Python list repr — which
    is what an over-ceiling `--limit` produces in the community build.
    """

    @staticmethod
    def _resp(payload, *, text="raw body", raises=False):
        class _R:
            @property
            def text(self):
                return text

            def json(self):
                if raises:
                    raise ValueError("not json")
                return payload

        return _R()

    def test_a_string_detail_passes_through(self) -> None:
        from tdb.cli.main import _error_detail

        assert _error_detail(self._resp({"detail": "limit too large"})) == (
            "limit too large"
        )

    def test_a_422_list_becomes_one_readable_line(self) -> None:
        from tdb.cli.main import _error_detail

        out = _error_detail(
            self._resp(
                {
                    "detail": [
                        {
                            "loc": ["body", "limit"],
                            "msg": "Input should be less than or equal to 1000",
                        }
                    ]
                }
            )
        )

        assert out == "limit: Input should be less than or equal to 1000"
        assert "[{" not in out

    def test_several_validation_errors_are_joined(self) -> None:
        from tdb.cli.main import _error_detail

        out = _error_detail(
            self._resp(
                {
                    "detail": [
                        {"loc": ["body", "limit"], "msg": "too large"},
                        {"loc": ["body", "sql"], "msg": "too short"},
                    ]
                }
            )
        )

        assert out == "limit: too large; sql: too short"

    def test_a_list_of_non_dicts_still_renders(self) -> None:
        from tdb.cli.main import _error_detail

        assert _error_detail(self._resp({"detail": ["boom", "bang"]})) == "boom; bang"

    def test_unparseable_body_falls_back_to_text(self) -> None:
        from tdb.cli.main import _error_detail

        assert _error_detail(
            self._resp(None, text="<html>502</html>", raises=True)
        ) == ("<html>502</html>")

    def test_a_non_dict_payload_falls_back_to_text(self) -> None:
        from tdb.cli.main import _error_detail

        assert (
            _error_detail(self._resp(["unexpected"], text="body here")) == "body here"
        )

    def test_a_detail_of_another_type_is_stringified(self) -> None:
        from tdb.cli.main import _error_detail

        assert _error_detail(self._resp({"detail": {"code": 7}})) == "{'code': 7}"

    def test_no_detail_key_falls_back_to_text(self) -> None:
        from tdb.cli.main import _error_detail

        assert _error_detail(self._resp({"other": 1}, text="fallback")) == "fallback"
