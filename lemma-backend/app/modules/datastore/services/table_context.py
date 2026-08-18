from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.value_converter import ValueConverter


@dataclass(frozen=True, slots=True)
class TableHydration:
    """The two fields authorization would otherwise re-read the table for.

    ``_hydrate_resource`` short-circuits once a ``ResourceRef`` carries its
    visibility, so a caller that has just selected the table row can hand these
    over and skip a second read of it. They travel as a pair because hydration
    fills them as a pair — see ``ResourceRef.hydrated_table``.
    """

    visibility: str | None
    owner_user_id: UUID | None

    @classmethod
    def of(cls, table: DatastoreTableEntity) -> "TableHydration":
        return cls(visibility=table.visibility, owner_user_id=table.user_id)


@dataclass
class TableContext:
    """Resolved schema + operation flags for a single table's record ops.

    A lean data holder: table identity, the column schema, and the
    ``events_enabled`` flag. Authorization flows through the central ambient
    context (`get_current_context()`), never through this object. Record
    validation lives in ``RecordValidator``; value conversion in
    ``ValueConverter``.
    """

    pod_id: UUID
    table_id: UUID
    table_name: str
    schema_name: str
    columns: List[ColumnSchema]
    primary_key_column: str
    enable_rls: bool
    events_enabled: bool = False
    _column_map: Dict[str, ColumnSchema] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._column_map = {col.name: col for col in self.columns}

    @classmethod
    def from_table_entity(
        cls,
        table: DatastoreTableEntity,
        schema_name: str,
        events_enabled: bool = False,
    ) -> "TableContext":
        return cls(
            pod_id=table.pod_id,
            table_id=table.id,
            table_name=table.table_name,
            schema_name=schema_name,
            columns=table.columns,
            primary_key_column=table.primary_key_column,
            enable_rls=table.enable_rls,
            events_enabled=events_enabled,
        )

    def get_column(self, name: str) -> Optional[ColumnSchema]:
        return self._column_map.get(name)

    def get_primary_key_schema(self) -> ColumnSchema:
        col = self._column_map.get(self.primary_key_column)
        if col:
            return col

        if self.primary_key_column == "id":
            return ColumnSchema(
                name="id",
                type=DatastoreDataType.UUID,
                required=True,
                unique=True,
                auto=True,
                default="gen_random_uuid()",
            )

        raise DatastoreValidationError(
            f"Primary key column '{self.primary_key_column}' not found in schema"
        )

    def parse_primary_key(self, value: Any) -> int | str | UUID:
        pk_col = self.get_primary_key_schema()
        try:
            return ValueConverter.parse_primary_key(value, pk_col)
        except ValueError as exc:
            raise DatastoreValidationError(str(exc)) from exc
