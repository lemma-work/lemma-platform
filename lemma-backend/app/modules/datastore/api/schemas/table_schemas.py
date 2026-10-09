"""The table API's request and response shapes.

Apart from ``datastore_schemas`` so that file stays readable: a table's shape
is one subject -- its columns, its row policies, who may read it -- and every
other datastore schema is about records and files.
"""

from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.infrastructure.sql_identifiers import (
    MAX_IDENTIFIER_BYTES,
)


_CONTACT_COLUMNS = (
    "Of a contact-owned table, the columns a contact may read of their own "
    "rows -- an explicit choice, so a column added later stays members-only "
    "until it is chosen too."
)


class CreateTableRequest(BaseModel):
    """Schema for creating a new table."""

    name: str = Field(
        ...,
        validation_alias=AliasChoices("name", "table_name"),
        description=(
            "Table name. Use alphanumeric and underscore only, at most "
            f"{MAX_IDENTIFIER_BYTES} bytes — PostgreSQL truncates longer names "
            "and two that share that prefix would become one table. Names "
            "prefixed with `reserved_` are system-managed and should not be "
            "user-created."
        ),
        min_length=1,
        max_length=MAX_IDENTIFIER_BYTES,
    )
    primary_key_column: str = Field(
        default="id",
        description=(
            "Primary key column name. If not `id`, it must also be declared in `columns`."
        ),
    )
    columns: List[ColumnSchema] = Field(
        ...,
        description=(
            "Table column definitions. Each column name must be unique. "
            "Use `type`, `required`, `default`, `foreign_key`, and `computed` as needed. "
            "The backend also materializes physical system columns so table metadata reflects "
            "the real schema: `id` when omitted as the primary key, `created_at`, `updated_at`, "
            "and `user_id` when RLS is enabled."
        ),
        min_length=1,
    )
    config: Optional[Dict[str, object]] = Field(
        default=None,
        description=(
            "Optional table metadata/configuration. This updates table config metadata "
            "and does not directly alter physical columns."
        ),
    )
    enable_rls: bool = Field(
        default=True,
        description=(
            "Enable row-level security for this table. When enabled, API reads/writes are scoped by current user."
        ),
    )
    visibility: str | None = None
    contact_owned: bool = Field(
        default=False,
        description=(
            "Rows the pod keeps about its contacts. Adds a `contact_id` column; "
            "every member sees every row, and a contact's run reads only rows "
            "naming that contact. Not combinable with `enable_rls`, and never "
            "Public. Requires `contact_columns`."
        ),
    )
    contact_columns: List[str] | None = Field(
        default=None,
        description=_CONTACT_COLUMNS,
    )

    @property
    def table_name(self) -> str:
        return self.name


class UpdateTableRequest(BaseModel):
    """Schema for updating a table."""

    config: Dict[str, object] | None = Field(
        default=None,
        description="Replacement metadata/config payload for the table.",
    )
    visibility: str | None = None
    enable_rls: bool | None = Field(
        default=None,
        description=(
            "Toggle per-user row-level security. Only allowed on an empty table: "
            "enabling adds the user_id ownership column and isolation policy, "
            "disabling removes the policy. Omit to leave RLS unchanged."
        ),
    )
    contact_owned: bool | None = Field(
        default=None,
        description=(
            "Make the table contact-owned, or stop it being. Enabling adds a "
            "`contact_id` column if there is none (rows without one are seen by "
            "members only) and requires `contact_columns`. Omit to leave it "
            "unchanged."
        ),
    )
    contact_columns: List[str] | None = Field(
        default=None,
        description=_CONTACT_COLUMNS + " Omit to leave them unchanged.",
    )


class AddColumnRequest(BaseModel):
    """Schema for adding a column to a table."""

    column: ColumnSchema = Field(
        ...,
        description=(
            "Column definition to append to the table. Existing column names cannot be reused."
        ),
    )


class TableResponse(BaseModel):
    """Schema for table response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pod_id: UUID
    name: str
    primary_key_column: str
    columns: List[ColumnSchema]
    config: Optional[Dict[str, object]]
    enable_rls: bool
    visibility: str = "POD"
    contact_owned: bool = False
    contact_columns: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TableDetailResponse(TableResponse):
    """Schema for table detail response."""

    allowed_actions: List[str] = Field(default_factory=list)


class TableSummaryResponse(BaseModel):
    """Lean table shape for list responses.

    Omits the full `columns` definitions and `config` — fetch those from
    `table.get`. Exposes a cheap `column_count` for list views.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pod_id: UUID
    name: str
    primary_key_column: str
    column_count: int = 0
    enable_rls: bool
    visibility: str = "POD"
    contact_owned: bool = False
    contact_columns: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    allowed_actions: List[str] = Field(default_factory=list)


class TableListResponse(BaseModel):
    """Schema for table list response."""

    items: List[TableSummaryResponse]
    limit: int
    total: int | None = None
    next_page_token: Optional[str] = None
