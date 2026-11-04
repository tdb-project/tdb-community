"""TDB_AUDIT_SQL through the running API: what actually lands in the audit file."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.config import get_log_file
from tdb.main import app

_HEADERS = {"Authorization": "Bearer test-key-abc"}
_SECRET = "hunter2"
_QUERY = f"SELECT * FROM data WHERE v = '{_SECRET}' AND id = 987654"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TDB_LOG_FILE", str(tmp_path / "audit.jsonl"))
    (tmp_path / "s.csv").write_text("id,v\n1,a\n")
    test_client = TestClient(app)
    response = test_client.post(
        "/v1/sources",
        headers=_HEADERS,
        json={
            "name": "red",
            "source_type": "csv",
            "connection": {"file_path": str(tmp_path / "s.csv")},
        },
    )
    assert response.status_code == 201, response.text
    test_client.source_id = response.json()["id"]
    return test_client


def _audit() -> str:
    return Path(get_log_file()).read_text()


def _entries() -> list[dict]:
    return [json.loads(line) for line in _audit().splitlines()]


def _query(client: TestClient, sql: str):
    return client.post(
        "/v1/query", headers=_HEADERS, json={"source_id": client.source_id, "sql": sql}
    )


def test_by_default_the_statement_is_stored_as_written(client, monkeypatch):
    monkeypatch.delenv("TDB_AUDIT_SQL", raising=False)
    _query(client, _QUERY)
    entry = _entries()[-1]
    assert entry["sql"] == _QUERY
    assert "sql_redacted" not in entry
    assert "sql_sha256" not in entry


def test_redacted_mode_stores_the_shape_of_a_query(client, monkeypatch):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    assert _query(client, _QUERY).status_code == 200
    entry = _entries()[-1]
    assert entry["sql"] == "SELECT * FROM data WHERE v = ? AND id = ?"
    assert entry["sql_redacted"] is True
    assert len(entry["sql_sha256"]) == 64
    assert _SECRET not in _audit()


def test_redacted_mode_covers_a_refusal(client, monkeypatch):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    response = _query(client, f"DROP TABLE x -- token={_SECRET}")
    assert response.status_code == 400
    assert _entries()[-1]["event"] == "denied"
    assert _SECRET not in _audit()


def test_redacted_mode_covers_mcp(client, monkeypatch):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    client.post(
        "/v1/mcp",
        headers=_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_source", "arguments": {"sql": _QUERY}},
        },
    )
    assert _entries()[-1]["sql_redacted"] is True
    assert _SECRET not in _audit()


def test_the_caller_still_gets_the_real_result(client, monkeypatch):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    response = _query(client, "SELECT * FROM data WHERE v = 'a'")
    assert response.json()["rows"] == [{"id": 1, "v": "a"}]


def test_a_new_audit_file_is_not_world_readable(client):
    _query(client, _QUERY)
    assert stat.S_IMODE(Path(get_log_file()).stat().st_mode) == 0o600


def test_a_mistyped_mode_stops_startup(monkeypatch):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redact")
    with pytest.raises(ValueError, match="TDB_AUDIT_SQL"):
        with TestClient(app):
            pass
