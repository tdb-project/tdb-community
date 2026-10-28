"""
Lean Mode: smaller MCP tool results for AI agents. Opt-in at every level.

Byte-identical in tdb-community and tdb-enterprise; change both or neither.

A setting comes from up to three layers — the server default (`TDB_LEAN_MODE`),
the API key's stored setting (enterprise), and the tool call's own arguments —
and the higher one wins field by field. `lean: false` in a layer clears
everything below it. A caller that sets nothing gets exactly today's result.

Levers:
  format          rows as `objects` (today) or `table` (each row an array in
                  `columns` order). Lossless.
  max_cell_chars  cut text cells longer than this, marking each cut with how
                  much was removed. Lossy, so never part of `lean: true`.
  detail          `schema_source` shape: `full` (today), `types`, `names`, or
                  `tables` (table names only). `types` is lossless.

`lean: true` means `format: table` and `detail: types` unless a field says
otherwise. Measured on Claude Code (2026-10-28, tdb-internal wave plan): a
100-row result 10,412 → 6,855 tokens; a 30-table schema 5,235 → 3,025.

Lean output is applied after the prompt-injection filter and before the
structured copy is built, so `structuredContent` and the text always carry the
same lean payload, and a trimmed cell can never carry what the filter removed.
"""

from __future__ import annotations

import json
import os
from typing import Any

FORMATS = ("objects", "table")
DETAILS = ("tables", "names", "types", "full")
MIN_CELL_CHARS = 50
FIELDS = ("lean", "format", "detail", "max_cell_chars")
ROW_TOOLS = frozenset(
    {"query_source", "preview_source", "filter_source", "aggregate_source", "run_view"}
)
SCHEMA_TOOL = "schema_source"

# Input-schema properties a tool advertises. Kept short: every tool's schema is
# sent to the model on every request. `format` is accepted from a call but not
# advertised — for an agent, `lean` already chooses between the two formats.
ROW_ARGUMENTS: dict[str, dict] = {
    "lean": {
        "type": "boolean",
        "description": "true: rows as arrays in 'columns' order (smaller).",
    },
    "max_cell_chars": {
        "type": "integer",
        "description": "Cut longer text (min 50); each cut is marked.",
    },
}
SCHEMA_ARGUMENTS: dict[str, dict] = {
    "detail": {
        "type": "string",
        "enum": list(DETAILS),
        "description": "Default full. types: {column: type}; tables: names only.",
    },
}


class LeanError(ValueError):
    """A Lean Mode setting that is not one of the allowed values."""


