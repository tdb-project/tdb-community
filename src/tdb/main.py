"""
TDB – The Data-Bridge  |  Main Application Entry Point

Start the server:
    uvicorn tdb.main:app --reload --port 8000

Environment variables:
    TDB_API_KEYS    Comma-separated list of valid API keys
                    Default (dev only): dev-insecure-key-change-me
    TDB_LOG_FILE    Path for the JSON audit log  (default: tdb_audit.jsonl)
    TDB_LOG_LEVEL   Logging level  (default: INFO)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from tdb import __version__
from tdb.audit.logger import get_logger
from tdb.config import get_log_level
from tdb.registry.migrations import run_migrations
from tdb.routers.mcp import router as mcp_router
from tdb.routers.query import router as query_router
from tdb.routers.sources import router as sources_router

# ---------------------------------------------------------------------------
# Lifespan  (startup / shutdown)
# ---------------------------------------------------------------------------

_log = get_logger("tdb.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run setup tasks on startup; yield to serve requests; clean up on shutdown."""
    logging.root.setLevel(getattr(logging, get_log_level()))
    os.makedirs("data", exist_ok=True)
    run_migrations()
    dev_mode = "dev-insecure-key-change-me" in os.environ.get(
        "TDB_API_KEYS", "dev-insecure-key-change-me"
    )
    _log.info("tdb_startup version=%s dev_mode=%s", "0.4.2", dev_mode)
    if dev_mode:
        print(
            "\n⚠️  WARNING: TDB is running with the default insecure dev API key.\n"
            "   Set  TDB_API_KEYS=your-secret-key  before exposing to a network.\n"
        )
    yield
    # shutdown: nothing to release in community edition


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="The Data-Bridge (TDB)",
    description=(
        "A secure, auditable API layer over your existing data sources. "
        "Register a data source once, query it anywhere."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(sources_router, prefix="/v1")
app.include_router(query_router, prefix="/v1")
app.include_router(mcp_router, prefix="/v1")

_log = get_logger("tdb.main")

# ---------------------------------------------------------------------------
# Health / root endpoints  (no auth needed)
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root():
    return {
        "product": "The Data-Bridge",
        "version": __version__,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Meta"])
def health():
    """Liveness probe — used by Docker / k8s health checks."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Global exception handler  (catch-all for unhandled errors)
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    _log.error(
        "unhandled_exception — %s %s — %s",
        request.method,
        str(request.url),
        str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error", "detail": str(exc)},
    )
