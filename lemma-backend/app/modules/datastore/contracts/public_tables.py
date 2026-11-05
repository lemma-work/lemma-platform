"""A Public table, read by a page on the web for somebody outside the pod.

A page holding a web widget's key reads what the pod marked Public with the
same authority the pod's chat has when it answers that visitor: the outsider
context (``core/authorization/anonymous``), which reads Public resources and
nothing else. So what a page can show and what the chat can say cannot
disagree, and marking a table Public is the whole permission -- there is no
second switch to keep in step.

A table whose rows belong to somebody is never read this way: a per-member
table's rows are each member's own. (A contact-owned table can't be Public at
all.) Everything else Public reads whole, every column but ``contact_id``.

A submodule for the same reason as ``public_reach``: it reaches the services,
and ``contracts/__init__`` is imported by anything that wants any contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.datastore.domain.row_security import CONTACT_COLUMN
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)

logger = get_logger(__name__)

#: A refusal of the read itself -- no such table, or not this person's to read
#: (401 for an anonymous reader, 403 for a contact) -- as opposed to the
#: datastore failing underneath it.
_REFUSED = frozenset({401, 403, 404})

#: The most rows one read returns. A page reads a short list it shows whole --
#: the free slots of the coming weeks, a menu -- not one to page through.
MAX_PUBLIC_ROWS = 500

type PublicValue = (
    str | int | float | bool | None | list[PublicValue] | dict[str, PublicValue]
)


class PublicTableClosed(Exception):
    """Not a table this person may read: missing, not Public, or per-member.

    Said the same way for each, since which tables exist is not the visitor's
    to learn.
    """


class PublicOrderRefused(ValueError):
    """The rows were asked for in the order of a column the table doesn't have."""


@dataclass(frozen=True, slots=True)
class PublicColumn:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class PublicRows:
    table: str
    columns: tuple[PublicColumn, ...]
    rows: list[dict[str, PublicValue]]
    #: More rows than :data:`MAX_PUBLIC_ROWS` matched, so ``rows`` is the
    #: first of them, not all.
    truncated: bool


def public_value(value: object) -> PublicValue:
    """A stored value as a page receives it: JSON, dates as ISO 8601.

    A JSON column arrives as the lists and objects it holds, never as a repr.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(key): public_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [public_value(item) for item in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


async def read_public_table(
    uow,
    *,
    ctx: Context,
    table_name: str,
    order_by: str | None = None,
    descending: bool = False,
    limit: int = MAX_PUBLIC_ROWS,
) -> PublicRows:
    """Every row of a Public table, as ``ctx`` -- an outsider's context -- reads it.

    Raises :class:`PublicTableClosed` for anything ``ctx`` may not read, and
    :class:`PublicOrderRefused` for an order by a column the table lacks.
    """
    if ctx.pod_id is None:
        raise PublicTableClosed(table_name)
    try:
        table = await build_table_service(uow).get_table(ctx.pod_id, table_name, ctx)
    except DomainError as exc:
        raise _closed_or_failed(exc, ctx, table_name) from exc
    if table.enable_rls:
        raise PublicTableClosed(table_name)
    columns = tuple(
        PublicColumn(name=column.name, type=column.type.value)
        for column in table.columns
        if column.name != CONTACT_COLUMN
    )
    # The name sorted by is the table's own, looked up by what the page asked
    # for: the page's string never reaches the query.
    order_column = None
    if order_by is not None:
        order_column = next((c.name for c in columns if c.name == order_by), None)
        if order_column is None:
            raise PublicOrderRefused(order_by)
    table_ctx = TableContext.from_table_entity(
        table, get_schema_manager().get_schema_name(ctx.pod_id)
    )
    try:
        records, total = await build_record_service(uow).list_records(
            table_ctx,
            ctx.user_id,
            limit=max(1, min(limit, MAX_PUBLIC_ROWS)),
            sorts=[(order_column, "desc" if descending else "asc")]
            if order_column
            else None,
        )
    except DomainError as exc:
        raise _closed_or_failed(exc, ctx, table_name) from exc
    return PublicRows(
        table=table.table_name,
        columns=columns,
        rows=[
            {
                column.name: public_value(record.data.get(column.name))
                for column in columns
            }
            for record in records
        ],
        truncated=total > len(records),
    )


def _closed_or_failed(
    exc: DomainError, ctx: Context, table_name: str
) -> PublicTableClosed | DomainError:
    """A refusal, said like every other; anything else, kept and logged.

    Only "no such table" and "not yours to read" become the one answer a
    stranger gets. A datastore that failed underneath stays a failure, so an
    outage never reads as a table that isn't Public.
    """
    if exc.status_code in _REFUSED:
        return PublicTableClosed(table_name)
    logger.warning(
        "datastore.public_tables.read_failed.degraded",
        pod_id=str(ctx.pod_id),
        error_type=type(exc).__name__,
    )
    return exc


__all__ = [
    "MAX_PUBLIC_ROWS",
    "PublicColumn",
    "PublicOrderRefused",
    "PublicRows",
    "PublicTableClosed",
    "PublicValue",
    "read_public_table",
]
