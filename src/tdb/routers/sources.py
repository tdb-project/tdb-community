"""
TDB – Source Registry Router

Endpoints:
    POST   /sources          – register a new data source
    GET    /sources          – list all registered sources
    GET    /sources/{id}     – get a single source (full detail)
    DELETE /sources/{id}     – remove a source
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from tdb.audit.logger import get_logger
from tdb.auth.apikey import require_api_key
from tdb.connectors.csv import CsvConnector
from tdb.models import (
    SchemaColumn,
    SourceRecord,
    SourceRegistrationRequest,
    SourceSchema,
    SourceSummary,
)
from tdb.registry import store

router = APIRouter(prefix="/sources", tags=["Source Registry"])
_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# POST /sources
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=SourceRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new data source",
)
def register_source(
    body: SourceRegistrationRequest,
    api_key: str = Depends(require_api_key),
) -> SourceRecord:
    """
    Register a data source so it can be queried through TDB.

    **source_type** values supported today:
    - `csv` — local CSV file; connection: `{ "file_path": "/path/to/file.csv" }`

    More connector types come in later days.
    """
    key_hint = api_key[:6] + "..." if api_key else ""

    # Validate the connection before persisting, so a missing/unreadable file
    # fails fast with a clear 4xx instead of registering successfully and only
    # surfacing as a 500 at query time (issue #7).
    try:
        connector = CsvConnector(body.connection)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if not connector.path_is_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="file_path is outside the allowed data directory.",
        )
    if not connector.validate_connection():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "CSV file not found or not readable: "
                f"{body.connection.get('file_path')}"
            ),
        )

    try:
        record = store.register_source(body, registered_by=key_hint)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    _log.info(
        "source_registered source_id=%s name=%s source_type=%s",
        record.id,
        record.name,
        record.source_type,
    )
    return record


# ---------------------------------------------------------------------------
# GET /sources
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[SourceSummary],
    summary="List all registered sources",
)
def list_sources(
    _api_key: str = Depends(require_api_key),
) -> list[SourceSummary]:
    records = store.list_sources()
    return [
        SourceSummary(
            id=r.id,
            name=r.name,
            source_type=r.source_type,
            description=r.description,
            tags=r.tags,
            registered_at=r.registered_at,
        )
        for r in records
    ]


# ---------------------------------------------------------------------------
# GET /sources/{source_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{source_id}",
    response_model=SourceRecord,
    summary="Get details of a specific source",
)
def get_source(
    source_id: str,
    _api_key: str = Depends(require_api_key),
) -> SourceRecord:
    record = store.get_source(source_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found.",
        )
    return record


# ---------------------------------------------------------------------------
# DELETE /sources/{source_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a registered source",
)
def delete_source(
    source_id: str,
    _api_key: str = Depends(require_api_key),
) -> None:
    deleted = store.remove_source(source_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found.",
        )
    _log.info("source_deleted source_id=%s", source_id)


# ---------------------------------------------------------------------------
# GET /sources/{source_id}/schema
# ---------------------------------------------------------------------------


@router.get(
    "/{source_id}/schema",
    response_model=SourceSchema,
    summary="Inspect column names and types for a registered source",
)
def get_source_schema(
    source_id: str,
    _api_key: str = Depends(require_api_key),
) -> SourceSchema:
    """
    Returns the column names and inferred DuckDB types for the registered CSV.
    No rows are returned — schema introspection only.
    """
    record = store.get_source(source_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found.",
        )

    connector = CsvConnector(record.connection)
    if not connector.path_is_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This source's file is outside the allowed data directory.",
        )
    if not connector.validate_connection():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"CSV file is not accessible: {record.connection.get('file_path')}",
        )

    schema_dict = connector.get_schema()

    _log.info(
        "schema_inspected source_id=%s column_count=%d",
        source_id,
        len(schema_dict),
    )

    return SourceSchema(
        source_id=source_id,
        source_name=record.name,
        columns=[
            SchemaColumn(name=col, type=dtype) for col, dtype in schema_dict.items()
        ],
        inspected_at=datetime.now(UTC),
    )
