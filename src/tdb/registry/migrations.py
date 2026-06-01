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
    conn = get_connection()
    try:
        conn.execute(_CREATE_SOURCES)
        conn.commit()
    finally:
        conn.close()
