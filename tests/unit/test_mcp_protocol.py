"""
The MCP endpoint answers malformed and protocol-level messages the way the
spec says, rather than with a 500.

Found by probing the published 0.9.2 image: a batch array, a non-object body
and non-object `params`/`arguments` each reached `.get()` on a non-dict, and
the 500 handler returned the exception text to the caller. Notifications were
answered with "Method not found", `ping` was unknown, and `Origin` was never
checked.

Imports live inside fixtures: other test modules delete `tdb*` from
sys.modules, and a module-level import would go stale under them.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

KEY = {"Authorization": "Bearer test-key-day4"}


@pytest.fixture()
def client() -> TestClient:
    from tdb.main import app

    return TestClient(app, raise_server_exceptions=False)


def _last_audit() -> dict:
    from tdb.config import get_log_file

    with open(get_log_file()) as f:
        return json.loads(f.readlines()[-1])


@pytest.mark.parametrize(
    "body", ['[{"jsonrpc":"2.0","id":1,"method":"ping"}]', '"x"', "42"]
)
def test_a_non_object_body_is_an_invalid_request(client: TestClient, body: str) -> None:
    r = client.post(
        "/v1/mcp", content=body, headers={**KEY, "Content-Type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


@pytest.mark.parametrize(
    "params",
    [
        [],
        None,
        {"name": "query_source", "arguments": "x"},
        {"name": ["query_source"], "arguments": {}},
    ],
)
def test_non_object_params_are_invalid_params(
    client: TestClient, params: object
) -> None:
    r = client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        headers=KEY,
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


def test_a_non_string_sql_argument_is_a_tool_error(client: TestClient) -> None:
    r = client.post(
        "/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_source", "arguments": {"sql": 5}},
        },
        headers=KEY,
    )
    assert r.json()["result"]["isError"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 7, "result": {}},
    ],
)
def test_notifications_and_responses_get_202_and_no_body(
    client: TestClient, body: dict
) -> None:
    r = client.post("/v1/mcp", json=body)
    assert r.status_code == 202
    assert r.content == b""


def test_ping_answers_without_a_key(client: TestClient) -> None:
    r = client.post("/v1/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert r.json() == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_a_foreign_origin_is_refused_and_audited(client: TestClient) -> None:
    r = client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**KEY, "Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    entry = _last_audit()
    assert (entry["event"], entry["reason"]) == ("denied", "invalid_origin")


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://testserver"}])
def test_no_origin_or_our_own_origin_is_served(
    client: TestClient, headers: dict
) -> None:
    r = client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**KEY, **headers},
    )
    assert r.status_code == 200
    assert "result" in r.json()


def test_an_unhandled_error_does_not_echo_the_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tdb.routers.mcp as mcp

    def boom(*_a: object) -> dict:
        raise RuntimeError("internal detail /srv/secret.csv")

    monkeypatch.setitem(mcp._HANDLERS, "tools/list", boom)
    r = client.post(
        "/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=KEY
    )
    assert r.status_code == 500
    assert "secret" not in r.text
