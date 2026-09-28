"""
TDB — audit coverage of denied and blocked attempts.

The audit log's promise is "every query logged". Successful queries write an
`event: "query"` line; anything refused must write an `event: "denied"` line
with a machine-readable `reason`, so a reviewer can answer "who tried what and
was turned away" from the audit file alone.

Environment setup is handled entirely by tests/conftest.py.
Do not set os.environ here.

Run with:  pytest tests/test_audit_denials.py -v
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.config import get_log_file
from tdb.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-key-abc"}
BAD_HEADERS = {"Authorization": "Bearer wrong-key-xyz"}


@pytest.fixture()
def sample_csv(tmp_path: Path) -> str:
    csv_file = tmp_path / "sales.csv"
    with csv_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "product"])
        writer.writeheader()
        writer.writerows([{"id": 1, "product": "Widget A"}])
    return str(csv_file)


@pytest.fixture()
def audit_lines():
    """Truncate the audit log, then read back the entries a test produced."""
    path = get_log_file()
    Path(path).write_text("")

    def _read() -> list[dict]:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    return _read


def _denials(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e["event"] == "denied"]


class TestAuthDenials:
    def test_invalid_api_key_is_audited(self, audit_lines):
        client.get("/v1/sources", headers=BAD_HEADERS)
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["action"] == "auth"
        assert denied[0]["reason"] == "invalid_api_key"

    def test_missing_api_key_is_audited(self, audit_lines):
        client.get("/v1/sources")
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["reason"] == "missing_api_key"

    def test_denial_records_key_hint_not_the_key(self, audit_lines):
        client.get("/v1/sources", headers=BAD_HEADERS)
        entry = _denials(audit_lines())[0]
        assert entry["key_hint"] == "wrong-..."
        assert "wrong-key-xyz" not in json.dumps(entry)

    def test_valid_key_writes_no_denial(self, audit_lines):
        client.get("/v1/sources", headers=HEADERS)
        assert _denials(audit_lines()) == []


class TestQueryDenials:
    def test_non_select_sql_is_audited(self, audit_lines, sample_csv):
        payload = {
            "name": "src_write",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
        }
        client.post("/v1/sources", json=payload, headers=HEADERS)
        client.post(
            "/v1/query",
            json={"source_id": "src_write", "sql": "DROP TABLE data"},
            headers=HEADERS,
        )
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["action"] == "query"
        assert denied[0]["reason"] == "sql_validation_failed"
        assert denied[0]["sql"] == "DROP TABLE data"

    def test_unknown_source_is_audited(self, audit_lines):
        client.post(
            "/v1/query",
            json={"source_id": "nope", "sql": "SELECT 1"},
            headers=HEADERS,
        )
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["reason"] == "source_not_found"
        assert denied[0]["source_id"] == "nope"

    def test_successful_query_writes_query_not_denied(self, audit_lines, sample_csv):
        payload = {
            "name": "src_ok",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
        }
        client.post("/v1/sources", json=payload, headers=HEADERS)
        r = client.post(
            "/v1/query",
            json={"source_id": "src_ok", "sql": "SELECT * FROM data"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        entries = audit_lines()
        assert _denials(entries) == []
        assert [e for e in entries if e["event"] == "query"]


class TestRegisterDenials:
    def test_unreadable_file_is_audited(self, audit_lines):
        payload = {
            "name": "ghost",
            "source_type": "csv",
            "connection": {"file_path": "/nonexistent/ghost.csv"},
        }
        r = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert r.status_code == 400
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["action"] == "register"
        assert denied[0]["reason"] == "file_unreadable"

    def test_duplicate_name_conflict_is_audited(self, audit_lines, sample_csv):
        payload = {
            "name": "dupe",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
        }
        first = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert first.status_code == 201
        r = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert r.status_code == 409
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["reason"] == "registry_conflict"


class TestMcpDenials:
    def test_unauthorized_mcp_call_is_audited(self, audit_lines):
        client.post(
            "/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "query_source", "arguments": {"sql": "SELECT 1"}},
            },
            headers=BAD_HEADERS,
        )
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["action"] == "mcp_auth"
        assert denied[0]["reason"] == "invalid_api_key"

    def test_mcp_sql_validation_failure_is_audited(self, audit_lines):
        client.post(
            "/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "query_source",
                    "arguments": {"sql": "DELETE FROM data"},
                },
            },
            headers=HEADERS,
        )
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["action"] == "mcp_query"
        assert denied[0]["reason"] == "sql_validation_failed"

    def test_mcp_read_of_a_file_outside_the_data_dir_is_audited(
        self, audit_lines, sample_csv, tmp_path_factory
    ):
        """The MCP path is the one an AI agent takes, so it refuses and audits
        the same file read the REST path does (P9)."""
        secret = tmp_path_factory.mktemp("elsewhere") / "secret.csv"
        secret.write_text("k\nMCP-SECRET\n")
        payload = {
            "name": "src_mcp_file",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
        }
        client.post("/v1/sources", json=payload, headers=HEADERS)
        r = client.post(
            "/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "query_source",
                    "arguments": {
                        "sql": f"SELECT * FROM read_csv('{secret}')",
                        "source_name": "src_mcp_file",
                    },
                },
            },
            headers=HEADERS,
        )
        assert "MCP-SECRET" not in r.text
        denied = _denials(audit_lines())
        assert len(denied) == 1
        assert denied[0]["action"] == "mcp_query"
        assert denied[0]["reason"] == "sql_file_access"


class TestAuditSourceIdIsTheUuid:
    """The audit trail's source_id is the registered source's UUID, whatever
    ref the caller used — a by-name query must not be invisible to a by-UUID
    grep (e2e pass 2026-08-11)."""

    def _register(self, sample_csv: str) -> str:
        r = client.post(
            "/v1/sources",
            headers=HEADERS,
            json={
                "name": "uuid_check",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_query_by_name_logs_the_uuid(self, sample_csv, audit_lines) -> None:
        uuid = self._register(sample_csv)

        r = client.post(
            "/v1/query",
            headers=HEADERS,
            json={"source_id": "uuid_check", "sql": "SELECT * FROM data", "limit": 5},
        )

        assert r.status_code == 200, r.text
        assert audit_lines()[-1]["source_id"] == uuid

    def test_denial_after_resolution_logs_the_uuid(
        self, sample_csv, audit_lines
    ) -> None:
        uuid = self._register(sample_csv)

        r = client.post(
            "/v1/query",
            headers=HEADERS,
            json={"source_id": "uuid_check", "sql": "DROP TABLE data", "limit": 5},
        )

        assert r.status_code == 400
        entry = audit_lines()[-1]
        assert entry["reason"] == "sql_validation_failed"
        assert entry["source_id"] == uuid
