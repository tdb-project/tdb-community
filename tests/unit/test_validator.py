"""
Tests for the SQL validator.
validate_sql() returns ValidationResult(is_valid, reason) — it does not raise.
"""

import pytest

from tdb.engine.validator import validate_sql


class TestValidSQL:
    def test_simple_select(self):
        result = validate_sql("SELECT * FROM source")
        assert result.is_valid

    def test_select_with_where(self):
        result = validate_sql("SELECT name, age FROM source WHERE age > 30")
        assert result.is_valid

    def test_select_with_limit(self):
        result = validate_sql("SELECT * FROM source LIMIT 100")
        assert result.is_valid

    def test_strips_whitespace(self):
        result = validate_sql("  SELECT * FROM source  ")
        assert result.is_valid


class TestBlockedSQL:
    def test_blocks_drop(self):
        result = validate_sql("DROP TABLE source")
        assert not result.is_valid

    def test_blocks_delete(self):
        result = validate_sql("DELETE FROM source")
        assert not result.is_valid

    def test_blocks_insert(self):
        result = validate_sql("INSERT INTO source VALUES (1)")
        assert not result.is_valid

    def test_blocks_update(self):
        result = validate_sql("UPDATE source SET col = 1")
        assert not result.is_valid

    def test_blocks_create(self):
        result = validate_sql("CREATE TABLE foo (id INT)")
        assert not result.is_valid

    def test_blocks_non_select(self):
        result = validate_sql("EXEC sp_something")
        assert not result.is_valid

    def test_blocks_semicolon(self):
        result = validate_sql("SELECT * FROM source; DROP TABLE source")
        assert not result.is_valid
        assert "DROP" in result.reason.upper()

    def test_blocks_empty(self):
        result = validate_sql("")
        assert not result.is_valid
        assert "empty" in result.reason.lower()


class TestKeywordsInsideNonCode:
    """
    A blocked keyword inside a string literal, comment or quoted identifier is
    data or prose, not a write. Refusing these was a real defect: a perfectly
    ordinary filter on an order-status column was rejected as a write attempt,
    and the refusal was audited as a denial.
    """

    def test_allows_keyword_inside_a_string_literal(self):
        result = validate_sql("SELECT * FROM t WHERE status = 'update pending'")
        assert result.is_valid, result.reason

    def test_allows_several_keywords_inside_a_literal(self):
        result = validate_sql("SELECT * FROM t WHERE note = 'do not delete or drop'")
        assert result.is_valid, result.reason

    def test_allows_keyword_in_a_line_comment(self):
        result = validate_sql("SELECT id FROM t -- delete this column later")
        assert result.is_valid, result.reason

    def test_allows_keyword_in_a_block_comment(self):
        result = validate_sql("SELECT /* drop the old join */ id FROM t")
        assert result.is_valid, result.reason

    def test_allows_quoted_identifier_named_after_a_keyword(self):
        result = validate_sql('SELECT "delete" FROM t')
        assert result.is_valid, result.reason

    def test_allows_backtick_identifier_named_after_a_keyword(self):
        result = validate_sql("SELECT `update` FROM t")
        assert result.is_valid, result.reason

    def test_allows_a_doubled_quote_inside_a_literal(self):
        """'' is an escaped quote — the literal does not end there."""
        result = validate_sql("SELECT * FROM t WHERE a = 'it''s update time'")
        assert result.is_valid, result.reason


class TestScannerCannotBeTricked:
    """
    The masking that fixes those false positives must not hide real SQL. Each
    case here is a way to make the scanner believe code is data; every one must
    still be refused.
    """

    def test_keyword_after_a_literal_is_still_blocked(self):
        result = validate_sql("SELECT 'a'; DROP TABLE t")
        assert not result.is_valid

    def test_keyword_after_a_masked_keyword_is_still_blocked(self):
        result = validate_sql("SELECT 'update' ; DELETE FROM t")
        assert not result.is_valid

    def test_unterminated_quote_does_not_swallow_the_rest(self):
        """An unclosed literal must leave the tail visible to the scan."""
        result = validate_sql("SELECT 'abc ; DROP TABLE t")
        assert not result.is_valid

    def test_backslash_does_not_escape_a_quote(self):
        r"""
        MySQL reads 'a\'' as a string containing a quote; standard SQL ends the
        string at the second quote. Honouring the backslash would mask real
        code on PostgreSQL, so it is ignored — over-rejecting some valid MySQL.
        """
        result = validate_sql("SELECT 'a\\' ; DROP TABLE t --'")
        assert not result.is_valid

    def test_keyword_after_a_block_comment_is_still_blocked(self):
        result = validate_sql("SELECT * FROM t /* note */; TRUNCATE t")
        assert not result.is_valid

    def test_keyword_after_a_line_comment_is_still_blocked(self):
        result = validate_sql("SELECT * FROM t -- note\n; DROP TABLE t")
        assert not result.is_valid

    def test_brackets_cannot_swallow_a_statement_separator(self):
        """
        T-SQL `[identifier]` is not treated as a quoted region: it is
        bracket-delimited, so masking it would hide code that PostgreSQL,
        MySQL and DuckDB all execute.
        """
        result = validate_sql("SELECT a[1; DROP TABLE t]")
        assert not result.is_valid

    def test_mysql_executable_comment_is_not_treated_as_a_comment(self):
        """MySQL executes the body of /*! ... */ — masking it would hide a write."""
        result = validate_sql("SELECT 1 /*! ; DROP TABLE t */")
        assert not result.is_valid

    def test_versioned_mysql_executable_comment_is_not_a_comment(self):
        result = validate_sql("SELECT 1 /*!40001 ; DROP TABLE t */")
        assert not result.is_valid


