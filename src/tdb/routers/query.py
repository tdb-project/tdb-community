"""
TDB – Query Router

Endpoint:
    POST /query   – run a SQL SELECT against a registered source

Flow:
    1. Authenticate via API key
    2. Resolve the source from the registry
    3. Validate the SQL (read-only guard)
    4. Load the connector for the source_type
    5. Execute the query
    6. Audit-log everything
    7. Return clean JSON
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from tdb.audit.logger import get_logger, log_query
from tdb.auth.apikey import require_api_key
from tdb.connectors.csv import CsvConnector
from tdb.engine.validator import validate_sql
from tdb.models import QueryRequest, QueryResponse
from tdb.registry import store

router = APIRouter(prefix="/query", tags=["Query"])
_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Connector factory
# (Day-N: replace with a proper plugin registry / entry-points)
# ---------------------------------------------------------------------------


def _get_connector(source_type: str, connection: dict):
    """
    Return an initialised connector for the given source_type.
    Raises HTTPException 400 for unknown types.
    """
    if source_type == "csv":
        return CsvConnector(connection)
    # Future:
    # if source_type == "sqlite":  return SqliteConnector(connection)
    # if source_type == "postgres": return PostgresConnector(connection)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported source type: '{source_type}'. Supported today: ['csv']",
    )


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=QueryResponse,
    summary="Run a SQL query against a registered data source",
)
def run_query(
    body: QueryRequest,
    api_key: str = Depends(require_api_key),
) -> QueryResponse:
    """
    Execute a **read-only** SQL SELECT against a registered source.

    ### Example
    ```json
    {
      "source_id": "abc-123",
      "sql": "SELECT * FROM data LIMIT 50",
      "limit": 50
    }
    ```

    Always use `data` as the table name: the registered CSV is loaded into an
    in-memory table under that fixed name, regardless of the source name or the
    file name.
    """
    key_hint = api_key[:6] + "..." if api_key else ""

    # 1. Resolve source
    source = store.get_source(body.source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{body.source_id}' not found.",
        )

    # 2. Validate SQL (read-only guard)
    validation = validate_sql(body.sql)
    if not validation.is_valid:
        _log.warning(
            "query_rejected — source_id=%s reason=%s",
            body.source_id,
            validation.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SQL validation failed: {validation.reason}",
        )

    # 3. Get connector
    connector = _get_connector(source.source_type, source.connection)

    # 4. Execute
    try:
        result = connector.execute(body.sql, limit=body.limit)
    except FileNotFoundError as exc:
        # The source's backing file is gone/unreadable (e.g. removed after
        # registration). This is a source-availability problem, not a server
        # fault — return 503 (matching the schema endpoint) and don't echo the
        # absolute server path back to the caller (issue #7).
        _log.warning(
            "query_source_unavailable source_id=%s error=%s",
            body.source_id,
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Source '{source.name}' is not accessible — its underlying "
                "file is missing or unreadable."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _log.error(
            "query_execution_error source_id=%s error=%s",
            body.source_id,
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {exc}",
        ) from exc

    executed_at = datetime.now(UTC)

    # 5. Audit log — key_hint written to NDJSON only, not to the Python logger
    _log.info(
        "query_executed source_id=%s source=%s rows=%d sql_len=%d",
        body.source_id,
        source.name,
        len(result.rows),
        len(body.sql),
    )
    log_query(
        source_id=body.source_id,
        sql=body.sql,
        rows_returned=len(result.rows),
        key_hint=key_hint,
    )

    # 6. Respond
    return QueryResponse(
        source_id=body.source_id,
        sql=body.sql,
        rows_returned=len(result.rows),
        columns=result.columns,
        rows=result.rows,
        truncated=result.truncated,
        executed_at=executed_at,
    )
