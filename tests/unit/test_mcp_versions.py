"""
MCP version negotiation, the MCP-Protocol-Version header, and the tool metadata
a 2025-06-18+ client receives (tracker §7 item 30).

The rule worth guarding: a client that never sends the header, or names
2024-11-05, receives exactly what it did before item 30. Titles, annotations
and structured output appear only from 2025-06-18.

Imports live inside fixtures: other test modules delete `tdb*` from sys.modules.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

KEY = {"Authorization": "Bearer test-key-day4"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    from tdb.main import app

    c = TestClient(app)
    csv_file = tmp_path / "d.csv"
    csv_file.write_text("id,name\n1,a\n2,b\n")
    r = c.post(
        "/v1/sources",
        json={
            "name": "d",
            "source_type": "csv",
            "connection": {"file_path": str(csv_file)},
        },
        headers=KEY,
    )
    assert r.status_code == 201
    return c


def _rpc(c: TestClient, method: str, params: dict, version: str | None) -> dict:
    headers = {**KEY, **({"MCP-Protocol-Version": version} if version else {})}
    r = c.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


@pytest.mark.parametrize(
    ("requested", "answered"),
    [
        ("2025-11-25", "2025-11-25"),
        ("2025-06-18", "2025-06-18"),
        ("2024-11-05", "2024-11-05"),
        ("2025-03-26", "2024-11-05"),  # batching revision: answered as before
        ("2026-07-28", "2025-11-25"),  # never newer than asked for
        ("2024-10-07", "2024-11-05"),
        ("not-a-date", "2024-11-05"),
        (None, "2024-11-05"),
    ],
)
def test_initialize_negotiates_the_version(
    client: TestClient, requested: str | None, answered: str
) -> None:
    params = {} if requested is None else {"protocolVersion": requested}
    assert _rpc(client, "initialize", params, None)["protocolVersion"] == answered


def test_server_info_gains_title_only_from_2025_06_18(client: TestClient) -> None:
    old = _rpc(client, "initialize", {"protocolVersion": "2024-11-05"}, None)
    new = _rpc(client, "initialize", {"protocolVersion": "2025-11-25"}, None)
    assert set(old["serverInfo"]) == {"name", "version"}
    assert old["capabilities"] == {"tools": {}}
    assert new["serverInfo"]["title"] == "TDB Community"
    assert new["capabilities"] == {"tools": {"listChanged": False}}


def test_an_unsupported_handshake_header_is_400(client: TestClient) -> None:
    # 2025-03-26 selects the handshake era (it is a handshake revision) but is
    # not served there. Any other unknown value is judged by 2026-07-28 rules.
    r = client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**KEY, "MCP-Protocol-Version": "2025-03-26"},
    )
    assert r.status_code == 400
    assert "2025-11-25" in r.json()["error"]["message"]


@pytest.mark.parametrize("version", [None, "2024-11-05"])
def test_a_legacy_client_sees_no_new_fields(
    client: TestClient, version: str | None
) -> None:
    tool = _rpc(client, "tools/list", {}, version)["tools"][0]
    assert set(tool) == {"name", "description", "inputSchema"}
    call = _rpc(
        client,
        "tools/call",
        {"name": "query_source", "arguments": {"sql": "SELECT * FROM data"}},
        version,
    )
    assert "structuredContent" not in call


@pytest.mark.parametrize("version", ["2025-06-18", "2025-11-25"])
def test_a_current_client_gets_metadata_and_valid_structured_output(
    client: TestClient, version: str
) -> None:
    tool = _rpc(client, "tools/list", {}, version)["tools"][0]
    assert tool["title"]
    assert tool["annotations"] == {"readOnlyHint": True, "openWorldHint": False}
    jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])

    call = _rpc(
        client,
        "tools/call",
        {"name": "query_source", "arguments": {"sql": "SELECT * FROM data"}},
        version,
    )
    assert call["structuredContent"] == json.loads(call["content"][0]["text"])
    jsonschema.validate(call["structuredContent"], tool["outputSchema"])
    assert call["structuredContent"]["rows_returned"] == 2


def test_a_tool_error_carries_no_structured_content(client: TestClient) -> None:
    call = _rpc(
        client,
        "tools/call",
        {"name": "query_source", "arguments": {"sql": "DROP TABLE data"}},
        "2025-11-25",
    )
    assert call["isError"] is True
    assert "structuredContent" not in call
