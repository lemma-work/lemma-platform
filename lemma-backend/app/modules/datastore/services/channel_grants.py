"""A table a group's people outside the pod may read, or add rows to.

People outside the pod in a group act as nobody: Public reads only. A member
can grant one group one table instead -- the columns everybody there may read,
and the columns they may fill in a new row. Everything goes through that
member's own access, rebuilt on every call: a grant is never more than they can
do themselves, and a member who leaves the pod, or loses the table, takes it
with them.

The rules are the ones a table opened to outside rows keeps
(``domain/public_rows``): only the columns granted; the record validator says
what a value must be, and the database's own refusals are told in one
sentence that names neither the table nor the value. Reads are rows, never
SQL, filtered only by equality on the granted columns: a filter on a hidden
column would let a guest learn what it holds. A table where each member sees
only their own rows is refused -- the granter's rows are not the group's -- and
on a contact-owned table a guest reads and adds only their own.

Everybody in the group reads what a guest's run reads: whoever grants a column
grants it to the whole group.
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
from app.modules.datastore.domain.public_rows import (
    OpenTable,
    PublicAudience,
    PublicRowsClosed,
    is_fillable,
    opening_problem,
    public_column,
    public_values,
)
from app.modules.datastore.domain.row_security import CONTACT_COLUMN
from app.modules.datastore.infrastructure.models import (
    DatastoreChannelGrantModel,
    DatastoreTable,
)
from app.modules.datastore.services.public_rows import (
    OpeningRefused,
    _insert_as,
    _schemas,
)
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)

logger = get_logger(__name__)

#: The most rows one read returns.
MAX_GRANTED_ROWS = 50


@dataclass(frozen=True, slots=True)
class ChannelGrant:
    table: str
    read_columns: tuple[str, ...]
    add_columns: tuple[str, ...]
    granted_by: UUID


async def grant_table(
    uow,
    *,
    pod_id: UUID,
    channel_id: UUID,
    table_name: str,
    read_columns: list[str],
    add_columns: list[str],
    ctx: Context,
) -> ChannelGrant:
    """Let the group read these columns of the table and fill those, as the
    member asking -- who must be able to read it, and to write it to grant
    adding."""
    tables = build_table_service(uow)
    table = await tables.get_table(pod_id, table_name, ctx)
    if add_columns:
        await tables.authz.require_table_update(
            user_id=ctx.user_id,
            pod_id=pod_id,
            table_id=table.id,
            table_name=table.table_name,
            ctx=ctx,
        )
    if table.enable_rls:
        raise OpeningRefused(
            "Each member sees only their own rows in this table, so a group "
            "can't be given it"
        )
    names = {column.name for column in table.columns}
    unknown = [name for name in read_columns if name not in names]
    if unknown:
        raise OpeningRefused(f"The table has no column {unknown[0]!r}")
    if add_columns:
        problem = opening_problem(
            table.columns,
            table.primary_key_column,
            add_columns,
            audience=PublicAudience.CONTACTS,
            contact_owned=table.contact_owned,
        )
        if problem:
            raise OpeningRefused(problem)
    if not read_columns and not add_columns:
        raise OpeningRefused("Grant at least one column to read or fill")
    grant = await _grant_row(uow, table_id=table.id, channel_id=channel_id)
    if grant is None:
        grant = DatastoreChannelGrantModel(
            pod_id=pod_id,
            table_id=table.id,
            channel_id=channel_id,
            read_columns=list(read_columns),
            add_columns=list(add_columns),
            granted_by=ctx.user_id,
        )
        uow.session.add(grant)
    else:
        grant.read_columns = list(read_columns)
        grant.add_columns = list(add_columns)
        grant.granted_by = ctx.user_id
    await uow.session.flush()
    logger.info(
        "datastore.channel_grants.granted",
        pod_id=str(pod_id),
        table_id=str(table.id),
        channel_id=str(channel_id),
        read_count=len(read_columns),
        add_count=len(add_columns),
    )
    return _view(table.table_name, grant)


async def revoke_table(
    uow, *, pod_id: UUID, channel_id: UUID, table_name: str, ctx: Context
) -> None:
    """The group no longer reads or adds to the table."""
    table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
    await uow.session.execute(
        delete(DatastoreChannelGrantModel).where(
            DatastoreChannelGrantModel.table_id == table.id,
            DatastoreChannelGrantModel.channel_id == channel_id,
        )
    )


async def channel_grants(uow, *, pod_id: UUID, channel_id: UUID) -> list[ChannelGrant]:
    """What the group may read and add, table by table."""
    rows = (
        await uow.session.execute(
            select(DatastoreTable.table_name, DatastoreChannelGrantModel)
            .join(
                DatastoreChannelGrantModel,
                DatastoreChannelGrantModel.table_id == DatastoreTable.id,
            )
            .where(
                DatastoreChannelGrantModel.pod_id == pod_id,
                DatastoreChannelGrantModel.channel_id == channel_id,
            )
            .order_by(DatastoreTable.table_name)
            .limit(MAX_GRANTED_ROWS)
        )
    ).all()
    return [_view(name, grant) for name, grant in rows]


async def read_granted_rows(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    channel_id: UUID,
    table_name: str,
    equals: dict[str, object],
    contact_id: UUID | None,
    limit: int = MAX_GRANTED_ROWS,
) -> list[dict[str, object]]:
    """Rows of a granted table, the granted columns only, as its granter reads
    them. Raises :class:`PublicRowsClosed` -- naming nothing -- for a table the
    group was not given, a filter on a column it may not read, or a granter who
    can no longer read it."""
    async with uow_factory() as uow:
        found = await _granted(uow, pod_id, channel_id, table_name)
        if found is None:
            raise PublicRowsClosed(table_name)
        table, grant = found
        readable = [name for name in grant.read_columns if _has(table, name)]
        if not readable or any(name not in readable for name in equals):
            raise PublicRowsClosed(table_name)
        filters: list[tuple[str, str, object]] = [
            (name, "eq", value) for name, value in equals.items()
        ]
        if table.contact_owned:
            if contact_id is None:
                raise PublicRowsClosed(table_name)
            filters.append((CONTACT_COLUMN, "eq", str(contact_id)))
        records = await _as_granter(
            uow,
            grant.granted_by,
            pod_id,
            table_name,
            filters,
            min(limit, MAX_GRANTED_ROWS),
        )
    return [{name: record.get(name) for name in readable} for record in records]


async def add_granted_row(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    channel_id: UUID,
    table_name: str,
    answers: dict[str, object],
    contact_id: UUID | None,
) -> None:
    """Add one row a guest in the group gave, as its granter, marked as from
    outside and naming the guest. Raises like ``add_visitor_row``."""
    async with uow_factory() as uow:
        found = await _granted(uow, pod_id, channel_id, table_name)
        if found is None:
            raise PublicRowsClosed(table_name)
        table, grant = found
        by_name = {column.name: column for column in _schemas(table)}
        opened = OpenTable(
            pod_id=pod_id,
            name=table.table_name,
            audience=PublicAudience.CONTACTS,
            contact_owned=table.contact_owned,
            columns=tuple(
                public_column(by_name[name])
                for name in grant.add_columns
                if name in by_name
                and is_fillable(by_name[name], table.primary_key_column)
            ),
        )
        if not opened.columns or table.enable_rls:
            raise PublicRowsClosed(table_name)
        row = public_values(opened.columns, answers)
        if opened.contact_owned and contact_id is not None:
            row[CONTACT_COLUMN] = str(contact_id)
        actor = f"contact:{contact_id}" if contact_id else "visitor"
        await _insert_as(uow, grant.granted_by, opened, row, actor)
        await uow.commit()


def _view(table_name: str, grant: DatastoreChannelGrantModel) -> ChannelGrant:
    return ChannelGrant(
        table=table_name,
        read_columns=tuple(grant.read_columns),
        add_columns=tuple(grant.add_columns),
        granted_by=grant.granted_by,
    )


def _has(table: DatastoreTable, name: str) -> bool:
    return any(column.name == name for column in _schemas(table))


async def _grant_row(
    uow, *, table_id: UUID, channel_id: UUID
) -> DatastoreChannelGrantModel | None:
    return await uow.session.scalar(
        select(DatastoreChannelGrantModel).where(
            DatastoreChannelGrantModel.table_id == table_id,
            DatastoreChannelGrantModel.channel_id == channel_id,
        )
    )


async def _granted(
    uow, pod_id: UUID, channel_id: UUID, table_name: str
) -> tuple[DatastoreTable, DatastoreChannelGrantModel] | None:
    found = (
        await uow.session.execute(
            select(DatastoreTable, DatastoreChannelGrantModel)
            .join(
                DatastoreChannelGrantModel,
                DatastoreChannelGrantModel.table_id == DatastoreTable.id,
            )
            .where(
                DatastoreTable.pod_id == pod_id,
                DatastoreTable.table_name == table_name,
                DatastoreChannelGrantModel.channel_id == channel_id,
            )
        )
    ).first()
    return (found[0], found[1]) if found else None


async def _as_granter(
    uow,
    user_id: UUID,
    pod_id: UUID,
    table_name: str,
    filters: list[tuple[str, str, object]],
    limit: int,
) -> list[dict[str, object]]:
    """The rows the granter reads, with their access rebuilt now."""
    try:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
    except DomainError as exc:
        raise PublicRowsClosed(table_name) from exc
    token = set_current_context(ctx)
    try:
        table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
        table_ctx = TableContext.from_table_entity(
            table, get_schema_manager().get_schema_name(pod_id)
        )
        items, _total = await build_record_service(uow).list_records(
            table_ctx, user_id, limit, 0, None, filters
        )
    except DomainError as exc:
        raise PublicRowsClosed(table_name) from exc
    finally:
        reset_current_context(token)
    return [dict(item.data) for item in items]
