"""
TDB – Source Registry Store (Day-4: SQLite-backed)

Public interface is identical to the Day-3 in-memory version.
All callers (routers, tests) import these functions without change.

Community Edition rule enforced here: one registered source at a time.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from tdb.models import SourceRecord, SourceRegistrationRequest
from tdb.registry.migrations import get_connection


def _row_to_record(row: object) -> SourceRecord:
    return SourceRecord(
        id=row["id"],
        name=row["name"],
        source_type=row["source_type"],
        connection=json.loads(row["connection"]),
        description=row["description"] or "",
        tags=json.loads(row["tags"]),
        registered_by=row["registered_by"],
        registered_at=datetime.fromisoformat(row["registered_at"]),
    )


def register_source(req: SourceRegistrationRequest, registered_by: str) -> SourceRecord:
    """
    Create and persist a new SourceRecord.

    Community Edition: only one source may be registered at a time.
    Raises ValueError if any source already exists, or if the name is taken.
    """
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        if count >= 1:
            raise ValueError(
                "Community Edition supports one registered source at a time. "
                "Delete the existing source before registering a new one."
            )

        dup = conn.execute(
            "SELECT id FROM sources WHERE lower(name) = lower(?)", (req.name,)
        ).fetchone()
        if dup:
            raise ValueError(f"A source named '{req.name}' is already registered.")

        record = SourceRecord(
            id=str(uuid.uuid4()),
            name=req.name,
            source_type=req.source_type,
            connection=req.connection,
            description=req.description,
            tags=req.tags,
            registered_by=registered_by,
            registered_at=datetime.now(UTC),
        )

        source_type_value = (
            record.source_type.value
            if hasattr(record.source_type, "value")
            else str(record.source_type)
        )
        conn.execute(
            """INSERT INTO sources
               (id, name, source_type, connection,
                description, tags, registered_by, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.name,
                source_type_value,
                json.dumps(record.connection),
                record.description or "",
                json.dumps(record.tags),
                record.registered_by,
                record.registered_at.isoformat(),
            ),
        )
        conn.commit()
        return record
    finally:
        conn.close()


def get_source(source_id: str) -> SourceRecord | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return _row_to_record(row) if row else None
    finally:
        conn.close()


def get_source_by_ref(ref: str) -> SourceRecord | None:
    """Resolve by UUID (exact match) or by name (case-insensitive)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ? OR lower(name) = lower(?)",
            (ref, ref),
        ).fetchone()
        return _row_to_record(row) if row else None
    finally:
        conn.close()


def list_sources() -> list[SourceRecord]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM sources ORDER BY registered_at").fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def remove_source(source_id: str) -> bool:
    """Delete by UUID only. Callers should resolve names via get_source_by_ref first."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def clear_all() -> None:
    """Wipe the store. Used in tests only."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sources")
        conn.commit()
    finally:
        conn.close()
