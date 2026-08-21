from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.authorization.context import ResourceType
from app.core.domain.aggregate import AggregateRoot
from app.core.helpers.slug import normalize_resource_name
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreReservedResourceError,
    DatastoreTableNotFoundError,
    DatastoreValidationError,
)

RESERVED_TABLE_PREFIX = "reserved_"
SYSTEM_COLUMNS = {"created_at", "updated_at", "user_id"}

# --- Computed-column expression safety -------------------------------------
# GENERATED ALWAYS AS (<expression>) cannot be parameterized, so the
# expression is validated against a strict allow-list before it ever reaches
# DDL. Only references to existing columns, a whitelist of pure functions and
# operators, and numeric/string literals are permitted.
_EXPR_FUNCTION_WHITELIST = frozenset(
    {
        "COALESCE",
        "NULLIF",
        "GREATEST",
        "LEAST",
        "LOWER",
        "UPPER",
        "TRIM",
        "BTRIM",
        "LENGTH",
        "ABS",
        "ROUND",
        "CEIL",
        "FLOOR",
        "CONCAT",
    }
)
_EXPR_KEYWORD_WHITELIST = frozenset(
    {"AND", "OR", "NOT", "IS", "NULL", "TRUE", "FALSE", "MOD"}
)
_EXPR_FORBIDDEN_SUBSTRINGS = (";", "--", "/*", "*/", "::")
_EXPR_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<string>'(?:[^']|'')*')
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op>\|\||<=|>=|<>|!=|[+\-*/%=<>(),])
    """,
    re.VERBOSE,
)


def validate_computed_expression(
    expression: str | None, known_columns: set[str] | None = None
) -> None:
    """Allow-list validation for a GENERATED column expression.

    Permits only references to known columns (when ``known_columns`` is
    supplied), a whitelist of pure functions/operators, and numeric/string
    literals. Rejects statement terminators, comments, casts, function calls
    outside the whitelist, and any unsupported character. Raises
    :class:`DatastoreValidationError` on rejection.

    When ``known_columns`` is ``None`` (construction-time syntax pass), bare
    identifiers are assumed to be column references; the authoritative
    membership check runs again in the schema manager with the real column
    set.
    """
    expr = (expression or "").strip()
    if not expr:
        raise DatastoreValidationError("Computed columns must have an expression")
    for forbidden in _EXPR_FORBIDDEN_SUBSTRINGS:
        if forbidden in expr:
            raise DatastoreValidationError(
                f"Computed expression contains a forbidden token: {forbidden!r}"
            )

    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        match = _EXPR_TOKEN_RE.match(expr, pos)
        if match is None:
            raise DatastoreValidationError(
                "Computed expression contains an unsupported character near "
                f"{expr[pos : pos + 16]!r}"
            )
        pos = match.end()
        kind = match.lastgroup
        if kind == "ws":
            continue
        tokens.append((kind, match.group(kind)))

    known_lower = (
        {c.lower() for c in known_columns} if known_columns is not None else None
    )
    for index, (kind, value) in enumerate(tokens):
        if kind != "ident":
            continue
        upper = value.upper()
        next_is_call = index + 1 < len(tokens) and tokens[index + 1] == ("op", "(")
        if next_is_call:
            if upper not in _EXPR_FUNCTION_WHITELIST:
                raise DatastoreValidationError(
                    f"Computed expression calls a disallowed function '{value}'"
                )
            continue
        if upper in _EXPR_KEYWORD_WHITELIST or upper in _EXPR_FUNCTION_WHITELIST:
            continue
        if known_lower is None or value.lower() in known_lower:
            continue
        raise DatastoreValidationError(
            f"Computed expression references disallowed identifier '{value}'. "
            "Only existing columns and whitelisted functions are permitted."
        )


class DatastoreDataType(str, Enum):
    """Supported data types for datastore columns."""

    TEXT = "TEXT"
    FILE_PATH = "FILE_PATH"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    JSON = "JSON"
    DATE = "DATE"
    DATETIME = "DATETIME"
    UUID = "UUID"
    USER = "USER"
    VECTOR = "VECTOR"
    SERIAL = "SERIAL"
    ENUM = "ENUM"


class ForeignKeySpec(BaseModel):
    """Specification for a foreign key constraint."""

    references: str = Field(..., description="Format: 'other_table.primary_key_column'")


class ColumnSchema(BaseModel):
    """Schema for a datastore table column."""

    name: str = Field(..., description="Column name")
    type: DatastoreDataType = Field(..., description="Column data type")
    description: Optional[str] = Field(None, description="Column description")
    required: bool = Field(
        False, description="Whether the column is required (NOT NULL)"
    )
    unique: bool = Field(
        False, description="Whether the column must have unique values"
    )
    default: Optional[Any] = Field(None, description="Default value for the column")
    foreign_key: Optional[ForeignKeySpec] = Field(
        None, description="Foreign key specification"
    )
    max_length: Optional[int] = Field(
        None, description="Maximum length for TEXT columns"
    )
    type_params: Optional[Dict[str, Any]] = Field(
        None, description="Additional type-specific parameters"
    )
    options: Optional[List[str]] = Field(
        None, description="Allowed options for ENUM columns"
    )
    auto: bool = Field(False, description="Whether the column is auto-generated")
    system: bool = Field(
        False,
        description="Whether the column is system-managed by the backend.",
    )
    computed: bool = Field(False, description="Whether this is a computed column")
    expression: Optional[str] = Field(
        None, description="SQL expression for computed columns"
    )

    @model_validator(mode="after")
    def _validate_column_constraints(self) -> "ColumnSchema":
        if not self.name or not self.name.strip():
            raise ValueError("Column name cannot be empty")

        if not all(c.isalnum() or c == "_" for c in self.name):
            raise ValueError(
                f"Invalid column name '{self.name}'. Only alphanumeric and underscore are allowed"
            )

        if self.computed:
            if not self.expression:
                raise ValueError("Computed columns must have an expression")
            if self.required or self.auto or self.unique or self.foreign_key:
                raise ValueError("Computed columns cannot have constraints")
            # Construction-time syntax/allow-list pass (no column set yet); the
            # schema manager re-validates against the real columns before DDL.
            try:
                validate_computed_expression(self.expression)
            except DatastoreValidationError as exc:
                raise ValueError(str(exc)) from exc
        elif self.expression:
            raise ValueError("Only computed columns can have expressions")

        if self.type == DatastoreDataType.ENUM and not self.options:
            raise ValueError("ENUM columns must define options")

        if self.options and self.type != DatastoreDataType.ENUM:
            raise ValueError("Only ENUM columns can define options")

        if self.default is not None and not self.computed and not self.auto:
            if not isinstance(self.default, (bool, int, float, str)):
                raise ValueError(
                    "Column default must be a scalar literal "
                    f"(got {type(self.default).__name__})"
                )
            if (
                self.type == DatastoreDataType.ENUM
                and self.options
                and self.default not in self.options
            ):
                raise ValueError(
                    f"ENUM default '{self.default}' must be one of {self.options}"
                )

        if self.max_length is not None and self.type not in {
            DatastoreDataType.TEXT,
            DatastoreDataType.FILE_PATH,
        }:
            raise ValueError(
                "max_length is only supported for TEXT and FILE_PATH columns"
            )

        allowed_auto_types = {
            DatastoreDataType.INTEGER,
            DatastoreDataType.SERIAL,
            DatastoreDataType.UUID,
            DatastoreDataType.USER,
        }
        if self.system:
            allowed_auto_types.add(DatastoreDataType.DATETIME)

        if self.auto and self.type not in allowed_auto_types:
            raise ValueError(
                "Auto columns must be INTEGER, SERIAL, UUID, USER, or system-managed DATETIME"
            )

        return self


def materialize_table_columns(
    primary_key_column: str,
    columns: list[ColumnSchema],
    *,
    enable_rls: bool,
) -> list[ColumnSchema]:
    """Return the full physical table schema, including implicit/system columns."""
    materialized = list(columns)
    existing_names = {column.name for column in materialized}

    reserved_system_names = {"created_at", "updated_at"}
    conflicting_system_names = existing_names & reserved_system_names
    if conflicting_system_names:
        raise DatastoreValidationError(
            "System-managed columns must not be declared explicitly: "
            + ", ".join(sorted(conflicting_system_names))
        )

    if enable_rls and "user_id" in existing_names:
        raise DatastoreValidationError(
            "System-managed column 'user_id' must not be declared explicitly when RLS is enabled"
        )

    if primary_key_column == "id" and "id" not in existing_names:
        materialized.insert(
            0,
            ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
            ),
        )
        existing_names.add("id")

    for system_column in (
        ColumnSchema(
            name="created_at",
            type=DatastoreDataType.DATETIME,
            auto=True,
            system=True,
        ),
        ColumnSchema(
            name="updated_at",
            type=DatastoreDataType.DATETIME,
            auto=True,
            system=True,
        ),
    ):
        if system_column.name not in existing_names:
            materialized.append(system_column)
            existing_names.add(system_column.name)

    if enable_rls and "user_id" not in existing_names:
        materialized.append(
            ColumnSchema(
                name="user_id",
                type=DatastoreDataType.UUID,
                required=True,
                auto=True,
                system=True,
            )
        )

    return materialized


class DatastoreEntity(AggregateRoot):
    """Datastore aggregate root."""

    pod_id: UUID
    name: str
    description: Optional[str] = None
    events_enabled: bool = False
    search_enabled: bool = True
    graph_rag_enabled: bool = False
    graph_instruction: Optional[str] = None

    def validate_name(self) -> None:
        if not self.name or not self.name.strip():
            raise DatastoreValidationError("Datastore name cannot be empty")
        self.name = normalize_resource_name(self.name)

    def rename(self, name: str) -> None:
        if not name or not name.strip():
            raise DatastoreValidationError("Datastore name cannot be empty")
        self.name = normalize_resource_name(name)

    def update_description(self, description: Optional[str]) -> None:
        self.description = description

    def set_events_enabled(self, events_enabled: bool) -> None:
        self.events_enabled = events_enabled

    def set_search_enabled(self, search_enabled: bool) -> None:
        self.search_enabled = search_enabled

    def set_graph_rag_enabled(self, graph_rag_enabled: bool) -> None:
        self.graph_rag_enabled = graph_rag_enabled

    def update_graph_instruction(self, graph_instruction: Optional[str]) -> None:
        self.graph_instruction = graph_instruction

    def apply_update(self, update: DatastoreUpdateEntity, *, actor_id: UUID) -> None:
        if update.description is not None:
            self.update_description(update.description)
        if update.events_enabled is not None:
            self.set_events_enabled(update.events_enabled)
        if update.search_enabled is not None:
            self.set_search_enabled(update.search_enabled)
        if update.graph_rag_enabled is not None:
            self.set_graph_rag_enabled(update.graph_rag_enabled)
        if update.graph_instruction is not None:
            self.update_graph_instruction(update.graph_instruction)
        self.mark_updated(actor_id)

    def mark_created(self, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreCreatedEvent

        self.add_event(
            DatastoreCreatedEvent(
                pod_id=self.pod_id,
                actor_id=actor_id,
                name=self.name,
            )
        )

    def mark_updated(self, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreUpdatedEvent

        self.add_event(
            DatastoreUpdatedEvent(
                pod_id=self.pod_id,
                actor_id=actor_id,
            )
        )

    def mark_deleted(self, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreDeletedEvent

        self.add_event(
            DatastoreDeletedEvent(
                pod_id=self.pod_id,
                actor_id=actor_id,
            )
        )


class DatastoreTableEntity(AggregateRoot):
    """Datastore table aggregate root."""

    resource_type: ClassVar[ResourceType] = ResourceType.DATASTORE_TABLE

    pod_id: UUID
    user_id: UUID | None = None
    table_name: str
    primary_key_column: str = "id"
    columns: List[ColumnSchema]
    config: Optional[Dict[str, Any]] = None
    enable_rls: bool = True
    visibility: str = "POD"
    allowed_actions: List[str] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.table_name

    def validate_structure(self) -> None:
        if not self.table_name or not self.table_name.strip():
            raise DatastoreValidationError("Table name cannot be empty")

        if not all(c.isalnum() or c == "_" for c in self.table_name):
            raise DatastoreValidationError(
                f"Invalid table name '{self.table_name}'. Use alphanumeric and underscore only"
            )

        if not self.columns:
            raise DatastoreValidationError("Table must define at least one column")

        seen: set[str] = set()
        for column in self.columns:
            if column.name in seen:
                raise DatastoreConflictError(
                    f"Duplicate column '{column.name}' in table definition"
                )
            seen.add(column.name)

        if self.primary_key_column != "id" and self.primary_key_column not in seen:
            raise DatastoreValidationError(
                f"Primary key column '{self.primary_key_column}' is not defined"
            )

        if self.primary_key_column in SYSTEM_COLUMNS:
            raise DatastoreValidationError(
                f"System-managed column '{self.primary_key_column}' cannot be the primary key"
            )

    def update_config(self, config: dict[str, Any], *, actor_id: UUID) -> None:
        self.config = config
        self.mark_updated(actor_id)

    def add_column(self, column: ColumnSchema, *, actor_id: UUID) -> None:
        if any(c.name == column.name for c in self.columns):
            raise DatastoreConflictError(f"Column '{column.name}' already exists")

        self.columns = [*self.columns, column]
        self.mark_column_added(column.name, actor_id)

    def remove_column(self, column_name: str, *, actor_id: UUID) -> None:
        if column_name == self.primary_key_column:
            raise DatastoreValidationError("Primary key column cannot be removed")

        if column_name in SYSTEM_COLUMNS:
            raise DatastoreReservedResourceError(
                f"System column '{column_name}' cannot be removed"
            )

        if not any(c.name == column_name for c in self.columns):
            raise DatastoreTableNotFoundError(
                f"Column '{column_name}' not found in table metadata"
            )

        self.columns = [c for c in self.columns if c.name != column_name]
        self.mark_column_removed(column_name, actor_id)

    def mark_created(self, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreTableCreatedEvent

        self.add_event(
            DatastoreTableCreatedEvent(
                pod_id=self.pod_id,
                table_id=self.id,
                table_name=self.table_name,
                actor_id=actor_id,
            )
        )

    def mark_updated(self, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreTableUpdatedEvent

        self.add_event(
            DatastoreTableUpdatedEvent(
                pod_id=self.pod_id,
                table_id=self.id,
                table_name=self.table_name,
                actor_id=actor_id,
            )
        )

    def mark_deleted(self, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreTableDeletedEvent

        self.add_event(
            DatastoreTableDeletedEvent(
                pod_id=self.pod_id,
                table_id=self.id,
                table_name=self.table_name,
                actor_id=actor_id,
            )
        )

    def mark_column_added(self, column_name: str, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import DatastoreTableColumnAddedEvent

        self.add_event(
            DatastoreTableColumnAddedEvent(
                pod_id=self.pod_id,
                table_id=self.id,
                table_name=self.table_name,
                column_name=column_name,
                actor_id=actor_id,
            )
        )

    def mark_column_removed(self, column_name: str, actor_id: UUID) -> None:
        from app.modules.datastore.domain.events import (
            DatastoreTableColumnRemovedEvent,
        )

        self.add_event(
            DatastoreTableColumnRemovedEvent(
                pod_id=self.pod_id,
                table_id=self.id,
                table_name=self.table_name,
                column_name=column_name,
                actor_id=actor_id,
            )
        )


class DatastoreTableSummaryEntity(BaseModel):
    """Lightweight table view for list responses.

    Carries a cheap ``column_count`` instead of the full ``columns`` schema, so
    list endpoints avoid validating every column into ``ColumnSchema``.
    """

    id: UUID
    pod_id: UUID
    table_name: str
    primary_key_column: str = "id"
    column_count: int = 0
    enable_rls: bool = True
    visibility: str = "POD"
    allowed_actions: List[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def name(self) -> str:
        return self.table_name


class DatastoreUpdateEntity(BaseModel):
    """Datastore update entity."""

    description: Optional[str] = None
    events_enabled: Optional[bool] = None
    search_enabled: Optional[bool] = None
    graph_rag_enabled: Optional[bool] = None
    graph_instruction: Optional[str] = None


def ensure_table_mutable(table_name: str) -> None:
    if table_name.startswith(RESERVED_TABLE_PREFIX):
        raise DatastoreReservedResourceError("Cannot modify reserved tables")
