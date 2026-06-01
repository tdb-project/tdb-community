from __future__ import annotations

import re
from dataclasses import dataclass

_BLOCKED = {
    "insert",
    "update",
    "delete",
    "drop",
    "create",
    "alter",
    "truncate",
    "replace",
    "merge",
}
_BLOCKED_PATTERN = re.compile(r"\b(" + "|".join(_BLOCKED) + r")\b", re.IGNORECASE)


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str = ""


def validate_sql(sql: str) -> ValidationResult:
    stripped = sql.strip()
    if not stripped:
        return ValidationResult(is_valid=False, reason="Empty SQL")

    match = _BLOCKED_PATTERN.search(stripped)
    if match:
        return ValidationResult(
            is_valid=False, reason=f"Blocked keyword: {match.group(0)}"
        )

    if not stripped.lower().lstrip().startswith("select"):
        return ValidationResult(
            is_valid=False, reason="Only SELECT statements are allowed"
        )

    return ValidationResult(is_valid=True)