def parse(raw: Any, where: str) -> dict:
    """Validate one layer's setting; return only the fields it sets."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LeanError(f"{where}: Lean Mode setting must be an object.")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise LeanError(
            f"{where}: unknown field(s) {unknown}. Allowed: {list(FIELDS)}."
        )
    out: dict = {}
    if raw.get("lean") is not None:
        if not isinstance(raw["lean"], bool):
            raise LeanError(f"{where}: 'lean' must be true or false.")
        out["lean"] = raw["lean"]
    for field, allowed in (("format", FORMATS), ("detail", DETAILS)):
        if raw.get(field) is not None:
            if raw[field] not in allowed:
                raise LeanError(
                    f"{where}: '{field}' must be one of {list(allowed)}, "
                    f"not {raw[field]!r}."
                )
            out[field] = raw[field]
    if raw.get("max_cell_chars") is not None:
        value = raw["max_cell_chars"]
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or (value != 0 and value < MIN_CELL_CHARS)
        ):
            raise LeanError(
                f"{where}: 'max_cell_chars' must be 0 (off) or at least "
                f"{MIN_CELL_CHARS}."
            )
        out["max_cell_chars"] = value
    return out


def server_default() -> dict:
    """`TDB_LEAN_MODE`: unset or `off`, `on` (= lean: true), or a JSON object."""
    raw = os.environ.get("TDB_LEAN_MODE", "").strip()
    if raw.lower() in ("", "off", "false", "0"):
        return {}
    if raw.lower() in ("on", "true", "1"):
        return {"lean": True}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LeanError(
            "TDB_LEAN_MODE must be off, on, or a JSON object of Lean Mode fields."
        ) from exc
    return parse(value, "TDB_LEAN_MODE")


def call_setting(args: dict) -> dict:
    return parse({k: args[k] for k in FIELDS if k in args}, "Argument")


def resolve(*layers: dict) -> dict:
    """
    Merge layers, lowest first, into the settings to apply. Returns {} when
    nothing differs from today's output, so callers can skip all work.
    """
    merged: dict = {}
    for layer in layers:
        if layer.get("lean") is False:
            merged = {}
        merged.update({k: v for k, v in layer.items() if k != "lean"})
        if layer.get("lean") is True:
            merged["lean"] = True
    preset = merged.get("lean", False)
    effective: dict = {}
    fmt = merged.get("format", "table" if preset else "objects")
    if fmt != "objects":
        effective["format"] = fmt
    detail = merged.get("detail", "types" if preset else "full")
    if detail != "full":
        effective["detail"] = detail
    if merged.get("max_cell_chars"):
        effective["max_cell_chars"] = merged["max_cell_chars"]
    return effective


def for_tool(tool: str, settings: dict) -> dict:
    """The part of `settings` that changes this tool's result; {} if none."""
    if tool in ROW_TOOLS:
        keep = ("format", "max_cell_chars")
    elif tool == SCHEMA_TOOL:
        keep = ("detail",)
    else:
        keep = ()
    return {k: v for k, v in settings.items() if k in keep}


def _trim(rows: list, limit: int) -> int:
    trimmed = 0
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str) and len(value) > limit:
                row[key] = f"{value[:limit]}…[+{len(value) - limit} chars]"
                trimmed += 1
    return trimmed


def _shape_columns(columns: list, detail: str) -> tuple[Any, dict]:
    """A `[{name, type, description?}]` list in the requested detail."""
    notes = {c["name"]: c["description"] for c in columns if c.get("description")}
    if detail == "types":
        return {c["name"]: c["type"] for c in columns}, notes
    return [c["name"] for c in columns], notes


def apply(tool: str, payload: dict, settings: dict) -> dict:
    """Return `payload` in the lean shape `settings` asks for (in place)."""
    if not settings:
        return payload
    if tool in ROW_TOOLS and isinstance(payload.get("rows"), list):
        report: dict = {"format": settings.get("format", "objects")}
        limit = settings.get("max_cell_chars")
        if limit:
            report["max_cell_chars"] = limit
            report["trimmed_cells"] = _trim(payload["rows"], limit)
        if settings.get("format") == "table":
            columns = payload["columns"]
            payload["rows"] = [[row.get(c) for c in columns] for row in payload["rows"]]
        payload["lean"] = report
    elif tool == SCHEMA_TOOL and settings.get("detail", "full") != "full":
        detail = settings["detail"]
        if isinstance(payload.get("tables"), dict):
            if detail == "tables":
                payload["tables"] = sorted(payload["tables"])
            else:
                notes: dict = {}
                for table, columns in payload["tables"].items():
                    payload["tables"][table], table_notes = _shape_columns(
                        columns, detail
                    )
                    if table_notes:
                        notes[table] = table_notes
                if notes:
                    payload["column_descriptions"] = notes
        elif isinstance(payload.get("columns"), list):
            # One table: `tables` has nothing to list, so it reads as `names`.
            payload["columns"], notes = _shape_columns(
                payload["columns"], "types" if detail == "types" else "names"
            )
            if notes:
                payload["column_descriptions"] = notes
        payload["lean"] = {"detail": detail}
    return payload


def render(payload: dict) -> str:
    """Lean text: no spaces after separators, UTF-8 rather than \\u escapes."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)
