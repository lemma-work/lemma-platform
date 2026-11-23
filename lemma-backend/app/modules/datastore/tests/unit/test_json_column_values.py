"""What a JSON column binds and reads back, for every JSON value.

The driver binds a JSONB parameter as JSON text, so a value has to arrive
encoded. Only objects and arrays were: a number or a boolean failed in the
driver, and a plain string reached the database as malformed JSON text.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)
from app.modules.datastore.services.value_converter import ValueConverter

_JSON = ColumnSchema(name="payload", type=DatastoreDataType.JSON)


@pytest.mark.parametrize(
    "value",
    [
        42,
        3.5,
        True,
        False,
        "hello",
        "42",
        "true",
        "null",
        "",
        '"quoted"',
        "{not json",
        {"vendor": "Acme", "retry": False},
        [1, "two", None],
        {},
        [],
    ],
)
def test_every_json_value_is_bound_as_json_text_that_decodes_to_itself(
    value: object,
) -> None:
    bound = ValueConverter.serialize_for_sql(value, _JSON)

    assert isinstance(bound, str)
    assert json.loads(bound) == value


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ('{"vendor": "Acme"}', {"vendor": "Acme"}),
        ("  [1, 2]  ", [1, 2]),
        ("[]", []),
    ],
)
def test_a_string_holding_an_object_or_array_is_still_taken_as_json_text(
    sent: str, stored: object
) -> None:
    """The column's long-standing reading of a serialized payload is kept."""
    assert json.loads(ValueConverter.serialize_for_sql(sent, _JSON)) == stored


def test_sql_null_stays_sql_null() -> None:
    assert ValueConverter.serialize_for_sql(None, _JSON) is None


def test_a_uuid_inside_a_payload_is_written_as_its_text() -> None:
    reference = UUID("4d06f8d1-0000-4000-8000-000000000001")

    bound = ValueConverter.serialize_for_sql({"ref": reference}, _JSON)

    assert json.loads(bound) == {"ref": str(reference)}


@pytest.mark.parametrize("stored", ["42", "true", "null", '{"a": 1}', "hello"])
def test_a_stored_json_string_reads_back_as_that_string(stored: str) -> None:
    """The driver already decoded the column; a string is not decoded twice."""
    assert ValueConverter.convert_output_value(stored, _JSON) == stored


def test_other_columns_bind_their_value_unchanged() -> None:
    text = ColumnSchema(name="title", type=DatastoreDataType.TEXT)

    assert ValueConverter.serialize_for_sql("hello", text) == "hello"
