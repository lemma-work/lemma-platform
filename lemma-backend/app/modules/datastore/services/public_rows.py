"""Opening a table to people outside the pod, and adding their rows.

Members open and close a table here (``open_table`` / ``close_table``), as
themselves: it takes what changing the table takes, and each is written to the
log as an audit line. A visitor's page asks what it may fill
(``visitor_table``) and adds a row (``add_visitor_row``) with no session of its
own -- the grant is the whole permission, and the row is added as the member
who opened the table, through the same validation and permission check as their
own hand. Its insert event is not theirs, though: it names the outsider, so a
schedule watching the table can tell a stranger's words from a member's.
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
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreConflictError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.public_rows import (
    UNSAVED,
    OpenTable,
    PublicAudience,
    PublicColumn,
    PublicRowRefused,
    PublicRowsClosed,
    is_fillable,
    opening_problem,
    public_column,
    public_values,
    refusal_for,
)
from app.modules.datastore.domain.row_security import CONTACT_COLUMN
from app.modules.datastore.infrastructure.models import (
    DatastorePublicRowsModel,
    DatastoreTable,
)
from app.modules.datastore.infrastructure.repositories.table_repository import (
    readable_by,
)
from app.modules.datastore.services.record_validator import (
    RecordValidator,
    convert_record,
)
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)

logger = get_logger(__name__)

#: The most open tables one pod lists.
MAX_OPEN_TABLES = 100


class OpeningRefused(ValueError):
    """Why a member cannot open this table, in words they can act on."""


@dataclass(frozen=True, slots=True)
class TableOpening:
    """A table as the member opening it sees it: what could be, and what is."""

    name: str
    per_user: bool
    contact_owned: bool
    offered: tuple[PublicColumn, ...]
    audience: PublicAudience | None
    columns: tuple[str, ...]


def _schemas(row: DatastoreTable) -> list[ColumnSchema]:
    return [ColumnSchema.model_validate(column) for column in row.columns or []]


async def _grant(uow, table_id: UUID) -> DatastorePublicRowsModel | None:
    return await uow.session.scalar(
        select(DatastorePublicRowsModel).where(
            DatastorePublicRowsModel.table_id == table_id
        )
    )


async def table_opening(
    uow, *, pod_id: UUID, table_name: str, ctx: Context
) -> TableOpening:
    """What a member may open of a table, and what is open now."""
    table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
    grant = await _grant(uow, table.id)
    return TableOpening(
        name=table.table_name,
        per_user=table.enable_rls,
        contact_owned=table.contact_owned,
        offered=tuple(
            public_column(column)
            for column in table.columns
            if is_fillable(column, table.primary_key_column)
        ),
        audience=PublicAudience(grant.audience) if grant else None,
        columns=tuple(grant.columns) if grant else (),
    )


async def open_table(
    uow,
    *,
    pod_id: UUID,
    table_name: str,
    audience: PublicAudience,
    columns: list[str],
    ctx: Context,
) -> TableOpening:
    """Let people outside add rows to a table, as the member asking."""
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
            "Each member sees only their own rows in this table, so it can't "
            "take rows from outside"
        )
    problem = opening_problem(
        table.columns,
        table.primary_key_column,
        columns,
        audience=audience,
        contact_owned=table.contact_owned,
    )
    if problem:
        raise OpeningRefused(problem)
    grant = await _grant(uow, table.id)
    if grant is None:
        uow.session.add(
            DatastorePublicRowsModel(
                pod_id=pod_id,
                table_id=table.id,
                audience=audience.value,
                columns=list(columns),
                opened_by=ctx.user_id,
            )
        )
    else:
        grant.audience = audience.value
        grant.columns = list(columns)
        grant.opened_by = ctx.user_id
    await uow.session.flush()
    logger.info(
        "datastore.public_rows.opened",
        pod_id=str(pod_id),
        table_id=str(table.id),
        user_id=str(ctx.user_id),
        audience=audience.value,
        column_count=len(columns),
    )
    return await table_opening(uow, pod_id=pod_id, table_name=table_name, ctx=ctx)


async def close_table(uow, *, pod_id: UUID, table_name: str, ctx: Context) -> None:
    """Stop a table taking rows from outside."""
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
        delete(DatastorePublicRowsModel).where(
            DatastorePublicRowsModel.table_id == table.id
        )
    )
    if closed.rowcount:
        logger.info(
            "datastore.public_rows.closed",
            pod_id=str(pod_id),
            table_id=str(table.id),
            user_id=str(ctx.user_id),
        )


async def open_tables(
    uow, *, pod_id: UUID, readable_by_ctx: Context | None = None
) -> list[tuple[str, PublicAudience]]:
    """Every table of the pod that takes rows from outside, and from whom.

    ``readable_by_ctx`` keeps only the tables that context may read: a member
    listing them learns no table name they could not already see. A visitor's
    run, which is told only which forms it may help fill, passes none.
    """
    statement = (
        select(DatastoreTable.table_name, DatastorePublicRowsModel.audience)
        .join(DatastoreTable, DatastoreTable.id == DatastorePublicRowsModel.table_id)
        .where(DatastorePublicRowsModel.pod_id == pod_id)
        .order_by(DatastoreTable.table_name)
        .limit(MAX_OPEN_TABLES)
    )
    if readable_by_ctx is not None:
        statement = statement.where(readable_by(readable_by_ctx))
    rows = await uow.session.execute(statement)
    return [(name, PublicAudience(audience)) for name, audience in rows]


async def _open_row(
    uow, pod_id: UUID, table_name: str
) -> tuple[DatastoreTable, DatastorePublicRowsModel] | None:
    found = (
        await uow.session.execute(
            select(DatastoreTable, DatastorePublicRowsModel)
            .join(
                DatastorePublicRowsModel,
                DatastorePublicRowsModel.table_id == DatastoreTable.id,
            )
            .where(
                DatastoreTable.pod_id == pod_id,
                DatastoreTable.table_name == table_name,
            )
        )
    ).first()
    return (found[0], found[1]) if found else None


async def visitor_table(uow, *, pod_id: UUID, table_name: str) -> OpenTable | None:
    """What a page outside the pod may fill of a table, or ``None``.

    Only the open columns, in the member's order, and only those the table
    still has: a column dropped since it was opened is simply not asked.
    """
    found = await _open_row(uow, pod_id, table_name)
    return _visitor_view(pod_id, *found) if found else None


def _visitor_view(
    pod_id: UUID, table: DatastoreTable, grant: DatastorePublicRowsModel
) -> OpenTable | None:
    if table.enable_rls:
        return None
    by_name = {column.name: column for column in _schemas(table)}
    return OpenTable(
        pod_id=pod_id,
        name=table.table_name,
        audience=PublicAudience(grant.audience),
        contact_owned=table.contact_owned,
        columns=tuple(
            public_column(by_name[name])
            for name in grant.columns
            if name in by_name and is_fillable(by_name[name], table.primary_key_column)
        ),
    )


async def add_visitor_row(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    table_name: str,
    answers: dict[str, object],
    contact_id: UUID | None,
    actor: str | None = None,
) -> None:
    """Add one row from outside, as the member who opened the table.

    ``actor`` names the outsider for the row's insert event --
    ``visitor:{session}`` or ``contact:{id}``; a contact is named by default.

    Raises :class:`PublicRowRefused` for answers that do not fit -- before
    anything is written -- and for any row the database refuses, in one
    sentence that names neither the table nor the value. Raises
    :class:`PublicRowsClosed` when the table does not take rows from this
    person, or the member can no longer write it.
    """
    outside_actor = actor or (f"contact:{contact_id}" if contact_id else "visitor")
    async with uow_factory() as uow:
        found = await _open_row(uow, pod_id, table_name)
        opened = _visitor_view(pod_id, *found) if found else None
        if found is None or opened is None:
            raise PublicRowsClosed(table_name)
        if opened.audience is PublicAudience.CONTACTS and contact_id is None:
            raise PublicRowsClosed(table_name)
        row = public_values(opened.columns, answers)
        if opened.contact_owned and contact_id is not None:
            row[CONTACT_COLUMN] = str(contact_id)
        await _insert_as(uow, found[1].opened_by, opened, row, outside_actor)
        await uow.commit()


async def _insert_as(
    uow,
    user_id: UUID,
    opened: OpenTable,
    row: dict[str, object],
    outside_actor: str,
) -> None:
    try:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=opened.pod_id
        )
    except DomainError as exc:
        raise PublicRowsClosed(opened.name) from exc
    token = set_current_context(ctx)
    try:
        table = await build_table_service(uow).get_table(
            opened.pod_id, opened.name, ctx
        )
        table_ctx = TableContext.from_table_entity(
            table,
            get_schema_manager().get_schema_name(opened.pod_id),
            events_enabled=True,
            outside_actor=outside_actor,
        )
        _check(table_ctx, opened, row)
        await build_record_service(uow).create_record(table_ctx, row, user_id)
    except DatastoreAccessDeniedError as exc:
        raise PublicRowsClosed(opened.name) from exc
    except (DatastoreValidationError, DatastoreConflictError) as exc:
        # Past ``_check``, only the database refuses: a duplicate, a missing
        # reference. Saying which would tell a stranger what the table holds.
        raise PublicRowRefused(UNSAVED) from exc
    except DomainError as exc:
        raise PublicRowsClosed(opened.name) from exc
    finally:
        reset_current_context(token)


def _check(table_ctx: TableContext, opened: OpenTable, row: dict[str, object]) -> None:
    """Ask the record validator about the row, and say what it finds in words.

    The validator is the authority on what a value must be, here as on every
    other write; it is asked first, apart from the insert, so that what it
    refuses can be told to the person and what the database refuses cannot.
    """
    by_name = {column.name: column for column in opened.columns}
    for name, value in row.items():
        try:
            convert_record(table_ctx.columns, {name: value})
        except DatastoreValidationError as exc:
            raise refusal_for(by_name.get(name), "type") from exc
    valid, _errors, details = RecordValidator(table_ctx).validate(row, is_creation=True)
    if not valid:
        first = details[0] if details else {}
        raise refusal_for(by_name.get(str(first.get("field"))), first.get("reason"))
