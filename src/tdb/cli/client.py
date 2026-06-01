"""Thin HTTP client for talking to a running TDB server."""

from __future__ import annotations

import os

import httpx


def get_base_url() -> str:
    return os.environ.get("TDB_URL", "http://localhost:8000")


def get_api_key() -> str:
    key = os.environ.get("TDB_API_KEYS", "").split(",")[0].strip()
    if not key:
        raise RuntimeError(
            "TDB_API_KEYS environment variable is not set.\n"
            "Set it to your API key before using the CLI.\n"
            "Example: export TDB_API_KEYS=your-secret-key"
        )
    return key


def make_client() -> httpx.Client:
    return httpx.Client(
        base_url=get_base_url(),
        headers={"Authorization": f"Bearer {get_api_key()}"},
        timeout=30.0,
    )
