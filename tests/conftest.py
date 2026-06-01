"""
Shared pytest configuration for tdb-community.

Responsibilities:
- Set ALL environment variables before any tdb.* module is imported.
  Pytest loads conftest.py before any test file, so vars set here are
  visible to every lazy env reader (get_api_keys, get_registry_db_path).
- Run SQLite migrations once on the temp test database.
- Provide the shared 'clean_registry' autouse fixture so individual
  test files don't need to manage teardown themselves.

Adding a new test file?
  - Do NOT set os.environ anywhere in the test file.
  - Do NOT call run_migrations() in the test file.
  - Just import tdb.* modules at the top — conftest guarantees the env.
"""

from __future__ import annotations

import os
import tempfile

# ── Environment — must happen before any tdb.* import ───────────────────────
# mkstemp gives us a real empty file, not just a path string like mktemp.
# We close the fd immediately; SQLite will open it separately.
_db_fd, _TEST_DB_PATH = tempfile.mkstemp(suffix="_tdb_test.db")
os.close(_db_fd)

os.environ["TDB_REGISTRY_DB"] = _TEST_DB_PATH
os.environ["TDB_API_KEYS"] = "test-key-abc,test-key-day4"
os.environ["TDB_LOG_FILE"] = "/tmp/tdb_test_audit.jsonl"

# ── Migrations — idempotent; safe to call once per session ──────────────────
# Import is deferred to here (after env vars) so the migrations module reads
# the correct TDB_REGISTRY_DB when it calls get_registry_db_path().
from tdb.registry.migrations import run_migrations  # noqa: E402

run_migrations()

# ── Shared fixtures ──────────────────────────────────────────────────────────
import pytest  # noqa: E402

from tdb.registry import store  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    """Wipe the registry before and after every test for full isolation."""
    store.clear_all()
    yield
    store.clear_all()
