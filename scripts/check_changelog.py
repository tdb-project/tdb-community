#!/usr/bin/env python3
"""
Fail if the version in ``src/tdb/__init__.py`` has no CHANGELOG section.

``__version__`` is the single source of truth for the version — hatchling reads
it, the startup banner prints it, and ``GET /v1/version`` reports it. That makes
it the version a user names when reporting a problem, and it should always be
possible to look that version up.

Releases are the easy thing to remember: the tag and the GitHub release feel like
shipping. The changelog entry does not, because nothing fails without one. This
is the thing that fails without one.

The check is deliberately narrow. It does not police wording, dates or section
order — only that the version a user can name has somewhere to look. During
ordinary work ``__version__`` still holds the last released version, whose section
already exists, so this stays silent until a release bump lands without an entry.

Usage:  python scripts/check_changelog.py [--changelog PATH] [--init PATH]
Exit:   0 = section present and non-empty, 1 = missing or empty
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def read_version(init_path: Path) -> str:
    match = _VERSION_RE.search(init_path.read_text())
    if not match:
        raise SystemExit(f"error: no __version__ found in {init_path}")
    return match.group(1)


def section_body(changelog: str, version: str) -> str | None:
    """Text under ``## [version]``, or None if that heading is absent."""
    heading = re.compile(rf"^##\s*\[{re.escape(version)}\].*$", re.MULTILINE)
    start = heading.search(changelog)
    if start is None:
        return None
    rest = changelog[start.end() :]
    nxt = re.search(r"^##\s*\[", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changelog", type=Path, default=_ROOT / "CHANGELOG.md")
    parser.add_argument(
        "--init", type=Path, default=_ROOT / "src" / "tdb" / "__init__.py"
    )
    args = parser.parse_args()

    version = read_version(args.init)
    body = section_body(args.changelog.read_text(), version)

    if body is None:
        print(
            f"CHANGELOG.md has no section for {version}.\n\n"
            f"  {args.init.name} says __version__ = {version!r}, and that is the "
            f"version a\n  user reads back from GET /v1/version. Add:\n\n"
            f"      ## [{version}] — <date>\n",
            file=sys.stderr,
        )
        return 1

    if not body.strip():
        print(
            f"CHANGELOG.md has a section for {version} but it is empty.\n"
            f"  An empty section is the same gap with a heading over it.",
            file=sys.stderr,
        )
        return 1

    print(f"CHANGELOG.md documents {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
