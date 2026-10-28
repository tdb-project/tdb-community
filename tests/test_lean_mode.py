"""
Lean Mode on community's `query_source`: smaller results for AI agents.

Two layers here: the server default (`TDB_LEAN_MODE`) and the tool call's own
arguments, the call winning field by field. Nothing set means today's output.

The app is imported inside a fixture: other test modules reload `tdb*`, and a
module-level import would keep a stale audit logger.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

client: TestClient
HEADERS = {"Authorization": "Bearer test-key-abc"}
_LONG = "x" * 120
_SQL = "SELECT id, note FROM data ORDER BY id"


@pytest.fixture(autouse=True)
def source(tmp_path: Path):
    global client
    from tdb.main import app

    client = TestClient(app)
    p = tmp_path / "lean.csv"
    p.write_text(f"id,note\n1,{_LONG}\n2,short\n3,\n")
    reg = client.post(
        "/v1/sources",
        headers=HEADERS,
        json={
            "name": "lean",
            "source_type": "csv",
            "connection": {"file_path": str(p)},
        },
    )
    assert reg.status_code == 201, reg.text
    yield
    client.delete(f"/v1/sources/{reg.json()['id']}", headers=HEADERS)


def _call(args: dict, version: str | None = "2025-06-18") -> dict:
    headers = {**HEADERS, **({"MCP-Protocol-Version": version} if version else {})}
    r = client.post(
        "/v1/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_source", "arguments": {"sql": _SQL, **args}},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _payload(result: dict) -> dict:
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


@pytest.mark.parametrize("version", [None, "2024-11-05", "2025-06-18"])
def test_nothing_set_is_today_s_output(version: str | None) -> None:
    result = _call({}, version)
    payload = _payload(result)
    assert "lean" not in payload
    assert payload["rows"][0] == {"id": 1, "note": _LONG}
    assert result["content"][0]["text"] == json.dumps(payload, default=str)


def test_lean_true_sends_rows_as_arrays() -> None:
    payload = _payload(_call({"lean": True}))
    assert payload["lean"] == {"format": "table"}
    assert payload["rows"] == [[1, _LONG], [2, "short"], [3, None]]


def test_max_cell_chars_marks_every_cut() -> None:
    payload = _payload(_call({"max_cell_chars": 50}))
    assert payload["lean"] == {
        "format": "objects",
        "max_cell_chars": 50,
        "trimmed_cells": 1,
    }
    assert payload["rows"][0]["note"] == "x" * 50 + "…[+70 chars]"
    assert payload["rows"][1]["note"] == "short"


@pytest.mark.parametrize(
    ("args", "named"),
    [
        ({"format": "csv"}, "'objects', 'table'"),
        ({"max_cell_chars": 10}, "at least 50"),
        ({"lean": "yes"}, "true or false"),
    ],
)
def test_a_bad_value_is_refused_naming_what_is_allowed(args: dict, named: str) -> None:
    result = _call(args)
    assert result["isError"] is True
    assert named in result["content"][0]["text"]


def test_server_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDB_LEAN_MODE", "on")
    assert _payload(_call({}))["lean"] == {"format": "table"}


def test_call_beats_server_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDB_LEAN_MODE", '{"lean": true, "max_cell_chars": 50}')
    assert _payload(_call({"format": "objects"}))["lean"]["format"] == "objects"
    assert "lean" not in _payload(_call({"lean": False}))


def test_an_invalid_server_default_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TDB_LEAN_MODE", "{not json")
    result = _call({})
    assert result["isError"] is True
    assert "TDB_LEAN_MODE" in result["content"][0]["text"]


def test_lean_structured_output_matches_the_text_and_the_schema() -> None:
    tools = client.post(
        "/v1/mcp",
        headers={**HEADERS, "MCP-Protocol-Version": "2025-06-18"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()["result"]["tools"]
    result = _call({"lean": True, "max_cell_chars": 50})
    assert result["structuredContent"] == _payload(result)
    jsonschema.validate(result["structuredContent"], tools[0]["outputSchema"])


def test_the_audit_entry_records_the_lean_settings() -> None:
    _call({"lean": True})
    lines = Path(os.environ["TDB_LOG_FILE"]).read_text().splitlines()
    last = [e for e in map(json.loads, lines) if e.get("event") == "query"][-1]
    assert last["lean"] == {"format": "table"}
