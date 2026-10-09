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
    "merge",
}
# `replace` is refused as a statement (MySQL's `REPLACE [INTO] t …`) but not as
# the string function every engine has. Measured on MySQL 8.0: no REPLACE
# statement form puts `(` straight after the keyword — `REPLACE (t) …` is a
# syntax error — and `WITH … REPLACE` is not valid. PostgreSQL, DuckDB, SQL
# Server and Snowflake have no REPLACE statement; `INSERT OR REPLACE` and
# `CREATE OR REPLACE` are refused by their first keyword. The lookahead runs on
# masked text, so a comment between the name and `(` reads as whitespace.
_BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(_BLOCKED) + r")\b|\b(replace)\b(?!\s*\()", re.IGNORECASE
)

# Regions that are data or prose, not executable SQL: their contents must not be
# scanned for blocked keywords.
#
# **The engines disagree about where those regions end**, and every disagreement
# is a place a scanner can be steered into calling live SQL "inside a string".
# PostgreSQL and DuckDB read `$$…$$` as a string, nest block comments and honour
# backslashes inside `E'…'`; MySQL honours backslashes everywhere, starts a
# comment at `#` and needs a space after `--`; T-SQL nests comments; Snowflake
# honours backslashes, dollar-quotes and `//` comments. A single scanner cannot
# be right for all of them, so the SQL is masked once per dialect below and
# **must pass in every one**. Each engine TDB executes on matches at least one
# profile, so a region is only ever treated as data if the engine running the
# query treats it as data too. The cost is over-rejection: SQL that one engine
# would misread is refused for all of them.
#
# T-SQL's `[identifier]` is deliberately absent everywhere: it is
# bracket-delimited rather than quote-delimited, so `SELECT a[1; DROP TABLE t]`
# would mask a statement separator that PostgreSQL, MySQL and DuckDB all read as
# code. A T-SQL column named `[delete]` is refused as a result.
_QUOTES = (("'", "'"), ('"', '"'), ("`", "`"))

# MySQL *executes* the body of `/*! ... */` and `/*!50000 ... */`. Masking those
# would hide live SQL from the scan, so they are treated as code in every profile.
_EXECUTABLE_COMMENT = "/*!"

_IDENT_CHAR = re.compile(r"[A-Za-z0-9_$\x80-\U0010ffff]")
_DOLLAR_TAG = re.compile(
    r"\$(?:[A-Za-z_\x80-\U0010ffff][A-Za-z0-9_\x80-\U0010ffff]*)?\$"
)


@dataclass(frozen=True)
class _Dialect:
    name: str
    # "never" | "always" | "e_prefix" (only inside E'…' literals)
    backslash: str = "never"
    dollar_quotes: bool = False
    nested_comments: bool = False
    hash_comments: bool = False
    slash_comments: bool = False
    # MySQL reads `--` as a comment only when whitespace follows it.
    dash_needs_space: bool = False


_DIALECTS = (
    _Dialect("ansi"),
    _Dialect(
        "postgres", backslash="e_prefix", dollar_quotes=True, nested_comments=True
    ),
    # standard_conforming_strings = off, still possible on old servers.
    _Dialect(
        "postgres_legacy", backslash="always", dollar_quotes=True, nested_comments=True
    ),
    _Dialect("mysql", backslash="always", hash_comments=True, dash_needs_space=True),
    # sql_mode = NO_BACKSLASH_ESCAPES
    _Dialect("mysql_no_backslash", hash_comments=True, dash_needs_space=True),
    _Dialect("tsql", nested_comments=True),
    _Dialect("snowflake", backslash="always", dollar_quotes=True, slash_comments=True),
    _Dialect(
        "snowflake_nested",
        backslash="always",
        dollar_quotes=True,
        slash_comments=True,
        nested_comments=True,
    ),
)
_ANSI = _DIALECTS[0]

# Openers a read-only statement may start with. `WITH` covers CTEs, including
# `WITH RECURSIVE`; see decisions/cte-support-in-validate-sql.md for why letting
# it through widens nothing.
_ALLOWED_PREFIXES = ("select", "with")


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str = ""


def _line_comment_at(sql: str, i: int, d: _Dialect) -> bool:
    if sql.startswith("--", i):
        if not d.dash_needs_space:
            return True
        return i + 2 >= len(sql) or sql[i + 2].isspace()
    if d.hash_comments and sql[i] == "#":
        return True
    return d.slash_comments and sql.startswith("//", i)


