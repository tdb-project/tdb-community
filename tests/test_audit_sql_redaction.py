"""
TDB_AUDIT_SQL=redacted: no value from a statement reaches a log. The cases are written
the way each engine reads them: a literal one dialect ends early is where it leaks.
"""

from __future__ import annotations

import importlib
import logging

import pytest

SECRET = "hunter2"


@pytest.fixture(autouse=True)
def _log_scrubbing_installed() -> None:
    """Importing the audit logger installs it, as `tdb.main` does in production."""
    importlib.import_module("tdb.audit.logger")


def _redact(sql: str) -> str:
    from tdb.audit.redact import redact_sql

    return redact_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param(f"SELECT * FROM t WHERE p = '{SECRET}'", id="string"),
        pytest.param(f"SELECT * FROM t WHERE p = 'it''s {SECRET}'", id="doubled-quote"),
        pytest.param(f"SELECT * FROM t WHERE p = 'a\\' {SECRET} '", id="backslash"),
        pytest.param(f"SELECT * FROM t WHERE p = E'a\\' {SECRET}'", id="e-string"),
        pytest.param(f"SELECT * FROM t WHERE p = $${SECRET}$$", id="dollar"),
        pytest.param(f"SELECT * FROM t WHERE p = $tag${SECRET}$tag$", id="dollar-tag"),
        pytest.param(f'SELECT * FROM t WHERE p = "{SECRET}"', id="mysql-double-quote"),
        pytest.param(f"SELECT * FROM t WHERE p = `{SECRET}`", id="backtick"),
        pytest.param(f"SELECT 1 -- {SECRET}", id="line-comment"),
        pytest.param(f"SELECT 1 # {SECRET}", id="hash-comment"),
        pytest.param(f"SELECT 1 // {SECRET}", id="slash-comment"),
        pytest.param(f"SELECT /* {SECRET} */ 1", id="block-comment"),
        pytest.param(f"SELECT /* a /* b */ {SECRET} */ 1", id="nested-comment"),
        pytest.param(f"SELECT /*! {SECRET} */ 1", id="executable-comment"),
        pytest.param(f"SELECT /*!50000 {SECRET} */ 1", id="versioned-comment"),
        pytest.param(f"SELECT * FROM t WHERE p = '{SECRET}", id="unterminated-string"),
        pytest.param(f"SELECT * FROM t WHERE p = $${SECRET}", id="unterminated-dollar"),
        pytest.param(f"SELECT /* {SECRET}", id="unterminated-comment"),
        pytest.param(f"SELECT 'a\n{SECRET}\nb'", id="multi-line"),
        pytest.param(f"SELECT * FROM t WHERE p = X'{SECRET}'", id="hex-prefix"),
        pytest.param(f"DROP TABLE x -- token={SECRET}", id="refused-statement"),
    ],
)
def test_a_value_does_not_survive(sql):
    assert SECRET not in _redact(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t WHERE ssn = 123456789",
        "SELECT * FROM t WHERE amount > 12.50",
        "SELECT * FROM t WHERE card IN (4111111111111111, 5500005555555559)",
        "SELECT * FROM t WHERE n = 1e5 OR h = 0xDEADBEEF",
    ],
)
def test_numbers_do_not_survive(sql):
    redacted = _redact(sql)
    for value in ("123456789", "12.50", "4111111111111111", "1e5", "DEADBEEF"):
        assert value not in redacted


def test_the_shape_of_the_statement_survives():
    assert _redact(
        "SELECT name, col1 FROM orders o JOIN t2 ON o.id = t2.id "
        "WHERE region = 'EU' AND n > 5 AND x = $1 LIMIT 10"
    ) == (
        "SELECT name, col1 FROM orders o JOIN t2 ON o.id = t2.id "
        "WHERE region = ? AND n > ? AND x = $1 LIMIT ?"
    )


def test_the_same_shape_has_the_same_fingerprint():
    from tdb.audit.redact import fingerprint

    one = fingerprint(_redact("SELECT * FROM t WHERE a = 'x' AND b = 1"))
    two = fingerprint(_redact("SELECT * FROM t WHERE a = 'yyy' AND b = 22"))
    three = fingerprint(_redact("SELECT * FROM u WHERE a = 'x' AND b = 1"))
    assert one == two != three


