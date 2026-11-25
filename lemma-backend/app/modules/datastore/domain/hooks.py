"""A hook: an address a service outside the pod posts JSON to, one row per delivery.

Typeform, Tally, Webflow, Zapier, Make -- the services that send a form's
answers on -- mostly cannot sign what they send. So the address is the
credential: a capability URL carrying a secret the pod's member was shown once,
whose hash is all the pod keeps. Whoever holds the address may add rows to one
table, and nothing else: no read, not even of the row just added.

What a delivery becomes is the hook's to say. With no ``fields``, the whole
body goes into the table's ``payload`` column (a JSON column). With ``fields``,
each named column takes the value at a path into the body -- ``email``,
``form_response.hidden.ref``, ``answers.0.text`` -- and ``$`` is the whole body.
Either way ``received_at`` and ``hook`` are stamped when the table has them and
the hook did not map them to something else.

A delivery's row is an outside row, like a form's: it is the words of whoever
filled the form in, not the member who made the hook. A DATASTORE schedule
fires on it only if it sets ``include_outside_rows``, and its run is told the
row is untrusted.

Pure: no I/O.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from app.modules.datastore.domain.data_types import DatastoreDataType
from app.modules.datastore.domain.datastore_entities import (
    SYSTEM_COLUMNS,
    ColumnSchema,
)
from app.modules.datastore.domain.row_security import CONTACT_COLUMN

#: The ``POST /webhooks/{source}`` segment a hook's address uses.
HOOK_SOURCE = "hook"
#: The query parameters of a hook's address: which hook, and its secret.
HOOK_PARAM = "hook"
KEY_PARAM = "key"

#: Where the whole body goes when a hook maps no fields.
PAYLOAD_COLUMN = "payload"
#: Stamped on each row when the table has the column and no field maps it.
RECEIVED_AT_COLUMN = "received_at"
HOOK_COLUMN = "hook"
#: A path that means the whole body.
WHOLE_BODY = "$"

#: The most hooks one pod keeps, and the most columns one hook maps.
MAX_HOOKS_PER_POD = 50
MAX_FIELDS = 50
_MAX_PATH_CHARS = 200
_MAX_PATH_SEGMENTS = 16

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SECRET_PREFIX = "lmh_"

#: Column types a hook may not write. The member who made the hook chose the
#: mapping, but the values are whoever posted's: a member reference would let
#: a stranger name a member, a path would let them name a pod file.
_UNWRITABLE_BECAUSE = {
    DatastoreDataType.USER: "it names a member of this Lemma",
    DatastoreDataType.FILE_PATH: "it names a file in the pod",
    DatastoreDataType.VECTOR: "it holds an embedding",
}
_STAMPED_TYPES = {
    RECEIVED_AT_COLUMN: frozenset({DatastoreDataType.DATETIME, DatastoreDataType.TEXT}),
    HOOK_COLUMN: frozenset({DatastoreDataType.TEXT}),
}


class HookProblem(ValueError):
    """Why a member cannot make or change a hook, in words they can act on."""


class HookDeliveryRefused(Exception):
    """A delivery that proved its hook, refused for a reason its sender is told.

    The same reason is kept on the hook as its last error, which is where the
    member who made it finds out why a form stopped arriving.
    """

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class HookFields:
    """A hook's mapping: column name to path, or ``None`` for the whole body."""

    paths: Mapping[str, str] | None


# --- The secret ------------------------------------------------------------


def new_secret() -> str:
    """A secret for a hook's address: 256 bits, URL-safe, recognisably ours."""
    return _SECRET_PREFIX + secrets.token_urlsafe(32)


