"""Opening a table for reads from outside the pod, and reading it for a page.

Members open and close a table here (``open_reads`` / ``close_reads``), as
themselves: it takes what changing the table takes, and each is written to the
log as an audit line. A visitor's page reads it (``visitor_rows``) with no
session of its own -- the grant is the whole permission, and the rows are read
as the member who opened the table, through the same read check as their own
hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.public_reads import (
    MAX_PUBLIC_READ_ROWS,
    PublicReadsClosed,
    PublicValue,
    ReadableTable,
    ReadColumn,
    is_readable,
    public_row,
    read_column,
    reads_problem,
)
from app.modules.datastore.domain.public_rows import PublicAudience
from app.modules.datastore.infrastructure.models import (
    DatastorePublicReadsModel,
    DatastorePublicRowsModel,
    DatastoreTable,
)
from app.modules.datastore.services.public_rows import OpeningRefused
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadsOpening:
    """A table as the member opening it for reads sees it: what could be, and what is."""

    name: str
    per_user: bool
    contact_owned: bool
    takes_rows: bool
    offered: tuple[ReadColumn, ...]
    audience: PublicAudience | None
    columns: tuple[str, ...]
    order_by: str | None


def _schemas(row: DatastoreTable) -> list[ColumnSchema]:
    return [ColumnSchema.model_validate(column) for column in row.columns or []]


async def _grant(uow, table_id: UUID) -> DatastorePublicReadsModel | None:
    return await uow.session.scalar(
        select(DatastorePublicReadsModel).where(
            DatastorePublicReadsModel.table_id == table_id
        )
    )


async def _takes_rows(uow, table_id: UUID) -> bool:
    found = await uow.session.scalar(
        select(DatastorePublicRowsModel.id).where(
            DatastorePublicRowsModel.table_id == table_id
        )
    )
    return found is not None


async def reads_opening(
    uow, *, pod_id: UUID, table_name: str, ctx: Context
) -> ReadsOpening:
    """What a member may open of a table for reads, and what is open now."""
    table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
    grant = await _grant(uow, table.id)
    return ReadsOpening(
        name=table.table_name,
        per_user=table.enable_rls,
        contact_owned=table.contact_owned,
        takes_rows=await _takes_rows(uow, table.id),
        offered=tuple(
            read_column(column) for column in table.columns if is_readable(column)
        ),
        audience=PublicAudience(grant.audience) if grant else None,
        columns=tuple(grant.columns) if grant else (),
        order_by=grant.order_by if grant else None,
    )


async def open_reads(
    uow,
    *,
    pod_id: UUID,
    table_name: str,
    audience: PublicAudience,
    columns: list[str],
    order_by: str | None,
    ctx: Context,
) -> ReadsOpening:
    """Let people outside read a table's chosen columns, as the member asking."""
    tables = build_table_service(uow)
    table = await tables.get_table(pod_id, table_name, ctx)
    await tables.authz.require_table_update(
        user_id=ctx.user_id,
        pod_id=pod_id,
        table_id=table.id,
        table_name=table.table_name,
        ctx=ctx,
    )
    if table.enable_rls:
        raise OpeningRefused(
            "Each member sees only their own rows in this table, so people "
            "outside can't read it"
        )
    if table.contact_owned:
        raise OpeningRefused(
            "Each row of this table is one contact's, so people outside can't read it"
        )
    if await _takes_rows(uow, table.id):
        raise OpeningRefused(
            "This table takes rows from outside, and those are never read back. "
            "Close it to rows first"
        )
    problem = reads_problem(table.columns, columns, order_by)
    if problem:
        raise OpeningRefused(problem)
    grant = await _grant(uow, table.id)
    if grant is None:
        uow.session.add(
            DatastorePublicReadsModel(
                pod_id=pod_id,
                table_id=table.id,
                audience=audience.value,
                columns=list(columns),
                order_by=order_by,
                opened_by=ctx.user_id,
            )
        )
    else:
        grant.audience = audience.value
        grant.columns = list(columns)
        grant.order_by = order_by
        grant.opened_by = ctx.user_id
    await uow.session.flush()
    logger.info(
        "datastore.public_reads.opened",
        pod_id=str(pod_id),
        table_id=str(table.id),
        user_id=str(ctx.user_id),
        audience=audience.value,
        column_count=len(columns),
    )
    return await reads_opening(uow, pod_id=pod_id, table_name=table_name, ctx=ctx)


