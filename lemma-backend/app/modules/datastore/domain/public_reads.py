"""A table people outside the pod may read: which columns, ordered how.

The read side of a form. A page -- a booking page reading free slots, a price
list, a menu -- shows what one table holds, and nothing else of the pod. What it
may show is the table's own setting, never the page's:

- **who**: confirmed contacts only, or anyone;
- **which columns**: an allowlist of plain values, never a member, a file, JSON
  or the contact a row belongs to;
- **every row**, up to a cap, in the order the member chose: a table opened for
  reads holds only what is meant to be seen, because only the pod writes it.

A table that takes rows from outside never opens for reads, nor the reverse:
adding a row promises that nothing is read back. Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from app.modules.datastore.domain.datastore_entities import (
    SYSTEM_COLUMNS,
    ColumnSchema,
    DatastoreDataType,
)
from app.modules.datastore.domain.public_rows import (
    FILLABLE_TYPES,
    MAX_PUBLIC_COLUMNS,
    PublicAudience,
)
from app.modules.datastore.domain.row_security import CONTACT_COLUMN

#: The most rows one read returns. A table opened for reads is a short list a
#: page shows whole -- the free slots of the coming weeks, a menu -- not one to
#: page through.
MAX_PUBLIC_READ_ROWS = 500

#: Column types a page may show: what a person could have typed, and the ids a
#: page needs to point at a row. Never a member, a file, JSON or a vector.
READABLE_TYPES = FILLABLE_TYPES | {DatastoreDataType.UUID}

type PublicValue = str | int | float | bool | None


class PublicReadsClosed(Exception):
    """The table is not open for reads to this person, or no longer can be.

    Said the same way whether it never was, was closed, or the member who
    opened it lost the right to read it.
    """


@dataclass(frozen=True, slots=True)
class ReadColumn:
    """One column a page may show, as the page is told about it."""

    name: str
    type: str


@dataclass(frozen=True, slots=True)
class ReadableTable:
    """A table open for reads, as its page is told about it."""

    pod_id: UUID
    name: str
    audience: PublicAudience
    columns: tuple[ReadColumn, ...]
    order_by: str | None


#: The system columns a page may show: when a row was written. ``user_id``
#: names a member, so it stays in.
_READABLE_SYSTEM_COLUMNS = SYSTEM_COLUMNS - {"user_id"}


def is_readable(column: ColumnSchema) -> bool:
    """Whether a page outside the pod could be shown this column."""
    if column.name == CONTACT_COLUMN:
        return False
    if column.name in SYSTEM_COLUMNS:
        return column.name in _READABLE_SYSTEM_COLUMNS
    return column.type in READABLE_TYPES


def read_column(column: ColumnSchema) -> ReadColumn:
    return ReadColumn(name=column.name, type=column.type.value)


def reads_problem(
    columns: list[ColumnSchema], chosen: list[str], order_by: str | None
) -> str | None:
    """Why these columns cannot be opened for reads, or ``None`` when they can."""
    if not chosen:
        return "Choose at least one column people can see"
    if len(chosen) > MAX_PUBLIC_COLUMNS:
        return f"At most {MAX_PUBLIC_COLUMNS} columns can be opened"
    if len(set(chosen)) != len(chosen):
        return "Each column can be chosen once"
    by_name = {column.name: column for column in columns}
    refused = [
        name for name in chosen if name not in by_name or not is_readable(by_name[name])
    ]
    if refused:
        return f"People outside can't be shown {', '.join(refused)}"
    if order_by is not None and order_by not in chosen:
        return "Order the rows by one of the columns people can see"
    return None


def public_value(value: object) -> PublicValue:
    """A stored value as a page receives it: JSON, dates as ISO 8601."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def public_row(
    columns: tuple[ReadColumn, ...], data: dict[str, object]
) -> dict[str, PublicValue]:
    """The open columns of one row, and nothing else it holds."""
    return {column.name: public_value(data.get(column.name)) for column in columns}
