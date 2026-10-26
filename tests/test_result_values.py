"""
REST and MCP return the same JSON for every value a CSV query can produce (P15).

Before 0.12.2 a binary value such as `from_hex('00ff')` made `POST /v1/query`
fail to encode after the query ran and was audited; NaN was `null` on REST and a
bare `NaN` on MCP, which strict JSON parsers reject; timestamps and intervals
differed by path.

Environment setup is handled entirely by tests/conftest.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-key-abc"}

_EXPRS = [
    "from_hex('00ff')",
    "'nan'::DOUBLE",
    "'inf'::DOUBLE",
    "0.10::DECIMAL(10,2)",
    "TIMESTAMP '2026-01-01 10:00:00.5'",
    "INTERVAL 1 DAY",
    "{'b': from_hex('00ff')}",
]


def _strict(text: str):
    def refuse(token: str):
        raise ValueError(f"not JSON: {token}")

    return json.loads(text, parse_constant=refuse)


@pytest.fixture()
def source_id(tmp_path: Path) -> str:
    f = tmp_path / "one.csv"
    f.write_text("a\n1\n")
    r = client.post(
        "/v1/sources",
        headers=HEADERS,
        json={"name": "c", "source_type": "csv", "connection": {"file_path": str(f)}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("expr", _EXPRS)
def test_rest_and_mcp_agree(source_id: str, expr: str) -> None:
    sql = f"SELECT {expr} AS v FROM data"
    rest = client.post(
        "/v1/query", headers=HEADERS, json={"source_id": source_id, "sql": sql}
    )
    assert rest.status_code == 200, rest.text
    mcp = client.post(
        "/v1/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_source", "arguments": {"sql": sql}},
        },
    )
    body = _strict(mcp.text)
    rows = _strict(body["result"]["content"][0]["text"])["rows"]
    assert rows == _strict(rest.text)["rows"]
