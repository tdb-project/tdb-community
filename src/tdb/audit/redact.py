"""
Keeping values out of what TDB logs, when `TDB_AUDIT_SQL=redacted`.

SQL can carry customer data and secrets in its literals, and an audit log that stores
it as written becomes sensitive data at rest. The default stays `full`, because the
exact statement is what makes the log evidence; this is the operator's choice.

Everything that any supported engine could read as data is hidden, so disagreement
between dialects hides more, never less. It fails the opposite way to the validator's
scan: there an unterminated string stays visible so that no keyword hides inside it;
here it is hidden, so that `WHERE x = 'secret` does not write the secret out.
"""

from __future__ import annotations

import hashlib
import re

from tdb.engine.validator import _DIALECTS, _mask_noncode

PLACEHOLDER = "?"

# A number standing alone: not `col1`, not the `$1` of a bound parameter.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_$\x80-\U0010ffff])[0-9][A-Za-z0-9_.]*")


def _hide_data_regions(text: str) -> str:
    """Each stretch any dialect reads as a literal or comment becomes one `?`."""
    data = [False] * len(text)
    for dialect in _DIALECTS:
        masked = _mask_noncode(text, dialect, hide_unterminated=True)
        for i, (before, after) in enumerate(zip(text, masked)):
            if before != after:
                data[i] = True
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if not data[i]:
            out.append(text[i])
            i += 1
            continue
        end = i
        while end < n and (data[end] or text[end].isspace()):
            end += 1
        # Trailing whitespace belongs to the code after it.
        last = end
        while not data[last - 1]:
            last -= 1
        out.append(PLACEHOLDER)
        i = last
    return "".join(out)


def redact_sql(sql: str) -> str:
    """The statement's shape: literals, numbers and comments replaced by `?`."""
    return _NUMBER.sub(PLACEHOLDER, _hide_data_regions(sql))


def fingerprint(redacted: str) -> str:
    """
    Groups runs of the same statement. Of the redacted text, never the original: a hash
    of a short secret can be reversed by trying values, which would undo the redaction.
    """
    return hashlib.sha256(redacted.encode()).hexdigest()


def redact_fields(fields: dict) -> dict:
    """An audit entry's fields with statement text and bound values removed."""
    out = dict(fields)
    if out.get("sql"):
        out["sql"] = redact_sql(out["sql"])
        out["sql_sha256"] = fingerprint(out["sql"])
        out["sql_redacted"] = True
    if "params" in out:
        params = out.pop("params")
        out["params_count"] = len(params) if isinstance(params, list) else 0
    return out


def redact_log_value(value: object) -> object:
    """
    A log record's argument. Free text can be a driver error quoting the statement, so
    it is redacted whole. A value with no spaces is an ID, a path or an address, where
    only a quoted part could hold data.
    """
    if isinstance(value, BaseException):
        value = str(value)
    if not isinstance(value, str):
        return value
    return (
        redact_sql(value)
        if any(c.isspace() for c in value)
        else (_hide_data_regions(value))
    )
