"""A table open to people outside the pod: who may add rows, and what they may write.

This is what a form *is* underneath. A page -- any page: an app, a website, the
one Lemma hosts -- shows some questions; a person outside the pod answers; one
row is added. What decides whether that row may be written, and what it may
contain, is the table's own setting, never the page. So a page can look like
anything, be written by anyone or by the pod's agent, and still write no more
than the member who opened the table allowed:

- **who**: confirmed contacts only, or anyone;
- **which columns**: an allowlist, checked on every row;
- **nothing back**: adding a row reads nothing, not even the row just added.

The rest of the row is the platform's to fill: ``contact_id`` on a
contact-owned table, and the table's own defaults. Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.datastore.domain.datastore_entities import (
    SYSTEM_COLUMNS,
    ColumnSchema,
    DatastoreDataType,
)

#: The column a contact-owned table names its contact in. Stamped, never asked.
CONTACT_COLUMN = "contact_id"

#: The most columns one table may open.
MAX_PUBLIC_COLUMNS = 30
#: The longest answer one column takes from outside.
MAX_ANSWER_CHARS = 5000

#: Column types a person outside the pod can be asked to fill. A vector, a JSON
#: blob, a user reference or a file path is not something a stranger types.
FILLABLE_TYPES = frozenset(
    {
        DatastoreDataType.TEXT,
        DatastoreDataType.INTEGER,
        DatastoreDataType.FLOAT,
        DatastoreDataType.BOOLEAN,
        DatastoreDataType.DATE,
        DatastoreDataType.DATETIME,
        DatastoreDataType.ENUM,
    }
)

_TRUE = frozenset({"true", "on", "yes", "1"})
_FALSE = frozenset({"false", "off", "no", "0"})


class PublicAudience(StrEnum):
    """Who outside the pod may add rows."""

    #: Only somebody the pod knows: a contact, confirmed by a code, a host
    #: token or the channel they wrote from.
    CONTACTS = "contacts"
    #: Anybody with the page. A confirmed visitor's row still names them.
    ANYONE = "anyone"


class PublicColumn(BaseModel):
    """One column a page may ask for, as the page is told about it."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    required: bool
    options: tuple[str, ...] = ()
    description: str | None = None


class PublicRowsClosed(Exception):
    """The table does not take rows from this person, or no longer can.

    Said the same way whether it never did, was closed, or the member who
    opened it lost the right to write it: which tables exist is not the
    visitor's to learn.
    """


@dataclass(frozen=True, slots=True)
class OpenTable:
    """A table that takes rows from outside, as its page is told about it."""

    pod_id: UUID
    name: str
    audience: PublicAudience
    contact_owned: bool
    columns: tuple[PublicColumn, ...]


class PublicRowRefused(ValueError):
    """An answer that does not fit, said so the person can fix it."""

    def __init__(self, message: str, *, column: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.column = column


def is_fillable(column: ColumnSchema, primary_key: str) -> bool:
    """Whether a person outside the pod could be asked for this column."""
    if column.name in SYSTEM_COLUMNS or column.name == CONTACT_COLUMN:
        return False
    if column.auto or column.computed or column.system:
        return False
    if column.name == primary_key and column.default is not None:
        return False
    return column.type in FILLABLE_TYPES


def needs_answer(column: ColumnSchema) -> bool:
    """Whether a row cannot be added without this column."""
    return column.required and column.default is None


def public_column(column: ColumnSchema) -> PublicColumn:
    return PublicColumn(
        name=column.name,
        type=column.type.value,
        required=needs_answer(column),
        options=tuple(column.options or ()),
        description=column.description,
    )


def opening_problem(
    columns: list[ColumnSchema], primary_key: str, chosen: list[str]
) -> str | None:
    """Why these columns cannot be opened, or ``None`` when they can."""
    if not chosen:
        return "Choose at least one column people can fill in"
    if len(chosen) > MAX_PUBLIC_COLUMNS:
        return f"At most {MAX_PUBLIC_COLUMNS} columns can be opened"
    if len(set(chosen)) != len(chosen):
        return "Each column can be chosen once"
    by_name = {column.name: column for column in columns}
    refused = [
        name
        for name in chosen
        if name not in by_name or not is_fillable(by_name[name], primary_key)
    ]
    if refused:
        return f"People outside can't fill in {', '.join(refused)}"
    missing = [
        column.name
        for column in columns
        if is_fillable(column, primary_key)
        and needs_answer(column)
        and column.name not in chosen
    ]
    if missing:
        return f"The table needs {', '.join(missing)}, so it must be open too"
    return None


def public_values(
    columns: tuple[PublicColumn, ...], answers: dict[str, object]
) -> dict[str, object]:
    """The row a person's answers add: the open columns only, each checked.

    Anything else they sent is ignored, never passed on. A blank optional
    answer is left out so the column's own default applies.
    """
    row: dict[str, object] = {}
    for column in columns:
        value = _value(column, answers.get(column.name))
        if value is None:
            if column.required:
                raise PublicRowRefused(
                    f"{_spoken(column.name)} is required", column=column.name
                )
            continue
        row[column.name] = value
    return row


def _spoken(name: str) -> str:
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def _value(column: PublicColumn, raw: object) -> object | None:
    if column.type == DatastoreDataType.BOOLEAN.value:
        return _yes_no(column, raw)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > MAX_ANSWER_CHARS:
        raise PublicRowRefused(
            f"{_spoken(column.name)} is too long", column=column.name
        )
    if column.type in (DatastoreDataType.INTEGER.value, DatastoreDataType.FLOAT.value):
        return _number(column, text)
    if column.type == DatastoreDataType.ENUM.value and text not in column.options:
        raise PublicRowRefused(
            f"Choose one of the options for {_spoken(column.name).lower()}",
            column=column.name,
        )
    return text


def _yes_no(column: PublicColumn, raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw if raw or not column.required else None
    text = str(raw or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE or not text:
        return None if column.required else False
    raise PublicRowRefused(f"{_spoken(column.name)} is a yes or no", column=column.name)


def _number(column: PublicColumn, text: str) -> int | float:
    try:
        if column.type == DatastoreDataType.INTEGER.value:
            return int(text)
        return float(text)
    except ValueError as exc:
        raise PublicRowRefused(
            f"{_spoken(column.name)} needs to be a number", column=column.name
        ) from exc
