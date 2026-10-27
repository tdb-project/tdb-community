"""
TDB – Base Connector

Every connector (CSV, SQLite, Postgres, …) must implement this interface.
The query router only talks to BaseConnector — it never imports concrete types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from tdb.connectors.values import json_safe_rows


class QueryError(Exception):
    """
    The source rejected the caller's SQL: an unknown column or table, a syntax
    error, a value that does not convert.

    Each connector translates its own driver's errors for these into this, so the
    routers can answer 400 instead of letting the caller's mistake fall into the
    generic 500 path. What counts as the caller's mistake is decided by the
    connector that knows its driver; the routers never inspect driver errors.
    """


@dataclass
class ConnectorResult:
    """
    Unified query result returned by every connector.
    columns   : ordered list of column names
    rows      : list of dicts  [{"col": value, ...}, ...]
    truncated : True if the row cap dropped rows the query would otherwise return
    """

    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = False

    def __post_init__(self) -> None:
        # Here rather than in each router, so REST and MCP send the same JSON.
        # See tdb.connectors.values.
        self.rows = json_safe_rows(self.rows)


class BaseConnector(ABC):
    """Abstract base that every TDB connector must subclass."""

    @abstractmethod
    def validate_connection(self) -> bool:
        """
        Return True if the underlying data source is reachable.
        Called before execute() to give a clear error message.
        """

    @abstractmethod
    def get_schema(self) -> dict[str, str]:
        """
        Return a mapping of  column_name → type_string.
        Used by the schema endpoint and AI introspection.
        """

    @abstractmethod
    def execute(self, sql: str, limit: int = 100) -> ConnectorResult:
        """
        Run a read-only SQL SELECT and return a ConnectorResult.

        The connector is responsible for enforcing the limit, and must do so by
        bounding the fetch — read at most ``limit + 1`` rows. An injected LIMIT
        cannot be relied on: the injector skips any query already containing
        the token. The extra row is what marks the result ``truncated``.
        """