def secret_hash(secret: str) -> str:
    """What the pod keeps of a secret. A plain digest is enough: the secret is
    random and long, so there is nothing for a slow hash to protect."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_matches(secret: str, stored_hash: str) -> bool:
    """Whether ``secret`` is the one ``stored_hash`` was made from, compared in
    constant time so the comparison says nothing about how close a guess was."""
    return hmac.compare_digest(secret_hash(secret), stored_hash)


#: Compared against when no hook was found, so a missing hook takes as long to
#: refuse as a wrong secret.
UNKNOWN_HOOK_HASH = secret_hash("no such hook")


def name_problem(name: str) -> str | None:
    if _NAME.match(name or ""):
        return None
    return (
        "A hook's name is 1 to 64 lowercase letters, digits, '-' or '_', "
        "starting with a letter or digit"
    )


# --- Paths into a body -----------------------------------------------------


def path_problem(path: str) -> str | None:
    if path == WHOLE_BODY:
        return None
    if not path or len(path) > _MAX_PATH_CHARS:
        return f"A path is 1 to {_MAX_PATH_CHARS} characters"
    segments = path.split(".")
    if len(segments) > _MAX_PATH_SEGMENTS:
        return f"A path is at most {_MAX_PATH_SEGMENTS} segments deep"
    if any(not segment for segment in segments):
        return f"'{path}' has an empty segment"
    return None


def value_at(body: object, path: str) -> object | None:
    """The value at a dotted path into ``body``, or ``None`` when it has none.

    A segment of digits indexes a list; on an object it is a key like any
    other, so ``{"0": ...}`` still answers ``0``.
    """
    if path == WHOLE_BODY:
        return body
    current: object = body
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


# --- Which columns a hook may write -----------------------------------------


def _unwritable_reason(column: ColumnSchema) -> str | None:
    if column.name in SYSTEM_COLUMNS or column.name == CONTACT_COLUMN:
        return "it is filled in automatically"
    if column.auto or column.computed or column.system:
        return "it is filled in automatically"
    return _UNWRITABLE_BECAUSE.get(column.type)


def _fills_itself(column: ColumnSchema, primary_key: str) -> bool:
    if column.auto or column.computed or column.system:
        return True
    if column.name in SYSTEM_COLUMNS or column.default is not None:
        return True
    return column.name == primary_key and column.type in (
        DatastoreDataType.UUID,
        DatastoreDataType.SERIAL,
    )


def _stamped(columns: Mapping[str, ColumnSchema], fields: HookFields) -> set[str]:
    mapped = set(fields.paths or ())
    return {
        name
        for name, types in _STAMPED_TYPES.items()
        if name in columns and name not in mapped and columns[name].type in types
    }


def fields_problem(
    columns: list[ColumnSchema], primary_key: str, fields: HookFields
) -> str | None:
    """Why this mapping cannot write rows to a table with ``columns``, or ``None``."""
    by_name = {column.name: column for column in columns}
    if fields.paths is None:
        payload = by_name.get(PAYLOAD_COLUMN)
        if payload is None or payload.type is not DatastoreDataType.JSON:
            return (
                f"With no fields, the table needs a JSON column named "
                f"'{PAYLOAD_COLUMN}' to hold each delivery. Add one, or map "
                "fields to the columns it has"
            )
        written = {PAYLOAD_COLUMN}
    else:
        problem = _mapping_problem(by_name, fields.paths)
        if problem:
            return problem
        written = set(fields.paths)
    written |= _stamped(by_name, fields)
    missing = [
        column.name
        for column in columns
        if _needs_value(column, primary_key) and column.name not in written
    ]
    if missing:
        return (
            f"The table needs {', '.join(missing)} on every row, so the hook "
            "must map it"
        )
    return None


def _needs_value(column: ColumnSchema, primary_key: str) -> bool:
    """Whether a row cannot be added without this column being written."""
    return (
        column.required
        and column.default is None
        and not _fills_itself(column, primary_key)
    )


def _mapping_problem(
    by_name: Mapping[str, ColumnSchema], paths: Mapping[str, str]
) -> str | None:
    if not paths:
        return "Map at least one field, or leave fields out to keep the whole body"
    if len(paths) > MAX_FIELDS:
        return f"A hook maps at most {MAX_FIELDS} fields"
    refused: list[str] = []
    for name, path in paths.items():
        problem = path_problem(path) if isinstance(path, str) else "not a path"
        if problem:
            return f"Field '{name}': {problem}"
        column = by_name.get(name)
        reason = (
            "the table has no such column"
            if column is None
            else _unwritable_reason(column)
        )
        if reason:
            refused.append(f"{name} ({reason})")
    if refused:
        return "A hook can't write " + "; ".join(refused)
    return None


# --- A delivery's body -------------------------------------------------------


def content_type_problem(content_type: str | None) -> str | None:
    """Why a body sent as ``content_type`` is not taken, or ``None``.

    No header at all is tried as JSON: some senders leave it off.
    """
    if not content_type:
        return None
    media = content_type.split(";", 1)[0].strip().lower()
    if media == "application/json" or media.endswith("+json"):
        return None
    return (
        f"A hook takes JSON, and this was sent as {media or 'nothing'}. Send "
        "the body as JSON with Content-Type: application/json (in Zapier, set "
        "the payload type to JSON)"
    )


def parse_body(raw: bytes) -> dict[str, object]:
    """The body as a JSON object, or a refusal the sender can act on."""
    try:
        body = json.loads(raw)
    except UnicodeDecodeError, ValueError:
        raise HookDeliveryRefused(400, "The body is not valid JSON") from None
    if not isinstance(body, dict):
        raise HookDeliveryRefused(422, "Send a JSON object, not a list or a value")
    return body


def hook_row(
    columns: list[ColumnSchema],
    fields: HookFields,
    body: dict[str, object],
    *,
    hook_name: str,
    received_at: datetime,
) -> dict[str, object]:
    """The row one delivery adds.

    A path the body has nothing at is left out, so the column's own default
    applies -- and a required one is refused by the record validator, in the
    same words a form gets.
    """
    by_name = {column.name: column for column in columns}
    row: dict[str, object] = {}
    if fields.paths is None:
        row[PAYLOAD_COLUMN] = body
    else:
        for name, path in fields.paths.items():
            column = by_name.get(name)
            value = value_at(body, path)
            if column is None or value is None:
                continue
            row[name] = _as_column_value(column, value)
    stamped = _stamped(by_name, fields)
    if RECEIVED_AT_COLUMN in stamped:
        row[RECEIVED_AT_COLUMN] = received_at.isoformat()
    if HOOK_COLUMN in stamped:
        row[HOOK_COLUMN] = hook_name
    return row


def _as_column_value(column: ColumnSchema, value: object) -> object:
    if column.type is DatastoreDataType.JSON:
        return value
    if column.type in (DatastoreDataType.TEXT, DatastoreDataType.ENUM):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    return value
