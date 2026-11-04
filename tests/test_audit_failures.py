"""A query that fails is audited like one that is refused, on REST and MCP."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from tdb.config import get_log_file
from tdb.connectors.base import QueryError
from tdb.connectors.csv import CsvConnector
from tdb.connectors.failures import failure_reason
from tdb.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-key-abc"}
MCP_HEADERS = {**HEADERS, "MCP-Protocol-Version": "2025-11-25"}
BAD_SQL = "SELECT nosuch FROM data WHERE id = 7"


@pytest.fixture()
def source(tmp_path: Path):
    path = tmp_path / "f.csv"
    path.write_text("id,v\n1,a\n")
    created = client.post(
        "/v1/sources",
        headers=HEADERS,
        json={
            "name": "failures",
            "source_type": "csv",
            "connection": {"file_path": str(path)},
        },
    )
    assert created.status_code == 201, created.text
    log = Path(get_log_file())
    log.write_text("")
    yield created.json()["id"], path
    client.delete(f"/v1/sources/{created.json()['id']}", headers=HEADERS)


def _entries() -> list[dict]:
    return [json.loads(x) for x in Path(get_log_file()).read_text().splitlines()]


def _only_entry() -> dict:
    entries = _entries()
    assert len(entries) == 1, entries
    return entries[0]


def _query(source_id: str, sql: str = BAD_SQL):
    return client.post(
        "/v1/query", headers=HEADERS, json={"source_id": source_id, "sql": sql}
    )


def _mcp(sql: str = BAD_SQL) -> dict:
    return client.post(
        "/v1/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "query_source",
                "arguments": {"source_name": "failures", "sql": sql},
            },
        },
    ).json()


def _assert_failed(entry: dict, source_id: str, action: str, reason: str) -> None:
    assert entry["event"] == "denied"
    assert entry["action"] == action
    assert entry["reason"] == reason
    assert entry["source_id"] == source_id
    assert entry["sql"] == BAD_SQL
    assert entry["key_hint"]


def test_rest_a_query_the_engine_rejects(source) -> None:
    source_id, _ = source
    assert _query(source_id).status_code == 400
    _assert_failed(_only_entry(), source_id, "query", "query_failed")


def test_rest_a_source_whose_file_is_gone(source) -> None:
    source_id, path = source
    path.unlink()
    assert _query(source_id).status_code == 503
    _assert_failed(_only_entry(), source_id, "query", "source_unavailable")


def test_rest_an_unexpected_error(source, monkeypatch) -> None:
    source_id, _ = source

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(CsvConnector, "execute", boom)
    assert _query(source_id).status_code == 500
    _assert_failed(_only_entry(), source_id, "query", "internal_error")


def test_mcp_a_query_the_engine_rejects(source) -> None:
    source_id, _ = source
    assert _mcp()["result"]["isError"] is True
    _assert_failed(_only_entry(), source_id, "mcp_query", "query_failed")


def test_mcp_a_source_whose_file_is_gone(source) -> None:
    source_id, path = source
    path.unlink()
    assert _mcp()["result"]["isError"] is True
    _assert_failed(_only_entry(), source_id, "mcp_query", "source_unavailable")


def test_the_entry_does_not_hold_the_engine_error_text(source) -> None:
    source_id, _ = source
    _query(source_id, "SELECT nosuch FROM data WHERE id = 'engine-quoted'")
    entry = _only_entry()
    assert set(entry) == {
        "event",
        "action",
        "reason",
        "source_id",
        "sql",
        "key_hint",
        "ts",
    }


def test_a_failed_query_is_redacted_in_redacted_mode(source, monkeypatch) -> None:
    source_id, _ = source
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    _query(source_id, "SELECT nosuch FROM data WHERE x = 'sekret'")
    entry = _only_entry()
    assert entry["reason"] == "query_failed"
    assert entry["sql"] == "SELECT nosuch FROM data WHERE x = ?"


def test_a_successful_query_is_still_one_query_entry(source) -> None:
    source_id, _ = source
    assert _query(source_id, "SELECT * FROM data").status_code == 200
    assert _only_entry()["event"] == "query"


@pytest.mark.parametrize(
    "exc,reason",
    [
        (QueryError("x"), "query_failed"),
        (FileNotFoundError("x"), "source_unavailable"),
        (duckdb.IOException("x"), "source_unavailable"),
        (PermissionError("x"), "path_outside_allowed_dir"),
        (ValueError("x"), "invalid_arguments"),
        (RuntimeError("x"), "internal_error"),
    ],
)
def test_failure_reason(exc: Exception, reason: str) -> None:
    assert failure_reason(exc) == reason