async def close_reads(uow, *, pod_id: UUID, table_name: str, ctx: Context) -> None:
    """Stop people outside reading a table."""
    tables = build_table_service(uow)
    table = await tables.get_table(pod_id, table_name, ctx)
    await tables.authz.require_table_update(
        user_id=ctx.user_id,
        pod_id=pod_id,
        table_id=table.id,
        table_name=table.table_name,
        ctx=ctx,
    )
    closed = await uow.session.execute(
        delete(DatastorePublicReadsModel).where(
            DatastorePublicReadsModel.table_id == table.id
        )
    )
    if closed.rowcount:
        logger.info(
            "datastore.public_reads.closed",
            pod_id=str(pod_id),
            table_id=str(table.id),
            user_id=str(ctx.user_id),
        )


async def _readable_row(
    uow, pod_id: UUID, table_name: str
) -> tuple[DatastoreTable, DatastorePublicReadsModel] | None:
    found = (
        await uow.session.execute(
            select(DatastoreTable, DatastorePublicReadsModel)
            .join(
                DatastorePublicReadsModel,
                DatastorePublicReadsModel.table_id == DatastoreTable.id,
            )
            .where(
                DatastoreTable.pod_id == pod_id,
                DatastoreTable.table_name == table_name,
            )
        )
    ).first()
    return (found[0], found[1]) if found else None


def _readable_view(
    pod_id: UUID, table: DatastoreTable, grant: DatastorePublicReadsModel
) -> ReadableTable | None:
    """The table as a page may read it, or ``None`` once it no longer can be.

    A column dropped or retyped since it was opened is simply not shown; a
    table that has since become per-member or contact-owned shows nothing.
    """
    if table.enable_rls or table.contact_owned:
        return None
    by_name = {column.name: column for column in _schemas(table)}
    columns = tuple(
        read_column(by_name[name])
        for name in grant.columns
        if name in by_name and is_readable(by_name[name])
    )
    if not columns:
        return None
    shown = {column.name for column in columns}
    return ReadableTable(
        pod_id=pod_id,
        name=table.table_name,
        audience=PublicAudience(grant.audience),
        columns=columns,
        order_by=grant.order_by if grant.order_by in shown else None,
    )


async def readable_table(uow, *, pod_id: UUID, table_name: str) -> ReadableTable | None:
    """What a page outside the pod may read of a table, or ``None``."""
    found = await _readable_row(uow, pod_id, table_name)
    return _readable_view(pod_id, *found) if found else None


async def visitor_rows(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID | None,
) -> tuple[ReadableTable, list[dict[str, PublicValue]]]:
    """The open columns of a readable table's rows, as the member who opened it.

    Raises :class:`PublicReadsClosed` when the table is not read by this
    person, or the member can no longer read it.
    """
    async with uow_factory() as uow:
        found = await _readable_row(uow, pod_id, table_name)
        readable = _readable_view(pod_id, *found) if found else None
        if found is None or readable is None:
            raise PublicReadsClosed(table_name)
        if readable.audience is PublicAudience.CONTACTS and contact_id is None:
            raise PublicReadsClosed(table_name)
        rows = await _read_as(uow, found[1].opened_by, readable)
    return readable, rows


async def _read_as(
    uow, user_id: UUID, readable: ReadableTable
) -> list[dict[str, PublicValue]]:
    try:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=readable.pod_id
        )
    except DomainError as exc:
        raise PublicReadsClosed(readable.name) from exc
    token = set_current_context(ctx)
    try:
        table = await build_table_service(uow).get_table(
            readable.pod_id, readable.name, ctx
        )
        table_ctx = TableContext.from_table_entity(
            table, get_schema_manager().get_schema_name(readable.pod_id)
        )
        records, _total = await build_record_service(uow).list_records(
            table_ctx,
            user_id,
            limit=MAX_PUBLIC_READ_ROWS,
            sorts=[(readable.order_by, "asc")] if readable.order_by else None,
        )
    except DomainError as exc:
        raise PublicReadsClosed(readable.name) from exc
    finally:
        reset_current_context(token)
    return [public_row(readable.columns, record.data) for record in records]
