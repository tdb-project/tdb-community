"""Findings of an independent review of 0.17.1, each reproduced before the fix."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from tdb.config import get_log_file
from tdb.connectors.csv import CsvConnector, _inject_limit
from tdb.main import app
from tdb.models.source import SourceRegistrationRequest
from tdb.registry import store

client = TestClient(app)
KEY = "test-key-abc"
HEADERS = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture()
def source(tmp_path: Path):
    path = tmp_path / "f.csv"
    path.write_text("id,credit_limit\n1,10\n2,20\n")
    created = client.post(
        "/v1/sources",
        headers=HEADERS,
        json={
            "name": "Review",
            "source_type": "csv",
            "connection": {"file_path": str(path)},
        },
    )
    assert created.status_code == 201, created.text
    Path(get_log_file()).write_text("")
    return created.json()["id"], path


def _entries() -> list[dict]:
    return [json.loads(x) for x in Path(get_log_file()).read_text().splitlines()]


def _mcp(arguments: dict, auth: str = f"Bearer {KEY}") -> dict:
    return client.post(
        "/v1/mcp",
        headers={"Authorization": auth, "MCP-Protocol-Version": "2025-11-25"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_source", "arguments": arguments},
        },
    ).json()


def _request(name: str, path: str) -> SourceRegistrationRequest:
    return SourceRegistrationRequest(
        name=name, source_type="csv", connection={"file_path": path}
    )


# ── One source at a time holds under concurrent registration ────────────────


def test_concurrent_registrations_register_one_source(tmp_path: Path):
    path = tmp_path / "f.csv"
    path.write_text("id\n1\n")
    start = threading.Barrier(8)
    outcomes: list[str] = []

    def register(i: int) -> None:
        start.wait()
        try:
            store.register_source(_request(f"s{i}", str(path)), registered_by="t")
            outcomes.append("ok")
        except ValueError:
            outcomes.append("refused")

    threads = [threading.Thread(target=register, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("ok") == 1, outcomes
    assert len(store.list_sources()) == 1


# ── MCP reads the Authorization header the way REST does ────────────────────


def test_mcp_refuses_a_key_sent_without_the_bearer_scheme(source):
    reply = _mcp({"sql": "SELECT 1"}, auth=KEY)
    assert reply["error"]["code"] == -32001


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "Bearer"])
def test_mcp_accepts_the_bearer_scheme_in_any_case(source, scheme):
    reply = _mcp({"sql": "SELECT 1 AS x"}, auth=f"{scheme} {KEY}")
    assert "error" not in reply, reply
    assert reply["result"].get("isError") is not True, reply


# ── Error responses name the source, never the server path ──────────────────


def test_schema_of_a_missing_file_does_not_show_its_path(source):
    _, path = source
    path.unlink()
    r = client.get("/v1/sources/Review/schema", headers=HEADERS)
    assert r.status_code == 503
    assert str(path) not in r.text
    assert "Review" in r.json()["detail"]


def test_mcp_query_on_a_missing_file_does_not_show_its_path(source):
    _, path = source
    path.unlink()
    reply = _mcp({"sql": "SELECT * FROM data"})
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert str(path) not in text
    assert "Review" in text


def test_an_unexpected_error_is_not_echoed(source, monkeypatch):
    def boom(self, sql, limit=100):
        raise RuntimeError("secret internals /srv/private")

    monkeypatch.setattr(CsvConnector, "execute", boom)
    r = client.post(
        "/v1/query", headers=HEADERS, json={"source_id": "Review", "sql": "SELECT 1"}
    )
    assert r.status_code == 500
    assert "secret" not in r.text
    text = _mcp({"sql": "SELECT 1"})["result"]["content"][0]["text"]
    assert "secret" not in text
    assert [e["reason"] for e in _entries()] == ["internal_error", "internal_error"]


def test_rest_and_mcp_audit_an_engine_io_error_alike(source, monkeypatch):
    def io_error(self, sql, limit=100):
        raise duckdb.IOException("file vanished mid-read")

    monkeypatch.setattr(CsvConnector, "execute", io_error)
    r = client.post(
        "/v1/query", headers=HEADERS, json={"source_id": "Review", "sql": "SELECT 1"}
    )
    assert r.status_code == 503
    _mcp({"sql": "SELECT 1"})
    assert [e["reason"] for e in _entries()] == [
        "source_unavailable",
        "source_unavailable",
    ]


# ── The schema endpoint audits what it refuses and does not 500 ─────────────


def test_schema_refusals_are_audited(source):
    _, path = source
    assert client.get("/v1/sources/nosuch/schema", headers=HEADERS).status_code == 404
    path.unlink()
    assert client.get("/v1/sources/Review/schema", headers=HEADERS).status_code == 503
    entries = _entries()
    assert [(e["event"], e["action"], e["reason"]) for e in entries] == [
        ("denied", "schema", "source_not_found"),
        ("denied", "schema", "source_unavailable"),
    ]


def test_schema_of_an_unreadable_file_is_503_and_audited(source):
    _, path = source
    path.write_bytes(b"\xff\xfe\x00garbage\x00\n\x00")
    r = client.get("/v1/sources/Review/schema", headers=HEADERS)
    assert r.status_code == 503, r.text
    assert str(path) not in r.text
    assert [e["reason"] for e in _entries()] == ["source_unavailable"]


# ── A source is found the same way on MCP as on REST ────────────────────────


def test_mcp_finds_a_source_by_name_in_any_case_and_by_uuid(source):
    source_id, _ = source
    for ref in ("review", source_id):
        reply = _mcp({"source_name": ref, "sql": "SELECT 1 AS x"})
        assert reply["result"].get("isError") is not True, (ref, reply)


def test_responses_carry_the_uuid_when_called_by_name(source):
    source_id, _ = source
    q = client.post(
        "/v1/query", headers=HEADERS, json={"source_id": "review", "sql": "SELECT 1"}
    )
    assert q.json()["source_id"] == source_id
    s = client.get("/v1/sources/review/schema", headers=HEADERS)
    assert s.json()["source_id"] == source_id


# ── The audit log's directory is created at startup ─────────────────────────


def test_startup_creates_the_audit_log_directory(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "nested" / "audit.jsonl"
    monkeypatch.setenv("TDB_LOG_FILE", str(log))
    with TestClient(app) as started:
        r = started.get("/v1/sources", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert json.loads(log.read_text().splitlines()[-1])["reason"] == "invalid_api_key"


# ── The row cap is pushed down unless the query has its own LIMIT ───────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT credit_limit FROM data",
        "SELECT * FROM data WHERE note = 'no limit'",
        "SELECT 1 -- limit",
        'SELECT "limit" FROM data',
    ],
)
def test_limit_is_injected_when_the_word_is_not_a_clause(sql):
    assert _inject_limit(sql, 5).endswith("\nLIMIT 5")


@pytest.mark.parametrize("sql", ["SELECT 1 LIMIT 3", "select 1\nlimit 3"])
def test_limit_is_not_injected_over_a_limit_clause(sql):
    assert _inject_limit(sql, 5) == sql
