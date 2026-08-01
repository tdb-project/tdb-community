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

# Regions that are data or prose, not executable SQL: their contents must not be
# scanned for blocked keywords. Each entry is (opening token, closing token).
#
# Only delimiters every supported engine agrees are non-code appear here. T-SQL's
# `[identifier]` is deliberately absent: it is bracket-delimited rather than
# quote-delimited, so `SELECT a[1; DROP TABLE t]` would mask a statement
# separator that PostgreSQL, MySQL and DuckDB all read as code. A T-SQL column
# named `[delete]` is refused as a result — an acceptable false rejection in
# exchange for closing a shape where the scanner and the engine disagree.
_QUOTES = (("'", "'"), ('"', '"'), ("`", "`"))
_COMMENTS = (("--", "\n"), ("/*", "*/"))

# MySQL *executes* the body of `/*! ... */` and `/*!50000 ... */`. Masking those
# would hide live SQL from the scan, which is the oldest trick in the injection
# book, so they are treated as code.
_EXECUTABLE_COMMENT = "/*!"


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str = ""


def _mask_noncode(sql: str) -> str:
    """
    Blank out the contents of string literals, quoted identifiers and comments,
    leaving executable SQL — and the offsets of everything — untouched.

    Without this, the keyword scan reads the whole statement as code, so
    ``WHERE status = 'update pending'`` is refused as a write attempt and a
    column named ``"delete"`` cannot be selected.

    **Two deliberate biases, both toward over-rejection.** A false rejection is
    a puzzled user; a keyword smuggled past this scanner is a write reaching a
    connector.

    1. *An unterminated region is not masked.* If a quote or block comment never
       closes, the rest of the statement stays visible to the scan rather than
       being swallowed by a malformed literal.
    2. *Backslash is not an escape character.* MySQL reads ``'a\\''`` as a
       string containing a quote; standard SQL (and PostgreSQL with
       ``standard_conforming_strings``) reads it as a string ending at the
       second quote. Honouring the backslash would mask everything after it on
       PostgreSQL — where it is real code. Ignoring it merely over-rejects some
       valid MySQL. Only doubled quotes (``''``) escape here.

    PostgreSQL dollar-quoting (``$$…$$``) is not recognised, so its contents are
    still scanned — over-rejection again, and it has no place in a SELECT the
    caller could not write another way.
    """
    out = list(sql)
    i, n = 0, len(sql)

    while i < n:
        if sql.startswith(_EXECUTABLE_COMMENT, i):
            i += len(_EXECUTABLE_COMMENT)
            continue

        for opener, closer in _COMMENTS:
            if sql.startswith(opener, i):
                end = sql.find(closer, i + len(opener))
                if end == -1:
                    # Unterminated block comment: leave the tail scannable.
                    # An unterminated line comment simply runs to end of input.
                    if closer == "\n":
                        end = n
                    else:
                        return "".join(out)
                else:
                    end += len(closer)
                for j in range(i, end):
                    out[j] = " "
                i = end
                break
        else:
            opener = sql[i]
            closer = next((c for o, c in _QUOTES if o == opener), None)
            if closer is None:
                i += 1
                continue
            j = i + 1
            while j < n:
                if sql[j] == closer:
                    # A doubled closer is an escaped literal quote, not the end.
                    if j + 1 < n and sql[j + 1] == closer:
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                # Unterminated: stop masking so the remainder is still scanned.
                return "".join(out)
            for k in range(i, j + 1):
                out[k] = " "
            i = j + 1

    return "".join(out)


def validate_sql(sql: str) -> ValidationResult:
    stripped = sql.strip()
    if not stripped:
        return ValidationResult(is_valid=False, reason="Empty SQL")

    match = _BLOCKED_PATTERN.search(_mask_noncode(stripped))
    if match:
        return ValidationResult(
            is_valid=False, reason=f"Blocked keyword: {match.group(0)}"
        )

    if not stripped.lower().lstrip().startswith("select"):
        return ValidationResult(
            is_valid=False, reason="Only SELECT statements are allowed"
        )

    return ValidationResult(is_valid=True)
