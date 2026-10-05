"""What a contact's run may read of the pod's tables: its own contact's rows.

A contact holds no grant, so these do not go through the authorizer. What
stands in for it is narrower: only a table its pod marked contact-owned, only
rows naming the contact the platform says is asking, read under a database
policy scoped to that contact. The caller passes the contact id it was handed
by routing, never one a model chose.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.api.dependencies import get_schema_manager
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.infrastructure.contact_rows import (
    MAX_CONTACT_ROWS,
    read_contact_rows,
)
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreTable,
)

__all__ = [
    "MAX_CONTACT_ROWS",
    "ContactRowsUnavailable",
    "ContactTable",
    "contact_owned_tables",
    "is_contact_owned",
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
    columns: tuple[str, ...]


async def contact_owned_tables(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> list[ContactTable]:
    """The pod's contact-owned tables and their column names."""
    rows = await uow.session.scalars(
        select(DatastoreTable)
        .where(DatastoreTable.pod_id == pod_id, DatastoreTable.contact_owned.is_(True))
        .order_by(DatastoreTable.table_name)
        .limit(MAX_LISTED_TABLES)
    )
    return [
        ContactTable(
            name=row.table_name,
            columns=tuple(str(column.get("name")) for column in row.columns or []),
        )
        for row in rows
    ]


async def is_contact_owned(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, table_name: str
) -> bool:
    """Whether this pod has a contact-owned table by this name."""
    return bool(
        await uow.session.scalar(
            select(DatastoreTable.contact_owned).where(
                DatastoreTable.pod_id == pod_id, DatastoreTable.table_name == table_name
            )
        )
    )


async def rows_for_contact(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, object]]:
    """This contact's rows of one contact-owned table.

    The table is checked on one short unit of work, closed before the rows are
    read on a datastore connection of their own. A table that is not
    contact-owned is reported as not found, the same as one that does not
    exist: which of the pod's tables exist is not the contact's to learn.
    """
    async with uow_factory() as uow:
        owned = await is_contact_owned(uow, pod_id=pod_id, table_name=table_name)
    if not owned:
        raise ContactRowsUnavailable(f"Table '{table_name}' not found")
    try:
        return await read_contact_rows(
            get_schema_manager(),
            pod_id,
            table_name,
            contact_id,
            limit=limit,
            offset=offset,
        )
    except DatastoreDomainError as exc:
        raise ContactRowsUnavailable(str(exc)) from exc
