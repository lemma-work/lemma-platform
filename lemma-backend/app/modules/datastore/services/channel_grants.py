"""A table a group's people outside the pod may read, or add rows to.

People outside the pod in a group act as nobody: Public reads only. A member
can grant one group one table instead -- the columns everybody there may read,
and the columns they may fill in a new row. Everything goes through that
member's own access, rebuilt on every call: a grant is never more than they can
do themselves, and a member who leaves the pod, or loses the table, takes it
with them.

Granting is a person's act, and asks what using it will: the table's settings
(as opening it to outside rows does), its rows to read, and to write them when
the group may add. Taking a grant back asks only that the caller may manage the
group: stopping a share never needs the table.

The rules are the ones a table opened to outside rows keeps
(``domain/public_rows``): only the columns granted; the record validator says
what a value must be, and the database's own refusals are told in one
sentence that names neither the table nor the value. Reads are rows, never
SQL, filtered only by equality on the granted columns: a filter on a hidden
column would let a guest learn what it holds. For the same reason no column a
refusal would give away -- unique, or pointing at another row -- is one a guest
fills. A table where each member sees only their own rows is refused -- the
granter's rows are not the group's -- now and whenever it is read; on a
contact-owned table a guest reads and adds only their own row, and reads only
the columns contacts are shown.

Everybody in the group reads what a guest's run reads: whoever grants a column
grants it to the whole group.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.datastore.domain.datastore_entities import DatastoreTableEntity
from app.modules.datastore.domain.public_rows import (
    OpenTable,
    PublicAudience,
    PublicRowRefused,
    PublicRowsClosed,
    is_fillable,
    opening_problem,
    public_column,
    public_values,
    reveals_existing,
)
from app.modules.datastore.domain.row_security import CONTACT_COLUMN
from app.modules.datastore.infrastructure.models import (
    DatastoreChannelGrantModel,
    DatastoreTable,
)
from app.modules.datastore.infrastructure.repositories.table_repository import (
    readable_by,
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
    #: A contact-owned table: each person reads and adds only their own row.
    own_rows: bool = False


@dataclass(frozen=True, slots=True)
class GrantedRows:
    rows: list[dict[str, object]]
    #: How many rows the filter matched; more than ``rows`` when it was cut off.
    total: int


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
    member asking -- who must hold what the grant will use."""
    tables = build_table_service(uow)
    table = await tables.get_table(pod_id, table_name, ctx)
    await tables.authz.require_table_update(
        user_id=ctx.user_id,
        pod_id=pod_id,
        table_id=table.id,
        table_name=table.table_name,
        ctx=ctx,
    )
    rows = TableContext.from_table_entity(
        table, get_schema_manager().get_schema_name(pod_id)
    )
    await tables.authz.require_record_read(user_id=ctx.user_id, ctx=rows)
    if add_columns:
        await tables.authz.require_record_write(user_id=ctx.user_id, ctx=rows)
    problem = _grant_problem(table, read_columns, add_columns)
    if problem:
        raise OpeningRefused(problem)
    grant = insert(DatastoreChannelGrantModel).values(
        pod_id=pod_id,
        table_id=table.id,
        channel_id=channel_id,
        read_columns=list(read_columns),
        add_columns=list(add_columns),
        granted_by=ctx.user_id,
    )
    await uow.session.execute(
        grant.on_conflict_do_update(
            index_elements=[
                DatastoreChannelGrantModel.table_id,
                DatastoreChannelGrantModel.channel_id,
            ],
            set_={
                "read_columns": grant.excluded.read_columns,
                "add_columns": grant.excluded.add_columns,
                "granted_by": grant.excluded.granted_by,
                "updated_at": func.now(),
            },
        )
    )
    logger.info(
        "datastore.channel_grants.granted",
        pod_id=str(pod_id),
        table_id=str(table.id),
        channel_id=str(channel_id),
        read_count=len(read_columns),
        add_count=len(add_columns),
    )
    return ChannelGrant(
        table=table.table_name,
        read_columns=tuple(read_columns),
        add_columns=tuple(add_columns),
        granted_by=ctx.user_id,
        own_rows=table.contact_owned,
    )


def _grant_problem(
    table: DatastoreTableEntity, read_columns: list[str], add_columns: list[str]
) -> str | None:
    """Why the group cannot be given these columns, or None."""
    if table.enable_rls:
        return (
            "Each member sees only their own rows in this table, so a group "
            "can't be given it"
        )
    if not read_columns and not add_columns:
        return "Grant at least one column to read or fill"
    by_name = {column.name: column for column in table.columns}
    unknown = [name for name in (*read_columns, *add_columns) if name not in by_name]
    if unknown:
        return f"The table has no column {unknown[0]!r}"
    if table.contact_owned:
        hidden = [name for name in read_columns if name not in table.contact_columns]
        if hidden:
            return (
                f"Contacts aren't shown {', '.join(hidden)} in this table, so "
                "a group can't read it"
            )
    if not add_columns:
        return None
    telling = [
        name
        for name in add_columns
        if reveals_existing(by_name[name], table.primary_key_column)
    ]
    if telling:
        return (
            f"{', '.join(telling)} must be unique or point at another row: a "
            "refused row would tell the group what the table holds"
        )
    return opening_problem(
        table.columns,
        table.primary_key_column,
        add_columns,
        audience=PublicAudience.CONTACTS,
        contact_owned=table.contact_owned,
    )


