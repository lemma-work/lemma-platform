"""The column types a table can have, and the names they are also known by.

Agents and people write ``TIMESTAMP`` or ``NUMBER`` from habit; refusing them
cost a failed call and a retry that learned nothing. They are accepted and
stored as the type they mean. Only unambiguous ones: ``BIGINT`` is not here,
because INTEGER is 32-bit and a silent narrowing would lose data.

The CLI keeps the same alias table (`lemma_cli/cli_app/enums.py`), because the
generated SDK enum refuses an alias before any request is made.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BeforeValidator


class DatastoreDataType(str, Enum):
    """Supported data types for datastore columns."""

    TEXT = "TEXT"
    FILE_PATH = "FILE_PATH"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    JSON = "JSON"
    DATE = "DATE"
    DATETIME = "DATETIME"
    UUID = "UUID"
    USER = "USER"
    VECTOR = "VECTOR"
    SERIAL = "SERIAL"
    ENUM = "ENUM"


DATA_TYPE_ALIASES: dict[str, DatastoreDataType] = {
    "TIMESTAMP": DatastoreDataType.DATETIME,
    "TIMESTAMPTZ": DatastoreDataType.DATETIME,
    "TIMESTAMP WITH TIME ZONE": DatastoreDataType.DATETIME,
    "DATE_TIME": DatastoreDataType.DATETIME,
    "NUMBER": DatastoreDataType.FLOAT,
    "NUMERIC": DatastoreDataType.FLOAT,
    "DECIMAL": DatastoreDataType.FLOAT,
    "DOUBLE": DatastoreDataType.FLOAT,
    "DOUBLE PRECISION": DatastoreDataType.FLOAT,
    "REAL": DatastoreDataType.FLOAT,
    "INT": DatastoreDataType.INTEGER,
    "STRING": DatastoreDataType.TEXT,
    "VARCHAR": DatastoreDataType.TEXT,
    "BOOL": DatastoreDataType.BOOLEAN,
    "JSONB": DatastoreDataType.JSON,
}


def canonical_data_type(value: object) -> object:
    """A column type as written, mapped onto ours: case-insensitive, aliases
    resolved. An unknown name raises ``ValueError`` naming every valid one."""
    if not isinstance(value, str):
        return value
    text = " ".join(value.strip().upper().split())
    if text in DatastoreDataType.__members__:
        return DatastoreDataType(text)
    if text in DATA_TYPE_ALIASES:
        return DATA_TYPE_ALIASES[text]
    raise ValueError(
        f"Unknown column type {value!r}. Valid types: "
        f"{', '.join(t.value for t in DatastoreDataType)}."
    )


#: A column's type field: the enum, reached through its aliases. The published
#: schema still lists only the enum's own members.
ColumnDataType = Annotated[DatastoreDataType, BeforeValidator(canonical_data_type)]
