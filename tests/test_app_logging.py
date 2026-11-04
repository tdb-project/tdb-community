"""
The application log reaches the console under uvicorn, at TDB_LOG_LEVEL.

Under uvicorn the root logger has no handler, so INFO lines were dropped and only
warnings got through Python's last-resort handler. It needs a process of its own:
pytest installs handlers on the root logger, which would hide the bug.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_RUN = """
import asyncio
from tdb.main import app, lifespan

async def main():
    async with lifespan(app):
        pass

asyncio.run(main())
"""


def _startup(tmp_path: Path, **env: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _RUN],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env={
            **os.environ,
            "TDB_API_KEYS": "a-real-key-for-this-test",
            "TDB_LOG_FILE": str(tmp_path / "audit.jsonl"),
            "TDB_REGISTRY_DB": str(tmp_path / "registry.db"),
            **env,
        },
    )
    return result.stderr


def test_the_startup_line_is_printed_by_default(tmp_path: Path) -> None:
    assert "INFO:     tdb_startup version=" in _startup(tmp_path)


@pytest.mark.parametrize("level", ["WARNING", "ERROR"])
def test_a_higher_level_silences_it(tmp_path: Path, level: str) -> None:
    assert "tdb_startup" not in _startup(tmp_path, TDB_LOG_LEVEL=level)