def _block_comment_end(sql: str, i: int, d: _Dialect) -> int:
    """Index just past the comment opening at *i*, or -1 if it never closes."""
    if not d.nested_comments:
        end = sql.find("*/", i + 2)
        return -1 if end == -1 else end + 2
    depth, j, n = 0, i, len(sql)
    while j < n:
        if sql.startswith("/*", j):
            depth += 1
            j += 2
        elif sql.startswith("*/", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
        else:
            j += 1
    return -1


def _quote_end(sql: str, i: int, d: _Dialect) -> int:
    """Index of the closing quote for the literal opening at *i*, or -1."""
    q = sql[i]
    if d.backslash == "always":
        escapes = q in ("'", '"')
    elif d.backslash == "e_prefix":
        escapes = (
            q == "'"
            and i > 0
            and sql[i - 1] in "eE"
            and (i < 2 or not _IDENT_CHAR.match(sql[i - 2]))
        )
    else:
        escapes = False
    j, n = i + 1, len(sql)
    while j < n:
        c = sql[j]
        if escapes and c == "\\":
            j += 2
            continue
        if c == q:
            # A doubled closer is an escaped literal quote, not the end.
            if j + 1 < n and sql[j + 1] == q:
                j += 2
                continue
            return j
        j += 1
    return -1


def _mask_noncode(sql: str, dialect: _Dialect = _ANSI) -> str:
    """
    Blank out the contents of string literals, quoted identifiers and comments
    as *dialect* reads them, leaving executable SQL — and the offsets of
    everything — untouched.

    Without this, the keyword scan reads the whole statement as code, so
    ``WHERE status = 'update pending'`` is refused as a write attempt and a
    column named ``"delete"`` cannot be selected.

    **An unterminated region is not masked.** If a quote, dollar-quote or block
    comment never closes, the rest of the statement stays visible to the scan
    rather than being swallowed by a malformed literal. A false rejection is a
    puzzled user; a keyword smuggled past this scanner is a write reaching a
    connector.

    One profile on its own is not safe — see `_DIALECTS`. The 0.7.x scanner was
    the `ansi` profile alone, and its docstring claimed that ignoring
    backslashes and dollar-quotes could only over-reject. The opposite held: a
    quote character *inside* a region another engine treats as data opened a
    string here and hid the SQL that followed it.
    """
    out = list(sql)
    i, n = 0, len(sql)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            out[k] = " "

    while i < n:
        if sql.startswith(_EXECUTABLE_COMMENT, i):
            i += len(_EXECUTABLE_COMMENT)
            continue
        if _line_comment_at(sql, i, dialect):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            blank(i, end)
            i = end
            continue
        if sql.startswith("/*", i):
            end = _block_comment_end(sql, i, dialect)
            if end == -1:
                return "".join(out)
            blank(i, end)
            i = end
            continue
        if dialect.dollar_quotes and sql[i] == "$":
            m = _DOLLAR_TAG.match(sql, i)
            if m and (i == 0 or not _IDENT_CHAR.match(sql[i - 1])):
                tag = m.group(0)
                close = sql.find(tag, m.end())
                if close == -1:
                    return "".join(out)
                blank(i, close + len(tag))
                i = close + len(tag)
                continue
        if any(sql[i] == o for o, _ in _QUOTES):
            j = _quote_end(sql, i, dialect)
            if j == -1:
                return "".join(out)
            blank(i, j + 1)
            i = j + 1
            continue
        i += 1

    return "".join(out)


def strip_trailing_semicolon(sql: str) -> str:
    """
    Remove one statement terminator — and any whitespace or comment after it —
    from the end of *sql*. Anything else is returned unchanged.

    Connectors append their row cap to the end of the statement, so
    ``SELECT 1;`` became ``SELECT 1; LIMIT 6`` — a syntax error. The ``;`` is
    found on the masked text, so one inside a literal or comment is never taken
    for the terminator.
    """
    code = _mask_noncode(sql).rstrip()
    if code.endswith(";"):
        return sql[: len(code) - 1].rstrip()
    return sql


def validate_sql(sql: str) -> ValidationResult:
    stripped = sql.strip()
    if not stripped:
        return ValidationResult(is_valid=False, reason="Empty SQL")

    for dialect in _DIALECTS:
        result = _validate_masked(_mask_noncode(stripped, dialect))
        if not result.is_valid:
            return result
    return ValidationResult(is_valid=True)


def _validate_masked(masked: str) -> ValidationResult:
    match = _BLOCKED_PATTERN.search(masked)
    if match:
        return ValidationResult(
            is_valid=False, reason=f"Blocked keyword: {match.group(0)}"
        )

    # One statement only. Only the first statement's opening token is checked
    # below, so a second one could be anything the keyword list does not name —
    # `COPY … TO`, `ATTACH`, `SET`, `EXEC`. A trailing `;` is fine, and the
    # masked text has already blanked any `;` inside a literal or comment.
    semi = masked.find(";")
    if semi != -1 and masked[semi + 1 :].strip():
        return ValidationResult(
            is_valid=False, reason="Only one statement per query is allowed"
        )

    # The prefix is read off the *masked* text, so a statement opening with a
    # comment is judged by the SQL that follows it rather than by the comment.
    # `WITH` is accepted because the scan above is what refuses a data-modifying
    # CTE — it runs first and ignores the opening token, so every writing `WITH`
    # is already gone by this line. Rejecting the prefix only ever refused the
    # read-only ones.
    if not masked.lstrip().lower().startswith(_ALLOWED_PREFIXES):
        return ValidationResult(
            is_valid=False, reason="Only SELECT and WITH statements are allowed"
        )

    return ValidationResult(is_valid=True)
