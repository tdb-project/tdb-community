"""
Tests for the SQL validator.
validate_sql() returns ValidationResult(is_valid, reason) — it does not raise.
"""

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
