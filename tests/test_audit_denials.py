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
