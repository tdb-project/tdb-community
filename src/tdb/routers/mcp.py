"""
TDB MCP-compatible endpoint — JSON-RPC 2.0 over HTTP.

POST /v1/mcp

Supported methods:
    initialize      — MCP handshake (unauthenticated — allows discovery)
    tools/list      — returns the single query_source tool spec (requires auth)
    tools/call      — executes the query_source tool (requires auth)

Community Edition: exactly one tool exposed (query_source).
Auth: Bearer token via Authorization header, same key(s) as REST endpoints.
initialize is unauthenticated so MCP clients can complete the handshake before
presenting credentials.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tdb import __version__
from tdb.audit.logger import get_logger, log_query
from tdb.config import get_api_keys
from tdb.connectors.csv import CsvConnector
from tdb.engine.validator import validate_sql
from tdb.registry import store

router = APIRouter(prefix="/mcp", tags=["MCP"])
_log = get_logger(__name__)

_QUERY_SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": (
                "SQL SELECT statement. Use 'data' as the table name. "
                "Example: SELECT * FROM data WHERE country = 'IN' LIMIT 10"
            ),
        },
        "source_name": {
            "type": "string",
            "description": (
                "Optional. Name of the registered source. "
                "Defaults to the only registered source."
            ),
        },
    },
    "required": ["sql"],
}

_TOOL_SPEC = {
    "name": "query_source",
    "description": (
        "Run a SQL SELECT query against the registered TDB data source. "
        "Use 'data' as the table name. Maximum 1,000 rows returned."
    ),
    "inputSchema": _QUERY_SOURCE_SCHEMA,
}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _ok(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_error(request_id: Any, text: str) -> dict:
    return _ok(
        request_id, {"content": [{"type": "text", "text": text}], "isError": True}
    )


def _tool_ok(request_id: Any, payload: dict) -> dict:
    return _ok(
        request_id,
        {"content": [{"type": "text", "text": json.dumps(payload, default=str)}]},
    )


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------


def _handle_initialize(request_id: Any, _params: dict) -> dict:
    return _ok(
        request_id,
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tdb-community", "version": __version__},
        },
    )


def _handle_tools_list(request_id: Any, _params: dict) -> dict:
    return _ok(request_id, {"tools": [_TOOL_SPEC]})


def _handle_tools_call(request_id: Any, params: dict, api_key: str = "") -> dict:
    if params.get("name") != "query_source":
        return _err(request_id, -32601, f"Unknown tool: {params.get('name')}")

    args = params.get("arguments", {})
    sql = args.get("sql", "").strip()
    source_name = args.get("source_name")

    validation = validate_sql(sql)
    if not validation.is_valid:
        return _tool_error(request_id, f"SQL validation error: {validation.reason}")

    sources = store.list_sources()
    if not sources:
        return _tool_error(
            request_id, "No data source is registered. Use 'tdb register' first."
        )

    if source_name:
        matching = [s for s in sources if s.name == source_name]
        if not matching:
            names = [s.name for s in sources]
            return _tool_error(
                request_id, f"Source '{source_name}' not found. Available: {names}"
            )
        source = matching[0]
    else:
        source = sources[0]

    try:
        connector = CsvConnector(source.connection)
        result = connector.execute(sql, limit=1000)
    except Exception as exc:
        _log.error("mcp_query_error — %s", str(exc))
        return _tool_error(request_id, f"Query execution error: {exc}")

    _log.info(
        "mcp_query_executed source_name=%s rows_returned=%d",
        source.name,
        len(result.rows),
    )
    log_query(
        source_id=source.id,
        sql=sql,
        rows_returned=len(result.rows),
        key_hint=api_key[:6] + "..." if api_key else "",
    )

    return _tool_ok(
        request_id,
        {
            "source": source.name,
            "columns": result.columns,
            "rows": result.rows,
            "rows_returned": len(result.rows),
            "truncated": result.truncated,
        },
    )


_HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("", include_in_schema=True, summary="MCP-compatible JSON-RPC 2.0 endpoint")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    Model Context Protocol endpoint (JSON-RPC 2.0).

    Connects Claude Desktop, Cursor, and other MCP clients directly to
    the registered data source without extra configuration.

    Supported methods: `initialize`, `tools/list`, `tools/call`
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err(None, -32700, "Parse error: invalid JSON"))

    if body.get("jsonrpc") != "2.0":
        return JSONResponse(_err(body.get("id"), -32600, "Invalid JSON-RPC version"))

    method = body.get("method")
    request_id = body.get("id")
    params = body.get("params", {})

    handler = _HANDLERS.get(method)
    if handler is None:
        return JSONResponse(_err(request_id, -32601, f"Method not found: {method}"))

    # initialize is unauthenticated (MCP handshake); all other methods require auth
    token = ""
    if method != "initialize":
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        if token not in get_api_keys():
            return JSONResponse(
                _err(request_id, -32001, "Unauthorized: invalid or missing API key")
            )

    if method == "tools/call":
        return JSONResponse(handler(request_id, params, token))
    return JSONResponse(handler(request_id, params))