class TestCtesAndLeadingComments:
    """
    The prefix guard is read off the masked SQL and accepts `WITH` (§7 item 8).

    `WITH … SELECT` is standard, read-only, and the natural shape for the
    analytical queries this product is sold for — and it was refused for the
    product's whole life by a guard that was doing no write-protection work.
    See tdb-internal decisions/cte-support-in-validate-sql.md.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "WITH a AS (SELECT 1) SELECT * FROM a",
            "with a as (select 1) select * from a",
            "  WITH a AS (SELECT 1) SELECT * FROM a  ",
            "WITH RECURSIVE t(n) AS ("
            "SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5"
            ") SELECT n FROM t",
            "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b",
            "/* leading block comment */ SELECT 1",
            "-- leading line comment\nSELECT 1",
            "/* a */ -- b\n WITH x AS (SELECT 1) SELECT * FROM x",
        ],
    )
    def test_read_only_shapes_are_accepted(self, sql):
        assert validate_sql(sql).is_valid

    @pytest.mark.parametrize(
        "sql,keyword",
        [
            (
                "WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x",
                "INSERT",
            ),
            ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "DELETE"),
            ("WITH x AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM x", "UPDATE"),
            ("WITH x AS (SELECT 1) DELETE FROM t", "DELETE"),
            ("WITH x AS (SELECT 1) MERGE INTO t USING x ON 1 = 1", "MERGE"),
        ],
    )
    def test_a_writing_cte_is_still_refused_by_the_keyword_scan(self, sql, keyword):
        """
        This is why widening the prefix guard widens nothing: the keyword scan
        runs first and ignores the opening token, so every data-modifying CTE
        was already refused before the prefix was consulted. The reason string
        proves which check did the work.
        """
        result = validate_sql(sql)
        assert not result.is_valid
        assert keyword in result.reason.upper()

    @pytest.mark.parametrize(
        "sql",
        [
            "-- select 1",
            "/* select 1 */",
            "/* c */ DROP TABLE t",
            "EXPLAIN SELECT 1",
            "EXEC sp_something",
        ],
    )
    def test_masking_the_prefix_does_not_open_a_hole(self, sql):
        """
        A statement that is *only* a comment masks to nothing and must not read
        as a valid SELECT, and a comment must not launder what follows it.
        """
        assert not validate_sql(sql).is_valid


class TestEveryDialectReading:
    """
    The SQL must pass however each engine TDB runs on reads its literals and
    comments. Each case passes the old ANSI-only scanner — the second statement
    sits inside what that scanner took for a string or comment — and is a
    second statement to the engine named.
    """

    @pytest.mark.parametrize(
        ("engine", "sql"),
        [
            ("postgres/duckdb dollar-quote", "SELECT $$'$$; SELECT 2; SELECT '"),
            ("postgres E-string", "SELECT E'\\'' AS a; SELECT 2; SELECT 'x'"),
            ("postgres/tsql nesting", "SELECT 1 /* /* */ ' */ ; SELECT 2; SELECT 'a'"),
            ("mysql backslash", "SELECT 'a\\'' AS x; SELECT 2 -- '"),
            ("mysql hash comment", "SELECT 1 # '\n; SELECT 2 -- '"),
            ("mysql -- needs a space", "SELECT 1 --x; SELECT 2\n"),
            ("snowflake // comment", "SELECT 1 // '\n; SELECT 2 -- '"),
        ],
    )
    def test_a_statement_hidden_from_one_reading_is_refused(self, engine, sql):
        assert not validate_sql(sql).is_valid, engine

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT data #>> '{a,b}' FROM t",
            "SELECT $1",
            "SELECT a$b FROM t",
            "SELECT $$plain$$",
            "SELECT 'it''s'",
            "SELECT 1 /* outer /* inner */ still */",
            "SELECT * FROM t WHERE p LIKE 'C:\\\\x%'",
        ],
    )
    def test_ordinary_read_only_sql_still_passes(self, sql):
        assert validate_sql(sql).is_valid


class TestReplaceIsAFunctionNotAStatement:
    """
    `REPLACE` is refused as MySQL's `REPLACE [INTO] t …` statement, never as
    the `replace()` string function — which pgjdbc's and Metabase's catalog
    queries use, and which was refused on every path until 0.8.0.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT replace(customer, 'c', 'x') FROM data",
            "SELECT REPLACE (customer, 'c', 'x') FROM data",
            "SELECT replace/* note */(customer, 'c', 'x') FROM data",
            "WITH t AS (SELECT replace(note, 'a', 'b') AS n FROM data) SELECT n FROM t",
        ],
    )
    def test_the_function_is_accepted(self, sql):
        assert validate_sql(sql).is_valid

    @pytest.mark.parametrize(
        "sql",
        [
            "REPLACE INTO t VALUES (1)",
            "REPLACE t VALUES (1)",
            "REPLACE /**/ INTO t VALUES (1)",
            "SELECT 1; REPLACE INTO t VALUES (1)",
            "WITH c AS (SELECT 1) REPLACE INTO t SELECT * FROM c",
            "SELECT 1 /*! REPLACE INTO t */",
        ],
    )
    def test_the_statement_is_refused(self, sql):
        assert not validate_sql(sql).is_valid
