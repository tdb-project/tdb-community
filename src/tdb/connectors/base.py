"""
TDB – Base Connector

Every connector (CSV, SQLite, Postgres, …) must implement this interface.
The query router only talks to BaseConnector — it never imports concrete types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


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
        The connector is responsible for enforcing the limit.
        """
