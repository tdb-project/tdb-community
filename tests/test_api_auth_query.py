"""
TDB — API auth, source registry, SQL validator, and end-to-end query tests.

Covers:
  - API key auth (missing, invalid, valid)
  - Source Registry CRUD
  - SQL Validator
  - End-to-end CSV query

Environment setup is handled entirely by tests/conftest.py.
Do not set os.environ here.

Run with:  pytest tests/test_api_auth_query.py -v
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.engine.validator import validate_sql
from tdb.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-key-abc"}
BAD_HEADERS = {"Authorization": "Bearer wrong-key"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_csv(tmp_path: Path) -> str:
    """Create a small CSV file and return its absolute path."""
    csv_file = tmp_path / "sales.csv"
    with csv_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "product", "amount", "country"])
        writer.writeheader()
        writer.writerows(
            [
                {"id": 1, "product": "Widget A", "amount": 100.0, "country": "IN"},
                {"id": 2, "product": "Widget B", "amount": 200.5, "country": "US"},
                {"id": 3, "product": "Widget C", "amount": 50.0, "country": "IN"},
            ]
        )
    return str(csv_file)


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestAuth:
    def test_missing_auth_header_returns_401(self):
        r = client.get("/v1/sources")
        assert r.status_code == 401

    def test_invalid_key_returns_401(self):
        r = client.get("/v1/sources", headers=BAD_HEADERS)
        assert r.status_code == 401

    def test_valid_key_is_accepted(self):
        r = client.get("/v1/sources", headers=HEADERS)
        assert r.status_code == 200

    def test_health_needs_no_auth(self):
        r = client.get("/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Source Registry tests
# ---------------------------------------------------------------------------


class TestSourceRegistry:
    def test_register_csv_source(self, sample_csv):
        payload = {
            "name": "sales_data",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
            "description": "Q1 sales",
            "tags": ["sales", "csv"],
        }
        r = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "sales_data"
        assert "id" in data

    def test_list_sources_empty(self):
        r = client.get("/v1/sources", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_list_sources_after_register(self, sample_csv):
        client.post(
            "/v1/sources",
            json={
                "name": "s1",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        )
        r = client.get("/v1/sources", headers=HEADERS)
        assert len(r.json()) == 1

    def test_duplicate_name_returns_409(self, sample_csv):
        payload = {
            "name": "dup",
            "source_type": "csv",
            "connection": {"file_path": sample_csv},
        }
        client.post("/v1/sources", json=payload, headers=HEADERS)
        r = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert r.status_code == 409

    def test_register_nonexistent_file_returns_400(self, tmp_path):
        payload = {
            "name": "ghost",
            "source_type": "csv",
            "connection": {"file_path": str(tmp_path / "nope.csv")},
        }
        r = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert r.status_code == 400
        # the bad source must not have been persisted
        assert client.get("/v1/sources", headers=HEADERS).json() == []

    def test_register_missing_file_path_returns_400(self):
        payload = {
            "name": "nofp",
            "source_type": "csv",
            "connection": {},
        }
        r = client.post("/v1/sources", json=payload, headers=HEADERS)
        assert r.status_code == 400

    def test_get_source_by_id(self, sample_csv):
        reg = client.post(
            "/v1/sources",
            json={
                "name": "s2",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        ).json()
        r = client.get(f"/v1/sources/{reg['id']}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["id"] == reg["id"]

    def test_get_nonexistent_source_returns_404(self):
        r = client.get("/v1/sources/does-not-exist", headers=HEADERS)
        assert r.status_code == 404

    def test_delete_source(self, sample_csv):
        reg = client.post(
            "/v1/sources",
            json={
                "name": "to_delete",
                "source_type": "csv",
                "connection": {"file_path": sample_csv},
            },
            headers=HEADERS,
        ).json()
        r = client.delete(f"/v1/sources/{reg['id']}", headers=HEADERS)
        assert r.status_code == 204
        r2 = client.get(f"/v1/sources/{reg['id']}", headers=HEADERS)
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# SQL Validator tests
# ---------------------------------------------------------------------------


class TestSqlValidator:
    def test_valid_select(self):
        assert validate_sql("SELECT * FROM data").is_valid

    def test_empty_sql_invalid(self):
        assert not validate_sql("").is_valid
        assert not validate_sql("   ").is_valid

    def test_non_select_invalid(self):
        assert not validate_sql("INSERT INTO data VALUES (1)").is_valid
        assert not validate_sql("UPDATE data SET x=1").is_valid
        assert not validate_sql("DROP TABLE data").is_valid
        assert not validate_sql("DELETE FROM data").is_valid

    def test_blocked_keywords_in_subquery(self):
        sql = "SELECT * FROM (DELETE FROM data RETURNING *) AS sub"
        assert not validate_sql(sql).is_valid

    def test_select_with_where_is_valid(self):
        assert validate_sql("SELECT id, amount FROM data WHERE country = 'IN'").is_valid


# ---------------------------------------------------------------------------
# End-to-end query tests
# ---------------------------------------------------------------------------


class TestQuery:
    def _register(self, name: str, file_path: str) -> str:
        r = client.post(
            "/v1/sources",
            json={
                "name": name,
                "source_type": "csv",
                "connection": {"file_path": file_path},
            },
            headers=HEADERS,
        )
        assert r.status_code == 201
        return r.json()["id"]

    def test_basic_select_all(self, sample_csv):
        source_id = self._register("sales", sample_csv)
        r = client.post(
            "/v1/query",
            json={"source_id": source_id, "sql": "SELECT * FROM data", "limit": 100},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["rows_returned"] == 3
        assert "id" in data["columns"]
        assert "product" in data["columns"]

    def test_select_with_filter(self, sample_csv):
        source_id = self._register("sales_filter", sample_csv)
        r = client.post(
            "/v1/query",
            json={
                "source_id": source_id,
                "sql": "SELECT * FROM data WHERE country = 'IN'",
                "limit": 100,
            },
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["rows_returned"] == 2
        for row in data["rows"]:
            assert row["country"] == "IN"

    def test_limit_is_applied(self, sample_csv):
        source_id = self._register("sales_limit", sample_csv)
        r = client.post(
            "/v1/query",
            json={"source_id": source_id, "sql": "SELECT * FROM data", "limit": 1},
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["rows_returned"] == 1

    def test_query_unknown_source_returns_404(self):
        r = client.post(
            "/v1/query",
            json={"source_id": "ghost-id", "sql": "SELECT 1", "limit": 1},
            headers=HEADERS,
        )
        assert r.status_code == 404

    def test_write_sql_is_rejected(self, sample_csv):
        source_id = self._register("sales_write", sample_csv)
        r = client.post(
            "/v1/query",
            json={
                "source_id": source_id,
                "sql": "DELETE FROM data",
                "limit": 10,
            },
            headers=HEADERS,
        )
        assert r.status_code == 400

    def test_query_after_file_removed_returns_503(self, tmp_path):
        csv_file = tmp_path / "ephemeral.csv"
        csv_file.write_text("id,name\n1,Alice\n")
        source_id = self._register("ephemeral", str(csv_file))
        csv_file.unlink()  # file disappears after a successful registration
        r = client.post(
            "/v1/query",
            json={"source_id": source_id, "sql": "SELECT * FROM data", "limit": 10},
            headers=HEADERS,
        )
        assert r.status_code == 503
        # the absolute server path must not be leaked in the error detail
        assert str(csv_file) not in r.json()["detail"]


# ---------------------------------------------------------------------------
# CSV path confinement (TDB_ALLOWED_DATA_DIR) — issue #6
# ---------------------------------------------------------------------------


class TestPathConfinement:
    @staticmethod
    def _register(name: str, file_path: str):
        return client.post(
            "/v1/sources",
            json={
                "name": name,
                "source_type": "csv",
                "connection": {"file_path": file_path},
            },
            headers=HEADERS,
        )

    def test_unset_allows_any_path(self, tmp_path):
        # No TDB_ALLOWED_DATA_DIR set -> current behavior, arbitrary path allowed.
        f = tmp_path / "anywhere.csv"
        f.write_text("id\n1\n")
        assert self._register("anyplace", str(f)).status_code == 201

    def test_register_outside_allowed_dir_returns_403(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside.csv"
        outside.write_text("id\n1\n")
        monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(allowed))
        r = self._register("out", str(outside))
        assert r.status_code == 403
        # nothing should have been persisted
        assert client.get("/v1/sources", headers=HEADERS).json() == []

    def test_register_inside_allowed_dir_ok_and_queryable(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        inside = allowed / "data.csv"
        inside.write_text("id,name\n1,Alice\n")
        monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(allowed))
        reg = self._register("inside", str(inside))
        assert reg.status_code == 201
        q = client.post(
            "/v1/query",
            json={"source_id": reg.json()["id"], "sql": "SELECT * FROM data"},
            headers=HEADERS,
        )
        assert q.status_code == 200
        assert q.json()["rows_returned"] == 1

    def test_symlink_escape_is_blocked(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        secret = tmp_path / "secret.csv"
        secret.write_text("id\n42\n")
        link = allowed / "link.csv"
        link.symlink_to(secret)  # lives inside allowed/, but resolves outside
        monkeypatch.setenv("TDB_ALLOWED_DATA_DIR", str(allowed))
        assert self._register("link", str(link)).status_code == 403
