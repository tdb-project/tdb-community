"""
An MCP request must not hold the event loop.

The endpoint is async (it reads the body itself), and it called authentication and the
tool directly: registry reads and the query, all blocking. One slow query stalled every
other request, health checks included, until it finished.
"""

from __future__ import annotations

import asyncio
import threading

import httpx

from tdb.main import app
from tdb.routers import mcp

_AUTH = {"Authorization": "Bearer test-key-abc"}


async def _served_while_mcp_is_busy(monkeypatch, body, headers):
    real = mcp._authenticate
    started, release = threading.Event(), threading.Event()
    finished_first: list[bool] = []

    def slow(*args, **kwargs):
        started.set()
        release.wait(timeout=3)
        finished_first.append(not release.is_set())
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp, "_authenticate", slow)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tdb") as http:
        call = asyncio.create_task(
            http.post("/v1/mcp", json=body, headers={**_AUTH, **headers})
        )
        while not started.is_set():
            assert not call.done(), call.result().text
            await asyncio.sleep(0.01)
        health = await http.get("/health")
        release.set()
        response = await call
    assert health.status_code == 200
    # False when the health check had to wait for the MCP call to give up waiting.
    assert finished_first == [False]
    return response


async def test_a_slow_tool_call_does_not_hold_other_requests(monkeypatch) -> None:
    r = await _served_while_mcp_is_busy(
        monkeypatch, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {}
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["tools"]


async def test_nor_does_one_at_2026_07_28(monkeypatch) -> None:
    version = "2026-07-28"
    r = await _served_while_mcp_is_busy(
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": version,
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        {"MCP-Protocol-Version": version, "Mcp-Method": "tools/list"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["tools"]
