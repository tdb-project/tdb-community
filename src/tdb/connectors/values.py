"""
TDB – JSON-safe result values

Every connector's rows go out as JSON twice: as the REST response, which
Pydantic encodes, and as MCP tool text, which `json.dumps(default=str)` encodes.
Left to those two encoders, the same value came out differently or not at all:

- **Binary values** (BLOB, bytea, varbinary) made the REST response fail to
  encode after the query had run and been audited, and MCP printed a Python
  `b'...'` literal.
- **NaN and ±Infinity** became `null` on REST, indistinguishable from SQL NULL,
  and bare `NaN` / `Infinity` tokens on MCP, which is not JSON: a strict parser
  such as JavaScript's `JSON.parse` rejects the whole response.
- **Timestamps and intervals** read `2026-01-01T10:00:00` / `P1D` on REST and
  `2026-01-01 10:00:00` / `1 day, 0:00:00` on MCP.

`ConnectorResult` runs every row through `json_safe()` once, so both paths
receive plain JSON types and agree.
"""

from __future__ import annotations

import base64
import math
from typing import Any

from pydantic_core import PydanticSerializationError, to_jsonable_python

_NON_FINITE = {math.inf: "Infinity", -math.inf: "-Infinity"}


def json_safe(value: Any) -> Any:
    """
    Return *value* as plain JSON types.

    - bytes → base64 text.
    - NaN / ±Infinity → `"NaN"`, `"Infinity"`, `"-Infinity"` — PostgreSQL's own
      `to_json` spells them the same way, and a string keeps them distinct from
      NULL.
    - Lists and dicts (LIST, ARRAY, STRUCT, JSON) are walked.
    - Anything else non-native (Decimal, UUID, date, time, datetime, timedelta)
      takes the form REST already gave it, via Pydantic's encoder, so REST output
      for those types is unchanged: Decimal keeps its exact digits as a string,
      datetimes are ISO 8601 with `T`, intervals ISO 8601 durations. A type
      Pydantic cannot encode falls back to `str()`.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return _NON_FINITE.get(value, value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    try:
        return to_jsonable_python(value)
    except PydanticSerializationError:
        # A driver type Pydantic does not know. str() is what MCP always did with
        # it, and is better than failing a query that has already run.
        return str(value)


def json_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{col: json_safe(v) for col, v in row.items()} for row in rows]
