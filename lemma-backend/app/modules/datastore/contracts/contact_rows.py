"""What a contact's run may read of the pod's tables: its own contact's rows.

A contact holds no grant, so these do not go through the authorizer. What
stands in for it is narrower: only a table its pod marked contact-owned, only
rows naming the contact the platform says is asking, read under a database
policy scoped to that contact. The caller passes the contact id it was handed
by routing, never one a model chose.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.api.dependencies import get_schema_manager
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.domain.ports import DatastoreSchemaPort
from app.modules.datastore.infrastructure.contact_rows import (
    MAX_CONTACT_ROWS,
    delete_rows_of_contact,
    read_contact_rows,
    read_rows_of_contact_after,
)
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreTable,
)

__all__ = [
    "MAX_CONTACT_ROWS",
    "ContactRow",
    "ContactRowsCursor",
    "ContactRowsPage",
    "ContactRowsUnavailable",
    "ContactTable",
    "contact_owned_tables",
    "delete_contact_rows",
    "export_contact_rows",
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


class ContactRow(BaseModel):
    """One row the pod keeps about a contact, for a request to see their data."""

    model_config = ConfigDict(frozen=True)

    table: str
    values: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ContactRowsCursor:
    """The last row an export page carried: its table, and its key as text."""

    table: str
    key: str


@dataclass(frozen=True, slots=True)
class ContactRowsPage:
    rows: tuple[ContactRow, ...]
    #: Where the next page starts, or ``None`` when every table is done.
    next_after: ContactRowsCursor | None


async def _owned_tables(
    uow_factory: UnitOfWorkFactory, pod_id: UUID
) -> list[tuple[str, str]]:
    """Every contact-owned table of the pod and its key column, by name.

    Not capped like ``contact_owned_tables``: forgetting or exporting a
    contact must reach every table that holds them, not the first fifty.
    """
    async with uow_factory() as uow:
        rows = await uow.session.execute(
            select(DatastoreTable.table_name, DatastoreTable.primary_key_column)
            .where(
                DatastoreTable.pod_id == pod_id,
                DatastoreTable.contact_owned.is_(True),
            )
            .order_by(DatastoreTable.table_name)
        )
        return [(name, key or "id") for name, key in rows]


async def delete_contact_rows(
    uow_factory: UnitOfWorkFactory, *, pod_id: UUID, contact_id: UUID
) -> int:
    """Delete this contact's rows from every contact-owned table of the pod.

    Table by table, each in its own pod-database transaction: there is no one
    transaction across the pod's tables. A failure part-way raises, and the
    caller forgets nothing else, so asking again finishes the job.
    """
    schema = get_schema_manager()
    deleted = 0
    for table_name, _key in await _owned_tables(uow_factory, pod_id):
        deleted += await delete_rows_of_contact(schema, pod_id, table_name, contact_id)
    return deleted


async def export_contact_rows(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    contact_id: UUID,
    after: ContactRowsCursor | None = None,
    limit: int = MAX_CONTACT_ROWS,
) -> ContactRowsPage:
    """A page of this contact's rows across the pod's contact-owned tables.

    Tables in name order, rows in key order within each, read as the contact
    so the row policy holds the read to them.
    """
    schema = get_schema_manager()
    budget = max(1, min(limit, MAX_CONTACT_ROWS))
    page: list[ContactRow] = []
    last: ContactRowsCursor | None = None
    for table_name, key in await _owned_tables(uow_factory, pod_id):
        if after is not None and table_name < after.table:
            continue
        resume = after.key if after is not None and table_name == after.table else None
        rows = await _read_after(
            schema, pod_id, table_name, contact_id, key, resume, budget - len(page)
        )
        for row in rows:
            page.append(ContactRow(table=table_name, values=to_jsonable_python(row)))
            last = ContactRowsCursor(table=table_name, key=str(row.get(key)))
        if len(page) >= budget:
            return ContactRowsPage(rows=tuple(page), next_after=last)
    return ContactRowsPage(rows=tuple(page), next_after=None)


async def _read_after(
    schema: DatastoreSchemaPort,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID,
    key: str,
    after_key: str | None,
    limit: int,
) -> list[dict[str, object]]:
    read = partial(
        read_rows_of_contact_after,
        schema,
        pod_id,
        table_name,
        contact_id,
        key_column=key,
        after_key=after_key,
        limit=limit,
    )
    try:
        return await read()
    except DBAPIError:
        if not await schema.heal_query_role_access(schema.get_schema_name(pod_id)):
            raise
    return await read()
