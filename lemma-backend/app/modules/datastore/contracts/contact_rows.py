"""What a contact's run may read of the pod's tables: its own contact's rows.

A contact holds no grant, so these do not go through the authorizer. What
stands in for it is narrower: only a table its pod marked contact-owned, only
the columns a member chose to show contacts, only rows naming the contact the
platform says is asking, read under a database policy scoped to that contact.
The caller passes the contact id it was handed by routing, never one a model
chose.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.infrastructure.contact_rows import (
    MAX_CONTACT_ROWS,
    read_contact_rows,
)
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreTable,
)
from app.modules.datastore.services.wiring import get_schema_manager

__all__ = [
    "MAX_CONTACT_ROWS",
    "ContactRowsUnavailable",
    "ContactTable",
    "contact_owned_tables",
    "rows_for_contact",
]


#: The most contact-owned tables a contact's run is told about.
MAX_LISTED_TABLES = 50


class ContactRowsUnavailable(Exception):
    """The rows could not be read: no such contact-owned table, or the read failed.

    Said the same way for both. Which of the pod's tables exist is not the
    contact's to learn.
    """


class ContactTable(BaseModel):
    """A contact-owned table, as a contact's run is told about it."""

    model_config = ConfigDict(frozen=True)

    name: str
    #: Only the columns a member chose to show contacts, and that still exist.
    columns: tuple[str, ...]
    primary_key: str = "id"


def _contact_table(row: DatastoreTable) -> ContactTable:
    present = {str(column.get("name")) for column in row.columns or []}
    return ContactTable(
        name=row.table_name,
        columns=tuple(name for name in row.contact_columns or [] if name in present),
        primary_key=row.primary_key_column,
    )


async def contact_owned_tables(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> list[ContactTable]:
    """The pod's contact-owned tables, each with the columns a contact may read."""
    rows = await uow.session.scalars(
        select(DatastoreTable)
        .where(DatastoreTable.pod_id == pod_id, DatastoreTable.contact_owned.is_(True))
        .order_by(DatastoreTable.table_name)
        .limit(MAX_LISTED_TABLES)
    )
    return [table for row in rows if (table := _contact_table(row)).columns]


async def _contact_owned(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, table_name: str
) -> ContactTable | None:
    row = await uow.session.scalar(
        select(DatastoreTable).where(
            DatastoreTable.pod_id == pod_id,
            DatastoreTable.table_name == table_name,
            DatastoreTable.contact_owned.is_(True),
        )
    )
    table = _contact_table(row) if row is not None else None
    return table if table is not None and table.columns else None


async def rows_for_contact(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    """This contact's rows of one contact-owned table, the chosen columns only.

    The table is checked on one short unit of work, closed before the rows are
    read on a datastore connection of their own. A table that is not
    contact-owned is reported as not found, the same as one that does not
    exist: which of the pod's tables exist is not the contact's to learn.
    """
    async with uow_factory() as uow:
        table = await _contact_owned(uow, pod_id=pod_id, table_name=table_name)
    if table is None:
        raise ContactRowsUnavailable(f"Table '{table_name}' not found")
    try:
        return await read_contact_rows(
            get_schema_manager(),
            pod_id,
            table.name,
            contact_id,
            columns=table.columns,
            primary_key=table.primary_key,
            limit=limit,
            offset=offset,
        )
    except DatastoreDomainError as exc:
        raise ContactRowsUnavailable(str(exc)) from exc
