"""
MCP 2026-07-28 on the same endpoint as the handshake era (tracker §7 item 31).

A request naming 2026-07-28 carries its version in `params._meta` and repeats
it, the method and the tool name in headers. The checks run in the spec's
order: envelope, header agreement, version. An unknown method is a 404, which
is how a client tells a current server from a legacy one, so it is answered
before authentication, as the handshake era answers it.

Imports live inside fixtures: other test modules delete `tdb*` from sys.modules.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

KEY = {"Authorization": "Bearer test-key-day4"}
V = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": V,
    "io.modelcontextprotocol/clientCapabilities": {},
}


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


def _headers(method: str, name: str | None = None, auth: bool = True) -> dict:
    h = {"MCP-Protocol-Version": V, "Mcp-Method": method, **(KEY if auth else {})}
    if name is not None:
        h["Mcp-Name"] = name
    return h


def _post(c: TestClient, method: str, params: dict | None = None, **kw):
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": {"_meta": META}}
    body["params"].update(params or {})
    headers = kw.pop("headers", None)
    if headers is None:
        headers = _headers(method, (params or {}).get("name"), kw.pop("auth", True))
    return c.post("/v1/mcp", json=body, headers=headers)


def _call(c: TestClient, sql: str = "SELECT * FROM data"):
    return _post(c, "tools/call", {"name": "query_source", "arguments": {"sql": sql}})


def test_discover_is_unauthenticated_and_complete(client: TestClient) -> None:
    r = _post(client, "server/discover", auth=False)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["supportedVersions"] == [V]
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["resultType"] == "complete"
    assert result["cacheScope"] == "public"
    assert result["ttlMs"] > 0
    info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "tdb-community"


def test_tools_list_carries_cache_hints_and_metadata(client: TestClient) -> None:
    result = _post(client, "tools/list").json()["result"]
    assert result["resultType"] == "complete"
    assert result["cacheScope"] == "private"
    assert result["ttlMs"] > 0
    tool = result["tools"][0]
    assert tool["annotations"]["readOnlyHint"] is True
    assert "outputSchema" in tool


def test_tools_call_returns_structured_content(client: TestClient) -> None:
    r = _call(client)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["resultType"] == "complete"
    assert result["structuredContent"] == json.loads(result["content"][0]["text"])
    assert result["structuredContent"]["rows_returned"] == 2
    assert "io.modelcontextprotocol/serverInfo" in result["_meta"]


def test_a_tool_error_is_a_complete_result(client: TestClient) -> None:
    r = _call(client, "DROP TABLE data")
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    assert r.json()["result"]["resultType"] == "complete"


@pytest.mark.parametrize(
    ("headers", "why"),
    [
        ({"Mcp-Method": "tools/list", **KEY}, "no version header"),
        ({"MCP-Protocol-Version": V, **KEY}, "no Mcp-Method"),
        (
            {"MCP-Protocol-Version": V, "Mcp-Method": "tools/call", **KEY},
            "Mcp-Method names another method",
        ),
    ],
)
def test_headers_must_agree_with_the_body(
    client: TestClient, headers: dict, why: str
) -> None:
    r = _post(client, "tools/list", headers=headers)
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32020), why


def test_a_handshake_header_with_a_modern_body_is_not_served_as_modern(
    client: TestClient,
) -> None:
    # The header picks the era; the handshake era ignores `_meta`.
    r = client.post(
        "/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": META},
        },
        headers={**KEY, "MCP-Protocol-Version": "2025-11-25"},
    )
    assert r.status_code == 200
    assert "resultType" not in r.json()["result"]


def test_mcp_name_must_match_the_tool(client: TestClient) -> None:
    r = _post(
        client,
        "tools/call",
        {"name": "query_source", "arguments": {"sql": "SELECT 1"}},
        headers=_headers("tools/call", "other_tool"),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32020)


def test_mcp_name_may_be_base64_wrapped(client: TestClient) -> None:
    wrapped = "=?base64?" + base64.b64encode(b"query_source").decode() + "?="
    r = _post(
        client,
        "tools/call",
        {"name": "query_source", "arguments": {"sql": "SELECT * FROM data"}},
        headers=_headers("tools/call", wrapped),
    )
    assert r.status_code == 200, r.text


def test_a_repeated_routing_header_is_refused(client: TestClient) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"_meta": META},
    }
    r = client.post(
        "/v1/mcp",
        content=json.dumps(body),
        headers=[
            ("content-type", "application/json"),
            ("authorization", KEY["Authorization"]),
            ("mcp-protocol-version", V),
            ("mcp-method", "tools/list"),
            ("mcp-method", "tools/call"),
        ],
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32020)


@pytest.mark.parametrize(
    "meta",
    [
        {"io.modelcontextprotocol/protocolVersion": V},
        {"io.modelcontextprotocol/clientCapabilities": {}},
    ],
)
def test_the_envelope_needs_version_and_capabilities(
    client: TestClient, meta: dict
) -> None:
    r = client.post(
        "/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": meta},
        },
        headers=_headers("tools/list"),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32602)


def test_an_unknown_version_names_the_supported_ones(client: TestClient) -> None:
    meta = {**META, "io.modelcontextprotocol/protocolVersion": "2099-01-01"}
    r = client.post(
        "/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": meta},
        },
        headers={**_headers("tools/list"), "MCP-Protocol-Version": "2099-01-01"},
    )
    assert r.status_code == 400
    error = r.json()["error"]
    assert error["code"] == -32022
    assert error["data"] == {"supported": [V], "requested": "2099-01-01"}


@pytest.mark.parametrize("method", ["initialize", "ping", "resources/list"])
def test_an_unknown_method_is_404_before_auth(client: TestClient, method: str) -> None:
    r = _post(client, method, auth=False)
    assert (r.status_code, r.json()["error"]["code"]) == (404, -32601)


def test_tools_need_a_key(client: TestClient) -> None:
    r = _post(client, "tools/list", auth=False)
    assert r.json()["error"]["code"] == -32001


def test_an_unknown_tool_is_invalid_params_not_404(client: TestClient) -> None:
    r = _post(client, "tools/call", {"name": "nope", "arguments": {}})
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32602)


def test_a_notification_is_acknowledged(client: TestClient) -> None:
    r = client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
        headers={"MCP-Protocol-Version": V},
    )
    assert r.status_code == 202


def test_a_notification_at_an_unknown_version_is_refused(client: TestClient) -> None:
    r = client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/cancelled"},
        headers={"MCP-Protocol-Version": "2099-01-01"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, -32022)


def test_the_handshake_era_still_works_after_modern_requests(
    client: TestClient,
) -> None:
    assert _call(client).status_code == 200
    r = client.post(
        "/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
    )
    assert r.json()["result"]["protocolVersion"] == "2025-11-25"
    legacy = client.post(
        "/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "query_source",
                "arguments": {"sql": "SELECT * FROM data"},
            },
        },
        headers=KEY,
    ).json()["result"]
    assert set(legacy) == {"content"}