def test_the_fingerprint_cannot_be_used_to_guess_a_value():
    """A hash of the original SQL could be checked against candidate secrets."""
    import hashlib

    from tdb.audit.redact import redact_fields

    sql = f"SELECT * FROM t WHERE p = '{SECRET}'"
    entry = redact_fields({"sql": sql})
    assert entry["sql_sha256"] != hashlib.sha256(sql.encode()).hexdigest()
    assert entry["sql_sha256"] == hashlib.sha256(entry["sql"].encode()).hexdigest()


def test_an_entry_says_it_was_redacted():
    from tdb.audit.redact import redact_fields

    entry = redact_fields({"sql": "SELECT 1"})
    assert entry["sql_redacted"] is True


def test_an_entry_with_no_sql_is_not_marked():
    from tdb.audit.redact import redact_fields

    assert redact_fields({"sql": "", "event": "denied"}) == {
        "sql": "",
        "event": "denied",
    }


def test_bound_parameters_become_a_count():
    from tdb.audit.redact import redact_fields

    entry = redact_fields({"params": [SECRET, "b", None]})
    assert "params" not in entry
    assert entry["params_count"] == 3


def test_the_other_fields_are_untouched():
    from tdb.audit.redact import redact_fields

    entry = redact_fields(
        {"source_id": "abc-123", "rows_returned": 5, "sql": "SELECT 1"}
    )
    assert (entry["source_id"], entry["rows_returned"]) == ("abc-123", 5)


# --- logs ---------------------------------------------------------------------


def _log_line(caplog, name: str, *args, **kw) -> str:
    logger = logging.getLogger(name)
    with caplog.at_level(logging.INFO, logger=name):
        logger.error(*args, **kw)
    return caplog.records[-1].getMessage() + (caplog.records[-1].exc_text or "")


def test_an_engine_error_in_a_log_line_loses_its_values(monkeypatch, caplog):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    line = _log_line(
        caplog,
        "tdb.test",
        "query_error source_id=%s error=%s",
        "abc-123",
        f"Conversion Error: could not convert '{SECRET}' LINE 1: WHERE n = 987654",
    )
    assert SECRET not in line
    assert "987654" not in line
    assert "source_id=abc-123" in line


def test_an_exception_passed_to_a_log_line_loses_its_values(monkeypatch, caplog):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    line = _log_line(caplog, "tdb.test", "failed %s", ValueError(f"bad '{SECRET}'"))
    assert SECRET not in line


def test_a_traceback_is_withheld(monkeypatch, caplog):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    logger = logging.getLogger("tdb.test")
    with caplog.at_level(logging.INFO, logger="tdb.test"):
        try:
            raise ValueError(f"bad '{SECRET}'")
        except ValueError:
            logger.exception("query failed")
    record = caplog.records[-1]
    assert SECRET not in caplog.text
    assert record.exc_info is None
    assert "ValueError" in record.getMessage()


def test_a_full_log_keeps_its_tracebacks_and_values(monkeypatch, caplog):
    monkeypatch.delenv("TDB_AUDIT_SQL", raising=False)
    line = _log_line(caplog, "tdb.test", "failed %s", f"bad '{SECRET}'")
    assert SECRET in line


def test_other_loggers_are_left_alone(monkeypatch, caplog):
    monkeypatch.setenv("TDB_AUDIT_SQL", "redacted")
    line = _log_line(caplog, "someone.else", "failed %s", f"bad '{SECRET}'")
    assert SECRET in line


# --- the setting --------------------------------------------------------------


@pytest.mark.parametrize("value", ["redact", "off", "true"])
def test_a_mistyped_mode_stops_startup(monkeypatch, value):
    from tdb.config import check_audit_sql_mode

    monkeypatch.setenv("TDB_AUDIT_SQL", value)
    with pytest.raises(ValueError, match="TDB_AUDIT_SQL"):
        check_audit_sql_mode()


@pytest.mark.parametrize("value", ["full", "redacted", "REDACTED", ""])
def test_a_valid_mode_is_accepted(monkeypatch, value):
    from tdb.config import check_audit_sql_mode

    monkeypatch.setenv("TDB_AUDIT_SQL", value)
    check_audit_sql_mode()


def test_the_default_is_full(monkeypatch):
    from tdb.config import get_audit_sql_mode

    monkeypatch.delenv("TDB_AUDIT_SQL", raising=False)
    assert get_audit_sql_mode() == "full"
