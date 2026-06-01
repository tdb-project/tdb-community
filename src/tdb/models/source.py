"""
TDB Source Models
A 'source' is a registered data source (CSV file, database, etc.)
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    CSV = "csv"
    # Future phases:
    # SQLITE = "sqlite"
    # POSTGRES = "postgres"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class SourceBase(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=100, description="Human-readable name"
    )
    description: str = Field(default="", max_length=500)
    source_type: SourceType
    path: str = Field(..., description="File path or connection string")


class SourceCreate(SourceBase):
    pass


class Source(SourceBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: SourceStatus = SourceStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    row_count: int | None = None
    column_count: int | None = None

    model_config = {"from_attributes": True}


class SchemaColumn(BaseModel):
    name: str
    type: str


class SourceSchema(BaseModel):
    source_id: str
    source_name: str
    columns: list[SchemaColumn]
    inspected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QueryRequest(BaseModel):
    source_id: str
    sql: str = Field(..., min_length=1, max_length=10_000)
    limit: int = Field(
        default=100,
        ge=1,
        le=1_000,
        description="Max rows to return (default 100, hard cap 1,000)",
    )


class QueryResult(BaseModel):
    source_id: str
    sql: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool = False
    executed_in_ms: float


class QueryResponse(BaseModel):
    source_id: str
    sql: str
    columns: list[str]
    rows: list[dict]
    rows_returned: int
    truncated: bool = False
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceRegistrationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    source_type: SourceType
    connection: dict
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list)


class SourceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    source_type: SourceType
    connection: dict
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    registered_by: str = ""
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: SourceStatus = SourceStatus.ACTIVE


class SourceSummary(BaseModel):
    id: str
    name: str
    source_type: SourceType
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
