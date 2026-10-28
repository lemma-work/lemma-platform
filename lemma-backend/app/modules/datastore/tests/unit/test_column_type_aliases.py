"""A column type written the way another database spells it is accepted.

Agents create tables with `TIMESTAMP` and `NUMBER` from habit. Refusing them
cost a failed call and a retry that learned nothing the error did not say.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("TIMESTAMP", DatastoreDataType.DATETIME),
        ("timestamptz", DatastoreDataType.DATETIME),
        ("timestamp  with time zone", DatastoreDataType.DATETIME),
        ("NUMBER", DatastoreDataType.FLOAT),
        ("numeric", DatastoreDataType.FLOAT),
        ("DECIMAL", DatastoreDataType.FLOAT),
        ("int", DatastoreDataType.INTEGER),
        ("varchar", DatastoreDataType.TEXT),
        ("bool", DatastoreDataType.BOOLEAN),
        ("jsonb", DatastoreDataType.JSON),
        ("datetime", DatastoreDataType.DATETIME),
        ("Text", DatastoreDataType.TEXT),
    ],
)
def test_common_spellings_map_onto_the_supported_type(written, stored):
    assert ColumnSchema(name="c", type=written).type is stored


def test_bigint_is_not_narrowed_to_a_32_bit_integer():
    with pytest.raises(ValidationError, match="Unknown column type 'BIGINT'"):
        ColumnSchema(name="c", type="BIGINT")


def test_an_unknown_type_names_every_valid_one():
    with pytest.raises(ValidationError) as caught:
        ColumnSchema(name="c", type="MONEY")
    message = str(caught.value)
    for member in DatastoreDataType:
        assert member.value in message


def test_the_published_schema_still_lists_only_the_canonical_types():
    schema = ColumnSchema.model_json_schema()
    enum = schema["$defs"]["DatastoreDataType"]["enum"]
    assert "TIMESTAMP" not in enum
    assert set(enum) == {member.value for member in DatastoreDataType}
