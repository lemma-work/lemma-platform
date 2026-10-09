"""A table open to people outside the pod: who may add rows, and what they may write.

This is what a form *is* underneath. A page -- any page: an app, a website, the
one Lemma hosts -- shows some questions; a person outside the pod answers; one
row is added. What decides whether that row may be written, and what it may
contain, is the table's own setting, never the page. So a page can look like
anything, be written by anyone or by the pod's agent, and still write no more
than the member who opened the table allowed:

- **who**: confirmed contacts only, or anyone;
- **which columns**: an allowlist, checked on every row;
- **nothing back**: adding a row reads nothing, not even the row just added --
  and a refusal the database makes (a duplicate, a missing reference) is one
  sentence that says nothing about what is already there.

The rest of the row is the platform's to fill: ``contact_id`` on a
contact-owned table, and the table's own defaults.

What a value must be is the record validator's to say, as it is for every
other write; this module picks the open columns, reads the shapes a web form
sends (a ticked box, a blank field) and says a refusal in the person's words.
Pure: no I/O.
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
from app.modules.datastore.domain.row_security import CONTACT_COLUMN

#: The most columns one table may open.
MAX_PUBLIC_COLUMNS = 30
#: The longest answer one column takes from outside.
MAX_ANSWER_CHARS = 5000

#: The one thing said when the database refuses a row: never which value
#: collided or which reference was missing, since either would tell a stranger
#: what the table already holds.
UNSAVED = "We couldn't save that answer"

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

#: The form control each type is asked with, so every page draws the same one.
_INPUT_BY_TYPE = {
    DatastoreDataType.INTEGER: "number",
    DatastoreDataType.FLOAT: "number",
    DatastoreDataType.BOOLEAN: "checkbox",
    DatastoreDataType.DATE: "date",
    DatastoreDataType.DATETIME: "datetime-local",
    DatastoreDataType.ENUM: "select",
}
_LONG_TEXT_WORDS = ("message", "note", "description", "detail", "comment")

#: Primary keys the table can fill itself without a declared default.
_SELF_KEYED_TYPES = frozenset(
    {DatastoreDataType.UUID, DatastoreDataType.USER, DatastoreDataType.SERIAL}
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
    #: The HTML control to ask with: ``text``, ``textarea``, ``email``,
    #: ``tel``, ``number``, ``date``, ``datetime-local``, ``checkbox`` or
    #: ``select``.
    input: str = "text"


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


def input_kind(column: ColumnSchema) -> str:
    """The form control a page asks for this column with."""
    by_type = _INPUT_BY_TYPE.get(column.type)
    if by_type is not None:
        return by_type
    name = column.name.lower()
    if "email" in name:
        return "email"
    if "phone" in name or "mobile" in name:
        return "tel"
    if any(word in name for word in _LONG_TEXT_WORDS):
        return "textarea"
    return "text"


def public_column(column: ColumnSchema) -> PublicColumn:
    return PublicColumn(
        name=column.name,
        type=column.type.value,
        required=needs_answer(column),
        options=tuple(column.options or ()),
        description=column.description,
        input=input_kind(column),
    )


def _fills_itself(column: ColumnSchema, primary_key: str) -> bool:
    if column.auto or column.computed or column.system:
        return True
    if column.name in SYSTEM_COLUMNS or column.default is not None:
        return True
    return column.name == primary_key and column.type in _SELF_KEYED_TYPES


def _reveals_existing(column: ColumnSchema, primary_key: str) -> bool:
    """Whether accepting or refusing this answer says what the table holds."""
    return column.unique or column.name == primary_key or column.foreign_key is not None


def opening_problem(
    columns: list[ColumnSchema],
    primary_key: str,
    chosen: list[str],
    *,
    audience: PublicAudience,
    contact_owned: bool = False,
) -> str | None:
    """Why these columns cannot be opened to ``audience``, or ``None`` when they can."""
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
    if audience is PublicAudience.ANYONE:
        telling = [
            name for name in chosen if _reveals_existing(by_name[name], primary_key)
        ]
        if telling:
            return (
                f"{', '.join(telling)} must be unique or point at another row, "
                "so only confirmed contacts can fill it in"
            )
    return _missing_problem(columns, primary_key, chosen, audience, contact_owned)


def _missing_problem(
    columns: list[ColumnSchema],
    primary_key: str,
    chosen: list[str],
    audience: PublicAudience,
    contact_owned: bool,
) -> str | None:
    """A column every row needs that nobody would fill in.

    Every such column counts, not only the ones a stranger could be asked: a
    required JSON column with no default would refuse every row just as surely.
    """
    stamped = contact_owned and audience is PublicAudience.CONTACTS
    missing = [
        column
        for column in columns
        if needs_answer(column)
        and not _fills_itself(column, primary_key)
        and column.name not in chosen
        and not (stamped and column.name == CONTACT_COLUMN)
    ]
    unaskable = [c.name for c in missing if not is_fillable(c, primary_key)]
    if unaskable:
        return (
            f"The table needs {', '.join(unaskable)}, which people outside "
            "can't fill in"
        )
    if missing:
        names = ", ".join(column.name for column in missing)
        return f"The table needs {names}, so it must be open too"
    return None


def public_values(
    columns: tuple[PublicColumn, ...], answers: dict[str, object]
) -> dict[str, object]:
    """The row a person's answers add: the open columns only, trimmed.

    Anything else they sent is ignored, never passed on. A blank optional
    answer -- a box left unticked included -- is left out so the column's own
    default applies.
    """
    row: dict[str, object] = {}
    for column in columns:
        value = _value(column, answers.get(column.name))
        if value is None:
            if column.required:
                raise refusal_for(column, "required")
            continue
        row[column.name] = value
    return row


def refusal_for(column: PublicColumn | None, reason: str | None) -> PublicRowRefused:
    """A validator's finding, said to the person who answered."""
    if column is None:
        return PublicRowRefused("Check your answers and try again")
    spoken = _spoken(column.name)
    if reason in ("required", "not_null"):
        message = f"{spoken} is required"
    elif reason == "enum":
        message = f"Choose one of the options for {spoken.lower()}"
    elif column.input == "number":
        message = f"{spoken} needs to be a number"
    elif column.input in ("date", "datetime-local"):
        message = f"{spoken} needs to be a date"
    else:
        message = f"{spoken} isn't a valid answer"
    return PublicRowRefused(message, column=column.name)


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
    return text


def _yes_no(column: PublicColumn, raw: object) -> bool | None:
    """A form's yes or no, or ``None`` for no answer at all.

    An unticked box sends nothing, and nothing is not "no": an optional
    column keeps its default, and a required one -- a box that must be
    ticked -- is refused.
    """
    if isinstance(raw, bool):
        return raw if raw or not column.required else None
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return None if column.required else False
    raise PublicRowRefused(f"{_spoken(column.name)} is a yes or no", column=column.name)
