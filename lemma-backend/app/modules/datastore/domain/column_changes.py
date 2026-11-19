"""What an existing column may be changed to: more ENUM options, no foreign key."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.datastore.domain.data_types import DatastoreDataType
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import (
    DatastoreColumnChangeRefusedError,
    DatastoreTableNotFoundError,
)


def widened_enum_column(
    table: DatastoreTableEntity, column_name: str, options: Sequence[str]
) -> ColumnSchema | None:
    """``table``'s ENUM column ``column_name`` accepting ``options``, or
    ``None`` when those are already its options.

    Widening only. Narrowing would have to decide what becomes of the rows
    holding a removed option -- the database refuses a CHECK that existing
    rows break -- and a schema change should not make that decision silently.
    Repeated options collapse to the first.
    """
    column = next((c for c in table.columns if c.name == column_name), None)
    if column is None:
        raise DatastoreTableNotFoundError(
            f"Column '{column_name}' not found in table metadata"
        )
    if column.type != DatastoreDataType.ENUM:
        raise DatastoreColumnChangeRefusedError(
            f"Column '{column_name}' is {column.type.value}, not ENUM: only an "
            "ENUM column's options can be changed."
        )
    if column.computed:
        raise DatastoreColumnChangeRefusedError(
            f"Column '{column_name}' is computed: its values come from its "
            "expression, so it has no options to change."
        )

    requested = list(dict.fromkeys(options))
    current = list(column.options or [])
    missing = [option for option in current if option not in requested]
    if missing:
        named = ", ".join(f"'{option}'" for option in missing)
        raise DatastoreColumnChangeRefusedError(
            f"Options can be added to column '{column_name}' but not removed; "
            f"this would remove {named}. Rows may already hold a removed "
            "option, so send every current option along with the new ones.",
            details={"field": column_name, "missing_options": missing},
        )
    if requested == current:
        return None
    return ColumnSchema.model_validate({**column.model_dump(), "options": requested})


@dataclass(frozen=True, slots=True)
class ColumnChange:
    """What one update makes of an existing column."""

    #: The column as it is to be declared.
    column: ColumnSchema
    #: Its ENUM CHECK is to be replaced to accept ``column.options``.
    options_widened: bool
    #: Its foreign key is to be dropped.
    foreign_key_dropped: bool


def column_change(
    table: DatastoreTableEntity,
    column_name: str,
    options: Sequence[str] | None,
    drop_foreign_key: bool = False,
) -> ColumnChange | None:
    """The change ``options`` and ``drop_foreign_key`` ask of ``column_name``,
    or ``None`` when the column already is that.

    A foreign key is only ever dropped here, never added: adding one would
    have to decide what becomes of rows that already point nowhere, and a
    bundle that no longer declares one is the case that needs this.
    """
    current = next((c for c in table.columns if c.name == column_name), None)
    if current is None:
        raise DatastoreTableNotFoundError(
            f"Column '{column_name}' not found in table metadata"
        )
    widened = (
        widened_enum_column(table, column_name, options)
        if options is not None
        else None
    )
    dropped = drop_foreign_key and current.foreign_key is not None
    if widened is None and not dropped:
        return None
    column = widened or current
    if dropped:
        column = ColumnSchema.model_validate(
            {**column.model_dump(), "foreign_key": None}
        )
    return ColumnChange(
        column=column, options_widened=widened is not None, foreign_key_dropped=dropped
    )
