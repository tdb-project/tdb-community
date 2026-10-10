"""
TDB MCP-compatible endpoint — JSON-RPC 2.0 over HTTP.

POST /v1/mcp

Supported methods:
    initialize      — MCP handshake (unauthenticated — allows discovery)
    ping            — liveness, empty result (unauthenticated)
    tools/list      — returns the single query_source tool spec (requires auth)
    tools/call      — executes the query_source tool (requires auth)

Notifications and client responses (no `id`, or no `method`) are accepted with
202 and no body. A request carrying an `Origin` from another site is refused
with 403.

Community Edition: exactly one tool exposed (query_source).
Auth: Bearer token via Authorization header, same key(s) as REST endpoints.
initialize is unauthenticated so MCP clients can complete the handshake before
presenting credentials.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from tdb import __version__
from tdb.audit.logger import get_logger, log_denial, log_query
from tdb.config import get_api_keys
from tdb.connectors.csv import CsvConnector, SqlFileAccessError, SqlRefusedError
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


# Revisions this server speaks, newest first. 2025-03-26 is left out on
# purpose: it obliges servers to accept JSON-RPC batches, which this endpoint
# refuses. A client asking for it is answered with 2024-11-05, as before.
_SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2024-11-05")

# Tool titles, annotations and structured output are sent only to a client
# whose MCP-Protocol-Version header names 2025-06-18 or later, so a 2024-11-05
# client receives exactly what it always did.
_STRUCTURED_FROM = "2025-06-18"

_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}

_ROWS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "object"}},
        "rows_returned": {"type": "integer"},
        "truncated": {"type": "boolean"},
    },
    "required": ["source", "columns", "rows", "rows_returned", "truncated"],
}

_TOOL_METADATA = {
    "query_source": {
        "title": "Query a data source",
        "annotations": _READ_ONLY,
        "outputSchema": _ROWS_OUTPUT_SCHEMA,
    },
}


def _negotiate(requested: Any) -> str:
    """
    The version to answer `initialize` with: the requested one if supported,
    else the newest supported one older than it, so a client is never handed a
    revision newer than it asked for, else the oldest.
    """
    if isinstance(requested, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested):
        for version in _SUPPORTED_VERSIONS:
            if version <= requested:
                return version
    return _SUPPORTED_VERSIONS[-1]


def _with_structure(method: str, response: dict) -> dict:
    result = response.get("result")
    if not isinstance(result, dict):
        return response
    if method == "tools/list":
        result["tools"] = [
            {**tool, **_TOOL_METADATA.get(tool["name"], {})} for tool in result["tools"]
        ]
    elif method == "tools/call" and not result.get("isError"):
        # Parsed from the text block the client also receives, so the two can
        # never disagree.
        result["structuredContent"] = json.loads(result["content"][0]["text"])
    return response


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


def _handle_initialize(request_id: Any, params: dict) -> dict:
    version = _negotiate(params.get("protocolVersion"))
    server_info: dict = {"name": "tdb-community", "version": __version__}
    capabilities: dict = {"tools": {}}
    if version >= _STRUCTURED_FROM:
        server_info["title"] = "TDB Community"
        server_info["description"] = (
            "Governed, audited, read-only SQL over a registered CSV source."
        )
        capabilities = {"tools": {"listChanged": False}}
    return _ok(
        request_id,
        {
            "protocolVersion": version,
            "capabilities": capabilities,
            "serverInfo": server_info,
        },
    )


def _handle_ping(request_id: Any, _params: dict) -> dict:
    return _ok(request_id, {})


def _handle_tools_list(request_id: Any, _params: dict) -> dict:
    return _ok(request_id, {"tools": [_TOOL_SPEC]})


def _handle_tools_call(request_id: Any, params: dict, api_key: str = "") -> dict:
    if params.get("name") != "query_source":
        return _err(request_id, -32601, f"Unknown tool: {params.get('name')}")

    args = params.get("arguments", {})
    sql = args.get("sql", "")
    source_name = args.get("source_name")
    if not isinstance(sql, str) or not isinstance(source_name, str | None):
        return _tool_error(request_id, "'sql' and 'source_name' must be strings.")
    sql = sql.strip()

    key_hint = api_key[:6] + "..." if api_key else ""

    validation = validate_sql(sql)
    if not validation.is_valid:
        log_denial(
            action="mcp_query",
            reason="sql_validation_failed",
            sql=sql,
            key_hint=key_hint,
        )
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
            log_denial(
                action="mcp_query",
                reason="source_not_found",
                source_id=source_name,
                sql=sql,
                key_hint=key_hint,
            )
            return _tool_error(
                request_id, f"Source '{source_name}' not found. Available: {names}"
            )
        source = matching[0]
    else:
        source = sources[0]

    try:
        connector = CsvConnector(source.connection)
        result = connector.execute(sql, limit=1000)
    except SqlRefusedError as exc:
        log_denial(
            action="mcp_query",
            reason="sql_validation_failed",
            source_id=source.id,
            sql=sql,
            key_hint=key_hint,
        )
        return _tool_error(request_id, f"SQL validation failed: {exc}")
    except SqlFileAccessError as exc:
        log_denial(
            action="mcp_query",
            reason="sql_file_access",
            source_id=source.id,
            sql=sql,
            key_hint=key_hint,
        )
        return _tool_error(request_id, str(exc))
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
        key_hint=key_hint,
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
    "ping": _handle_ping,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


# The handshake and liveness must work before a client presents credentials.
_UNAUTHENTICATED = frozenset({"initialize", "ping"})


def _origin_allowed(request: Request) -> bool:
    """
    A browser always sends Origin; MCP clients outside a browser do not, so a
    foreign Origin is a cross-site page and is refused. A same-origin match
    does not stop DNS rebinding (there Origin and Host agree); what protects
    the server then is that every method but initialize and ping needs a key.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("", include_in_schema=True, summary="MCP-compatible JSON-RPC 2.0 endpoint")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    Model Context Protocol endpoint (JSON-RPC 2.0).

    Connects Claude Desktop, Cursor, and other MCP clients directly to
    the registered data source without extra configuration.

    Supported methods: `initialize`, `ping`, `tools/list`, `tools/call`
    """
    if not _origin_allowed(request):
        log_denial(action="mcp_auth", reason="invalid_origin")
        return JSONResponse(
            _err(None, -32600, "Forbidden: Origin not allowed"), status_code=403
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err(None, -32700, "Parse error: invalid JSON"))

    if isinstance(body, list):
        return JSONResponse(
            _err(None, -32600, "Batch requests are not supported"), status_code=400
        )
    if not isinstance(body, dict):
        return JSONResponse(_err(None, -32600, "Invalid Request"), status_code=400)

    if body.get("jsonrpc") != "2.0":
        return JSONResponse(_err(body.get("id"), -32600, "Invalid JSON-RPC version"))

    header_version = request.headers.get("mcp-protocol-version")
    if header_version is not None and header_version not in _SUPPORTED_VERSIONS:
        return JSONResponse(
            _err(
                body.get("id"),
                -32600,
                f"Unsupported MCP-Protocol-Version: {header_version}. "
                f"Supported: {', '.join(_SUPPORTED_VERSIONS)}",
            ),
            status_code=400,
        )
    structured = header_version is not None and header_version >= _STRUCTURED_FROM

    # A notification, or a client's response to a server request: nothing to
    # answer. Replying to one is a protocol error the client has to tolerate.
    if "id" not in body or "method" not in body:
        return Response(status_code=202)

    method = body.get("method")
    request_id = body.get("id")
    params = body.get("params", {})
    if not isinstance(params, dict) or (
        method == "tools/call"
        and not (
            isinstance(params.get("name"), str)
            and isinstance(params.get("arguments", {}), dict)
        )
    ):
        return JSONResponse(_err(request_id, -32602, "Invalid params"))

    handler = _HANDLERS.get(method)
    if handler is None:
        return JSONResponse(_err(request_id, -32601, f"Method not found: {method}"))

    token = ""
    if method not in _UNAUTHENTICATED:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        if token not in get_api_keys():
            log_denial(
                action="mcp_auth",
                reason="missing_api_key" if not token else "invalid_api_key",
                key_hint=token[:6] + "..." if token else "",
            )
            return JSONResponse(
                _err(request_id, -32001, "Unauthorized: invalid or missing API key")
            )

    if method == "tools/call":
        response = handler(request_id, params, token)
    else:
        response = handler(request_id, params)
    if structured:
        response = _with_structure(method, response)
    return JSONResponse(response)