async def revoke_table(uow, *, pod_id: UUID, channel_id: UUID, table_name: str) -> None:
    """The group no longer reads or adds to the table."""
    table_id = (
        select(DatastoreTable.id)
        .where(DatastoreTable.pod_id == pod_id, DatastoreTable.table_name == table_name)
        .scalar_subquery()
    )
    await uow.session.execute(
        delete(DatastoreChannelGrantModel).where(
            DatastoreChannelGrantModel.pod_id == pod_id,
            DatastoreChannelGrantModel.table_id == table_id,
            DatastoreChannelGrantModel.channel_id == channel_id,
        )
    )


async def channel_grants(
    uow, *, pod_id: UUID, channel_id: UUID, readable_by_ctx: Context | None = None
) -> list[ChannelGrant]:
    """What the group may read and add, table by table. ``readable_by_ctx``
    keeps only the tables that context may read: a member looking at a group
    learns no table name they could not already see. A guest's run, told what
    it was given, passes none."""
    statement = (
        select(
            DatastoreTable.table_name,
            DatastoreTable.contact_owned,
            DatastoreChannelGrantModel,
        )
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
    if readable_by_ctx is not None:
        statement = statement.where(readable_by(readable_by_ctx))
    rows = (await uow.session.execute(statement)).all()
    return [_view(name, grant, own_rows) for name, own_rows, grant in rows]


async def read_granted_rows(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    channel_id: UUID,
    table_name: str,
    equals: dict[str, object],
    contact_id: UUID | None,
    limit: int = MAX_GRANTED_ROWS,
) -> GrantedRows:
    """Rows of a granted table, the granted columns only, as its granter reads
    them, and how many matched. Raises :class:`PublicRowsClosed` -- naming
    nothing -- for a table the group was not given, a filter on a column it may
    not read, a granter who can no longer read it, or a table made per-user
    since it was given: its rows would be the granter's own."""
    async with uow_factory() as uow:
        found = await _granted(uow, pod_id, channel_id, table_name)
        if found is None or found[0].enable_rls:
            raise PublicRowsClosed(table_name)
        table, grant = found
        readable = _readable(table, grant)
        if not readable or any(name not in readable for name in equals):
            raise PublicRowsClosed(table_name)
        filters: list[tuple[str, str, object]] = [
            (name, "eq", value) for name, value in equals.items()
        ]
        if table.contact_owned:
            if contact_id is None:
                raise PublicRowsClosed(table_name)
            filters.append((CONTACT_COLUMN, "eq", str(contact_id)))
        records, total = await _as_granter(
            uow,
            grant.granted_by,
            pod_id,
            table_name,
            filters,
            min(limit, MAX_GRANTED_ROWS),
        )
    return GrantedRows(
        rows=[{name: record.get(name) for name in readable} for record in records],
        total=max(total, len(records)),
    )


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
    outside and naming the guest. Raises like ``add_visitor_row`` -- and
    refuses the whole row, rather than dropping it, for a value of a column the
    group may not fill: a row saved without what was said is not what was
    said."""
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
                and not reveals_existing(by_name[name], table.primary_key_column)
            ),
        )
        if not opened.columns or table.enable_rls:
            raise PublicRowsClosed(table_name)
        fillable = [column.name for column in opened.columns]
        extra = sorted(name for name in answers if name not in fillable)
        if extra:
            raise PublicRowRefused(
                f"Only {', '.join(fillable)} can be filled in here, not "
                f"{', '.join(extra)}: nothing was saved"
            )
        row = public_values(opened.columns, answers)
        if opened.contact_owned and contact_id is not None:
            row[CONTACT_COLUMN] = str(contact_id)
        actor = f"contact:{contact_id}" if contact_id else "visitor"
        await _insert_as(uow, grant.granted_by, opened, row, actor)
        await uow.commit()


def _view(
    table_name: str, grant: DatastoreChannelGrantModel, own_rows: bool
) -> ChannelGrant:
    return ChannelGrant(
        table=table_name,
        read_columns=tuple(grant.read_columns),
        add_columns=tuple(grant.add_columns),
        granted_by=grant.granted_by,
        own_rows=bool(own_rows),
    )


def _readable(table: DatastoreTable, grant: DatastoreChannelGrantModel) -> list[str]:
    """The granted columns the group may still read: still in the table, and
    on a contact-owned table only those contacts are shown."""
    shown = set(table.contact_columns or []) if table.contact_owned else None
    return [
        name
        for name in grant.read_columns
        if _has(table, name) and (shown is None or name in shown)
    ]


def _has(table: DatastoreTable, name: str) -> bool:
    return any(column.name == name for column in _schemas(table))


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
) -> tuple[list[dict[str, object]], int]:
    """The rows the granter reads, with their access rebuilt now, and how many
    matched."""
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
        items, total = await build_record_service(uow).list_records(
            table_ctx, user_id, limit, 0, None, filters
        )
    except DomainError as exc:
        raise PublicRowsClosed(table_name) from exc
    finally:
        reset_current_context(token)
    return [dict(item.data) for item in items], int(total or 0)
