"""
No built-in API key. A default credential on an open port was the server's behaviour
whenever the variable was forgotten, and a printed warning did not stop that.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tdb.main import app

_ROOT = Path(__file__).resolve().parent.parent
_PLACEHOLDERS = [
    "dev-insecure-key-change-me",
    "CHANGE-ME-IN-PRODUCTION",
    "your-secret-key",
    "your-secret-key-here",
]


def _start() -> None:
    with TestClient(app):
        pass


def test_startup_refuses_when_the_variable_is_unset(monkeypatch, capsys):
    monkeypatch.delenv("TDB_API_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="TDB_API_KEYS is not set"):
        _start()
    assert "token_hex" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["", "   ", " , ,"])
def test_startup_refuses_an_empty_value(monkeypatch, value):
    monkeypatch.setenv("TDB_API_KEYS", value)
    with pytest.raises(RuntimeError, match="TDB_API_KEYS is not set"):
        _start()


@pytest.mark.parametrize("value", _PLACEHOLDERS)
def test_startup_refuses_an_example_value(monkeypatch, value):
    monkeypatch.setenv("TDB_API_KEYS", value)
    with pytest.raises(RuntimeError, match="anyone can guess"):
        _start()


def test_one_example_value_among_real_keys_is_still_refused(monkeypatch):
    monkeypatch.setenv("TDB_API_KEYS", "a-real-key, dev-insecure-key-change-me")
    with pytest.raises(RuntimeError, match="anyone can guess"):
        _start()


def test_startup_succeeds_with_a_real_key(monkeypatch):
    monkeypatch.setenv("TDB_API_KEYS", "a-real-key")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def _sources(key: str) -> int:
    response = TestClient(app).get(
        "/v1/sources", headers={"Authorization": f"Bearer {key}"}
    )
    return response.status_code


@pytest.mark.parametrize("key", _PLACEHOLDERS)
def test_an_example_value_is_never_a_credential(monkeypatch, key):
    """Even if startup was skipped, as it is here: no lifespan runs."""
    monkeypatch.setenv("TDB_API_KEYS", f"{key},a-real-key")
    assert _sources(key) == 401
    assert _sources("a-real-key") == 200


def test_the_old_default_does_not_work_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv("TDB_API_KEYS", raising=False)
    assert _sources("dev-insecure-key-change-me") == 401


def test_mcp_refuses_it_too(monkeypatch):
    monkeypatch.delenv("TDB_API_KEYS", raising=False)
    response = TestClient(app).post(
        "/v1/mcp",
        headers={"Authorization": "Bearer dev-insecure-key-change-me"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.json()["error"]["code"] == -32001


def test_the_compose_file_requires_the_variable():
    text = (_ROOT / "docker-compose.yml").read_text()
    assert re.search(r"TDB_API_KEYS: \"\$\{TDB_API_KEYS:\?", text)
    assert "dev-insecure" not in text


def test_compose_refuses_without_the_variable():
    docker = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False
    )
    if docker.returncode != 0:
        pytest.skip("docker compose is not available")
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": subprocess.os.environ["PATH"], "HOME": str(Path.home())},
        check=False,
    )
    assert result.returncode != 0
    assert "Set TDB_API_KEYS" in result.stderr


def test_the_env_example_has_no_value():
    assert re.search(r"^TDB_API_KEYS=$", (_ROOT / ".env.example").read_text(), re.M)
