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
from app.modules.datastore.domain.row_security import CONTACT_COLUMN
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)

#: The most rows one read returns. A page reads a short list it shows whole --
#: the free slots of the coming weeks, a menu -- not one to page through.
MAX_PUBLIC_ROWS = 500

type PublicValue = str | int | float | bool | None


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


def public_value(value: object) -> PublicValue:
    """A stored value as a page receives it: JSON, dates as ISO 8601."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
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
        raise PublicTableClosed(table_name) from exc
    if table.enable_rls:
        raise PublicTableClosed(table_name)
    columns = tuple(
        PublicColumn(name=column.name, type=column.type.value)
        for column in table.columns
        if column.name != CONTACT_COLUMN
    )
    if order_by is not None and order_by not in {c.name for c in columns}:
        raise PublicOrderRefused(order_by)
    table_ctx = TableContext.from_table_entity(
        table, get_schema_manager().get_schema_name(ctx.pod_id)
    )
    try:
        records, _total = await build_record_service(uow).list_records(
            table_ctx,
            ctx.user_id,
            limit=max(1, min(limit, MAX_PUBLIC_ROWS)),
            sorts=[(order_by, "desc" if descending else "asc")] if order_by else None,
        )
    except DomainError as exc:
        raise PublicTableClosed(table_name) from exc
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
    )


__all__ = [
    "MAX_PUBLIC_ROWS",
    "PublicColumn",
    "PublicOrderRefused",
    "PublicRows",
    "PublicTableClosed",
    "PublicValue",
    "read_public_table",
]
