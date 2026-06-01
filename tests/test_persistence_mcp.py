"""
TDB — SQLite persistence, one-source limit, schema endpoint, and MCP tests.

Covers:
  - Persistent registry (SQLite): data survives in DB, not just in memory
  - Community one-source-at-a-time limit
  - Schema inspection endpoint (GET /v1/sources/{id}/schema)
  - MCP HTTP endpoint (POST /v1/mcp, JSON-RPC 2.0)
  - Row limit enforcement at 1,000 (Pydantic validation)

Environment setup is handled entirely by tests/conftest.py.
Do not set os.environ here.

Run: pytest tests/test_persistence_mcp.py -v
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.main import app
from tdb.registry import store

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-key-day4"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_csv(tmp_path: Path) -> str:
    f = tmp_path / "sales.csv"
    with f.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "product", "amount", "country"])
        writer.writeheader()
        for i in range(5):
            writer.writerow(
                {
                    "id": i,
                    "product": f"Widget {i}",
                    "amount": i * 10.0,
                    "country": "IN",
                }
            )
    return str(f)


@pytest.fixture()
def registered_source(sample_csv):
    r = client.post(
        "/v1/sources",
        json={
            "name": "d4_source",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    return r.json()


# ---------------------------------------------------------------------------
# Community one-source limit
# ---------------------------------------------------------------------------


class TestCommunityOneSourceLimit:
    def test_first_source_accepted(self, sample_csv):
        r = client.post(
            "/v1/sources",
            json={
                "name": "first",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        )
        assert r.status_code == 201

    def test_second_source_rejected_409(self, sample_csv):
        client.post(
            "/v1/sources",
            json={
                "name": "first",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        )
        r = client.post(
            "/v1/sources",
            json={
                "name": "second",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        )
        assert r.status_code == 409
        assert "one registered source" in r.json()["detail"].lower()

    def test_delete_then_register_new_succeeds(self, sample_csv, registered_source):
        client.delete(f"/v1/sources/{registered_source['id']}", headers=HEADERS)
        r = client.post(
            "/v1/sources",
            json={
                "name": "replacement",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        )
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_registered_source_retrievable_via_store(self, registered_source):
        """Verify data is actually written to SQLite, not just held in a dict."""
        record = store.get_source(registered_source["id"])
        assert record is not None
        assert record.name == "d4_source"

    def test_clear_all_removes_from_db(self, registered_source):
        store.clear_all()
        assert store.list_sources() == []

    def test_list_sources_returns_stored_record(self, registered_source):
        records = store.list_sources()
        assert len(records) == 1
        assert records[0].id == registered_source["id"]


# ---------------------------------------------------------------------------
# Schema endpoint
# ---------------------------------------------------------------------------


class TestSchemaEndpoint:
    def test_schema_returns_200_with_columns(self, registered_source):
        r = client.get(f"/v1/sources/{registered_source['id']}/schema", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert "columns" in data
        assert "source_id" in data
        assert "source_name" in data
        assert data["source_name"] == "d4_source"

    def test_schema_column_names_match_csv(self, registered_source):
        r = client.get(f"/v1/sources/{registered_source['id']}/schema", headers=HEADERS)
        names = [c["name"] for c in r.json()["columns"]]
        assert "id" in names
        assert "product" in names
        assert "amount" in names
        assert "country" in names

    def test_schema_columns_have_type_field(self, registered_source):
        r = client.get(f"/v1/sources/{registered_source['id']}/schema", headers=HEADERS)
        for col in r.json()["columns"]:
            assert "name" in col
            assert "type" in col
            assert col["type"]  # non-empty string

    def test_schema_404_for_unknown_source(self):
        r = client.get("/v1/sources/does-not-exist/schema", headers=HEADERS)
        assert r.status_code == 404

    def test_schema_requires_auth(self, registered_source):
        r = client.get(f"/v1/sources/{registered_source['id']}/schema")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# MCP endpoint — JSON-RPC 2.0
# ---------------------------------------------------------------------------


class TestMcpEndpoint:
    def _post(self, body: dict) -> dict:
        r = client.post("/v1/mcp", json=body, headers=HEADERS)
        assert r.status_code == 200
        return r.json()

    def test_initialize_returns_server_info(self):
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        assert resp["jsonrpc"] == "2.0"
        assert resp["result"]["serverInfo"]["name"] == "tdb-community"

    def test_initialize_works_without_auth(self):
        """initialize must be reachable without a key — MCP client handshake."""
        r = client.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        )
        assert r.status_code == 200
        assert r.json()["result"]["serverInfo"]["name"] == "tdb-community"

    def test_tools_list_requires_auth(self):
        r = client.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}},
        )
        assert r.status_code == 200
        assert r.json()["error"]["code"] == -32001

    def test_tools_list_returns_exactly_one_tool(self):
        resp = self._post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        tools = resp["result"]["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "query_source"

    def test_tools_call_no_source_returns_error_content(self):
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "query_source",
                    "arguments": {"sql": "SELECT * FROM data"},
                },
            }
        )
        text = resp["result"]["content"][0]["text"].lower()
        assert "no data source" in text

    def test_tools_call_with_source_returns_rows(self, registered_source):
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "query_source",
                    "arguments": {"sql": "SELECT * FROM data LIMIT 3"},
                },
            }
        )
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["rows_returned"] == 3
        assert "columns" in payload

    def test_tools_call_blocked_sql_returns_is_error(self, registered_source):
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "query_source",
                    "arguments": {"sql": "DELETE FROM data"},
                },
            }
        )
        assert resp["result"]["isError"] is True

    def test_unknown_method_returns_json_rpc_error(self):
        resp = self._post(
            {"jsonrpc": "2.0", "id": 6, "method": "no/such/method", "params": {}}
        )
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_invalid_json_rpc_version_returns_error(self):
        resp = self._post(
            {"jsonrpc": "1.0", "id": 7, "method": "tools/list", "params": {}}
        )
        assert "error" in resp

    def test_unknown_tool_name_returns_error(self, registered_source):
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "nonexistent_tool", "arguments": {}},
            }
        )
        assert "error" in resp


# ---------------------------------------------------------------------------
# Row limit enforcement
# ---------------------------------------------------------------------------


class TestRowLimitEnforcement:
    def test_limit_above_1000_rejected_with_422(self, registered_source):
        r = client.post(
            "/v1/query",
            json={
                "source_id": registered_source["id"],
                "sql": "SELECT * FROM data",
                "limit": 1001,
            },
            headers=HEADERS,
        )
        assert r.status_code == 422

    def test_limit_at_1000_accepted(self, registered_source):
        r = client.post(
            "/v1/query",
            json={
                "source_id": registered_source["id"],
                "sql": "SELECT * FROM data",
                "limit": 1000,
            },
            headers=HEADERS,
        )
        assert r.status_code == 200

    def test_user_supplied_limit_cannot_exceed_param_cap(self, registered_source):
        """A LIMIT inside the SQL must not let the response exceed the param cap.

        The registered fixture CSV has 5 rows. Requesting param limit=2 with a
        SQL `LIMIT 99999` must still return only 2 rows — the cap is the real
        ceiling, not just a default when the SQL omits LIMIT — and the response
        must report truncated=True.
        """
        r = client.post(
            "/v1/query",
            json={
                "source_id": registered_source["id"],
                "sql": "SELECT * FROM data LIMIT 99999",
                "limit": 2,
            },
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rows_returned"] == 2
        assert body["truncated"] is True

    def test_truncated_false_when_all_rows_fit(self, registered_source):
        """When the result fits under the cap, truncated must be False.

        The fixture CSV has 5 rows; a limit of 1000 returns all of them.
        """
        r = client.post(
            "/v1/query",
            json={
                "source_id": registered_source["id"],
                "sql": "SELECT * FROM data",
                "limit": 1000,
            },
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rows_returned"] == 5
        assert body["truncated"] is False
