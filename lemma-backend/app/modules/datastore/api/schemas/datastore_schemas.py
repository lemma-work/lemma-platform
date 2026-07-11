from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any, Union
from uuid import UUID
from pydantic import AliasChoices, BaseModel, Field, ConfigDict
from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.file_entities import SearchMethod


class CreateTableRequest(BaseModel):
    """Schema for creating a new table."""

    name: str = Field(
        ...,
        validation_alias=AliasChoices("name", "table_name"),
        description=(
            "Table name. Use alphanumeric and underscore only. Names prefixed with "
            "`reserved_` are system-managed and should not be user-created."
        ),
        min_length=1,
        max_length=255,
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
    config: Optional[Dict[str, Any]] = Field(
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

    @property
    def table_name(self) -> str:
        return self.name


class UpdateTableRequest(BaseModel):
    """Schema for updating a table."""

    config: Dict[str, Any] | None = Field(
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


class AddColumnRequest(BaseModel):
    """Schema for adding a column to a table."""

    column: ColumnSchema = Field(
        ...,
        description=(
            "Column definition to append to the table. Existing column names cannot be reused."
        ),
    )


class CreateRecordRequest(BaseModel):
    """Schema for creating a record."""

    data: Dict[str, Any] = Field(
        ...,
        description="Record object keyed by table column names.",
    )


class UpdateRecordRequest(BaseModel):
    """Schema for updating a record."""

    data: Dict[str, Any] = Field(
        ...,
        description="Partial record patch keyed by table column names.",
    )


class BulkCreateRecordsRequest(BaseModel):
    """Schema for bulk creating records."""

    records: List[Dict[str, Any]] = Field(
        ...,
        description="List of record payload objects to insert.",
    )
    upsert: bool = Field(
        default=False,
        description=(
            "When true, insert records and update existing rows that conflict on the "
            "table primary key."
        ),
    )


class BulkUpdateRecordsRequest(BaseModel):
    """Schema for bulk updating records."""

    records: List[Dict[str, Any]] = Field(
        ...,
        description=(
            "List of record updates. Each item must include the table primary key field."
        ),
    )


class BulkDeleteRecordsRequest(BaseModel):
    """Schema for bulk deleting records."""

    record_ids: List[Union[str, int, UUID]] = Field(
        ...,
        description="Primary key values to delete.",
    )


class DatastoreQueryRequest(BaseModel):
    """Schema for executing read-only SQL within a datastore."""

    query: str = Field(
        ...,
        description=(
            "Read-only SQL query executed inside this datastore schema. A single "
            "SELECT statement only; mutating statements (INSERT, UPDATE, DELETE, "
            "ALTER, DROP, CREATE, TRUNCATE, ...) and cross-schema references are "
            "rejected. Joins, aggregates, and subqueries across tables are allowed, "
            "including RLS-enabled tables — rows of an RLS table are scoped to the "
            "caller unless they administer it. Example: "
            "`SELECT id, amount FROM expenses WHERE amount > 100 ORDER BY created_at DESC LIMIT 20`."
        ),
        min_length=1,
    )


class RecordAccessMode(str, Enum):
    """Row-visibility mode for record reads/writes on RLS-enabled tables.

    ``USER`` (the default when the ``mode`` param is omitted) scopes rows to the
    caller's own ``user_id`` so app apps keep their per-user semantics — pod
    admins included. ``ADMIN`` returns and operates on every member's rows and
    requires the caller to administer the table (otherwise the request is
    rejected). The mode is a no-op for non-RLS tables, whose rows are shared by
    every member regardless.

    Endpoints expose this as an *optional* query param (default ``None`` ==
    ``USER``) rather than one defaulting to ``RecordAccessMode.USER``: a named
    enum query param carrying a literal default makes the TypeScript generator
    emit ``mode: RecordAccessMode = 'USER'``, which fails strict typecheck (a raw
    string is not assignable to the enum). An optional param generates the valid
    ``mode?: RecordAccessMode`` while keeping one shared, CAPS-valued enum type.
    """

    USER = "USER"
    ADMIN = "ADMIN"


class RecordFilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    LIKE = "like"
    ILIKE = "ilike"


class RecordFilter(BaseModel):
    field: str = Field(..., min_length=1, description="Table column name to filter on.")
    op: RecordFilterOperator = Field(
        default=RecordFilterOperator.EQ,
        description="Comparison operator to apply.",
    )
    value: Any = Field(..., description="Filter comparison value.")


class RecordSortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class RecordSort(BaseModel):
    field: str = Field(..., min_length=1, description="Table column name to sort by.")
    direction: RecordSortDirection = Field(
        default=RecordSortDirection.ASC,
        description="Sort direction.",
    )


class CreateFolderRequest(BaseModel):
    path: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    parent_id: Optional[UUID] = None
    description: Optional[str] = None
    visibility: str | None = None


class FileSearchScopeMode(str, Enum):
    DIRECT = "DIRECT"
    SUBTREE = "SUBTREE"


class FileSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    search_method: SearchMethod = Field(default=SearchMethod.HYBRID)
    scope_path: Optional[str] = Field(
        default=None,
        description="Optional folder path to scope search results.",
    )
    scope_mode: FileSearchScopeMode = Field(default=FileSearchScopeMode.SUBTREE)


class DirectoryTreeNode(BaseModel):
    path: str
    name: str
    kind: str
    visibility: str | None = None
    has_more_files: bool = False
    children: List["DirectoryTreeNode"] = Field(default_factory=list)


class DirectoryTreeResponse(BaseModel):
    root_path: str
    files_per_directory: int
    tree: DirectoryTreeNode


DirectoryTreeNode.model_rebuild()


class TableResponse(BaseModel):
    """Schema for table response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pod_id: UUID
    name: str
    primary_key_column: str
    columns: List[ColumnSchema]
    config: Optional[Dict[str, Any]]
    enable_rls: bool
    visibility: str = "POD"
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
    created_at: datetime
    updated_at: datetime
    allowed_actions: List[str] = Field(default_factory=list)


class TableListResponse(BaseModel):
    """Schema for table list response."""

    items: List[TableSummaryResponse]
    limit: int
    total: int | None = None
    next_page_token: Optional[str] = None


class RecordListResponse(BaseModel):
    """Schema for record list response."""

    items: List[Dict[str, Any]]
    total: int | None = None
    limit: int
    next_page_token: str | None = None


class DatastoreQueryResponse(BaseModel):
    """Schema for read-only datastore query results."""

    items: List[Dict[str, Any]]
    total: int


class DatastoreCountResponse(BaseModel):
    """Schema for bulk mutation responses reporting an affected-row count."""

    count: int


class FileChildSchema(BaseModel):
    """A derived child artifact of a processed document (converted markdown,
    an extracted figure, or a renderable page). Fetch its bytes from
    ``GET …/files/children/content?path=<child path>``."""

    name: str
    path: str
    kind: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    page_number: Optional[int] = None


class FileChildrenResponse(BaseModel):
    path: str
    items: List[FileChildSchema] = Field(default_factory=list)


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pod_id: UUID
    owner_user_id: UUID | None = None
    path: str
    kind: str
    visibility: str = "PERSONAL"
    name: str
    description: Optional[str]
    mime_type: Optional[str] = None
    size_bytes: int = 0
    search_enabled: bool = True
    status: str
    metadata: Optional[dict] = None
    indexed_at: Optional[datetime] = None
    last_processing_error: Optional[str] = None
    processing_attempts: int = 0
    content_sha256: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FileDetailResponse(FileResponse):
    allowed_actions: List[str] = Field(default_factory=list)


class FileSummaryResponse(BaseModel):
    """Lean file/folder shape for list responses.

    Omits the heavy `metadata` (JSONB) and `last_processing_error` (can be
    multi-KB) — fetch those from `file.get`. Keeps `description` (small, may be
    shown in list views).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pod_id: UUID
    owner_user_id: UUID | None = None
    path: str
    kind: str
    visibility: str = "PERSONAL"
    name: str
    # Required-nullable (like FileResponse) so a summary stays structurally
    # assignable to FileDetailResponse for consumers that share a file type.
    description: Optional[str]
    mime_type: Optional[str] = None
    size_bytes: int = 0
    search_enabled: bool = True
    status: str
    indexed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    allowed_actions: List[str] = Field(default_factory=list)


class FileListResponse(BaseModel):
    items: List[FileSummaryResponse]
    limit: int
    total: int | None = None
    next_page_token: Optional[str] = None


class FileSearchResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: UUID
    path: str
    chunk_index: int
    content: str
    metadata: Optional[dict] = None
    score: float
    page_number: Optional[int] = None
    page_end: Optional[int] = None


class FileSearchResponse(BaseModel):
    items: List[FileSearchResultSchema]
    total: int
    query: str
    search_method: SearchMethod


class FileUrlResponse(BaseModel):
    path: str
    url: str
    app_url: str
    expires_at: datetime


class FileSignedUrlRequest(BaseModel):
    expires_seconds: Optional[int] = None
    max_hits: Optional[int] = None


class FileSignedUrlResponse(BaseModel):
    path: str
    signed_url: str
    expires_at: datetime
    max_hits: int
