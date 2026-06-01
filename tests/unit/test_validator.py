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
