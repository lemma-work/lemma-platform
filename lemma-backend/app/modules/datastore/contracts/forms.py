"""A table, as a web form sees it: the columns a form may ask for, and one insert.

A form is a door into one table. Somebody outside the pod fills it in, and a row
is added -- through the same validation, permission check and insert events as a
member adding the row by hand, because it *is* a member adding it: the one who
looks after the form. That is why there is no generated code anywhere: the only
thing a form can do is what that member could already do, narrowed to one
insert of the columns they ticked.

A submodule for the same reason as its siblings: these reach the service layer.
The services are imported where they are used, so the agent surfaces that name
these types at import time do not pay for the whole datastore to load.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.modules.datastore.domain.datastore_entities import (
    SYSTEM_COLUMNS,
    ColumnSchema,
    DatastoreDataType,
)
from app.modules.datastore.infrastructure.contact_rows import CONTACT_COLUMN

__all__ = [
    "FormColumn",
    "FormTable",
    "FormTableUnavailable",
    "form_table",
    "insert_form_row",
]

#: Column types a person can type into a form. A vector, a JSON blob, a user
#: reference or a file path is not something a stranger fills in.
_FILLABLE = frozenset(
    {
        DatastoreDataType.TEXT,
        DatastoreDataType.INTEGER,
        DatastoreDataType.FLOAT,
        DatastoreDataType.BOOLEAN,
        DatastoreDataType.DATE,
        DatastoreDataType.DATETIME,
        DatastoreDataType.ENUM,
    }
)

#: Never offered, whatever their type: the platform fills them in.
_STAMPED = SYSTEM_COLUMNS | {CONTACT_COLUMN}


class FormTableUnavailable(Exception):
    """No such table, the member cannot use it, or the row was refused.

    Carries a sentence fit for the member building the form; a visitor is told
    only that the form did not go through.
    """


@dataclass(frozen=True, slots=True)
class FormColumn:
    name: str
    type: str
    required: bool
    options: tuple[str, ...]
    description: str | None


@dataclass(frozen=True, slots=True)
class FormTable:
    name: str
    contact_owned: bool
    columns: tuple[FormColumn, ...]


def _fillable(column: ColumnSchema, primary_key: str) -> bool:
    if column.name in _STAMPED or column.auto or column.computed or column.system:
        return False
    if column.name == primary_key and column.default is not None:
        return False
    return column.type in _FILLABLE


async def form_table(uow, *, pod_id: UUID, table_name: str, ctx: Context) -> FormTable:
    """The table and the columns a form on it may ask for, as ``ctx`` sees it."""
    from app.modules.datastore.api.dependencies import build_table_service

    try:
        table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
    except DomainError as exc:
        raise FormTableUnavailable(f"There is no table called {table_name}") from exc
    return FormTable(
        name=table.table_name,
        contact_owned=bool(getattr(table, "contact_owned", False)),
        columns=tuple(
            FormColumn(
                name=column.name,
                type=column.type.value,
                required=column.required and column.default is None,
                options=tuple(column.options or ()),
                description=column.description,
            )
            for column in table.columns
            if _fillable(column, table.primary_key_column)
        ),
    )


async def insert_form_row(
    uow,
    *,
    pod_id: UUID,
    table_name: str,
    values: dict[str, object],
    user_id: UUID,
    ctx: Context,
    contact_id: UUID | None,
) -> None:
    """Add one row as ``user_id``, with the visitor's contact when the table keeps one.

    ``values`` must already be only the form's columns: this adds ``contact_id``
    itself, on a contact-owned table, and nothing else.
    """
    from app.modules.datastore.api.dependencies import (
        build_record_service,
        build_table_service,
        get_schema_manager,
    )
    from app.modules.datastore.services.table_context import TableContext

    try:
        table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
        row = dict(values)
        if getattr(table, "contact_owned", False) and contact_id is not None:
            row[CONTACT_COLUMN] = str(contact_id)
        table_ctx = TableContext.from_table_entity(
            table, get_schema_manager().get_schema_name(pod_id), events_enabled=True
        )
        await build_record_service(uow).create_record(table_ctx, row, user_id)
    except DomainError as exc:
        raise FormTableUnavailable(str(exc)) from exc
