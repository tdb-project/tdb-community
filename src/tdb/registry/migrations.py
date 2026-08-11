"""
TDB – Registry Migrations

Creates the SQLite schema for the source registry.
Call run_migrations() once on server startup — it is idempotent.
"""

from __future__ import annotations

import os
import sqlite3

from tdb.config import get_registry_db_path

_CREATE_SOURCES = """
CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    source_type   TEXT NOT NULL,
    connection    TEXT NOT NULL,
    description   TEXT,
    tags          TEXT NOT NULL DEFAULT '[]',
    registered_by TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    path = get_registry_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def run_migrations() -> None:
    """Idempotent schema setup, run once at startup.

    Wrapped so an unopenable or unwritable registry fails with an error that
    explains itself. The most common cause is a bind mount: `-v ./data:/app/data`
    where the host dir does not exist, so Docker creates it root-owned and the
    non-root container user cannot write inside it. SQLite's own message
    ("unable to open database file") names neither the path nor the cause, and
    this failure kills the container at startup — so this is the one place the
    error must say what to do.
    """
    try:
        _run_migrations()
    except (sqlite3.OperationalError, PermissionError, OSError) as exc:
        path = os.path.abspath(get_registry_db_path())
        raise RuntimeError(
            f"cannot open or write the registry database at {path!r}: {exc}. "
            f"TDB runs as a non-root user (uid {os.getuid()}); if the parent "
            "directory is bind-mounted, create it on the host BEFORE "
            "`docker run` (e.g. `mkdir -p data`) or chown it to that uid — "
            "a directory Docker auto-creates for a mount is owned by root."
        ) from exc


def _run_migrations() -> None:
    conn = get_connection()
    try:
        conn.execute(_CREATE_SOURCES)
        conn.commit()
    finally:
        conn.close()
