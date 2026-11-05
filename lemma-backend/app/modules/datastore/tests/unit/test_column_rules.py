"""What a column will hold, said before the database has to say it.

A `TEXT` column with a `max_length` is `VARCHAR(n)`, and the database refuses a
longer value with an error that names no column. And a column called `check` or
`Order` works for every write the platform makes -- it quotes its names -- and
then breaks every query anyone writes by hand.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.datastore.domain.column_description import (
    column_name_problem,
    describe_column,
    needs_quoting,
)
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.record_validator import RecordValidator
from app.modules.datastore.services.table_context import TableContext

pytestmark = pytest.mark.unit


def _validator() -> RecordValidator:
    return RecordValidator(
        TableContext(
            pod_id=uuid4(),
            table_id=uuid4(),
            table_name="notes",
            schema_name="pod_test",
            columns=[
                ColumnSchema(name="title", type=DatastoreDataType.TEXT, max_length=10),
            ],
            primary_key_column="id",
            enable_rls=False,
        )
    )


def test_a_value_longer_than_its_column_is_refused_by_name():
    is_valid, errors, details = _validator().validate(
        {"title": "x" * 11}, is_creation=False
    )

    assert not is_valid
    assert any("'title'" in error and "at most 10" in error for error in errors)
    assert {
        "field": "title",
        "reason": "max_length",
        "max_length": 10,
        "length": 11,
    } in details


def test_an_update_longer_than_its_column_is_refused_by_name():
    with pytest.raises(DatastoreValidationError, match="'title'"):
        _validator().validate_update({"title": "x" * 11})


def test_a_value_that_fits_is_accepted():
    is_valid, errors, _ = _validator().validate({"title": "x" * 10})

    assert is_valid, errors


@pytest.mark.parametrize("name", ["check", "order", "user", "Group", "Order", "1st"])
def test_a_name_hand_written_sql_cannot_read_is_flagged(name):
    assert needs_quoting(name)
    assert column_name_problem(name) is not None


@pytest.mark.parametrize("name", ["status", "order_id", "check_number", "amount"])
def test_an_ordinary_name_is_left_alone(name):
    assert not needs_quoting(name)
    assert column_name_problem(name) is None


def test_the_describe_carries_everything_a_write_depends_on():
    described = describe_column(
        ColumnSchema(
            name="check",
            type=DatastoreDataType.TEXT,
            max_length=500,
            required=True,
            unique=True,
            default="none",
        )
    ).as_json()

    assert described["sql_type"] == "VARCHAR(500)"
    assert described["max_length"] == 500
    assert described["default"] == "none"
    assert described["unique"] is True
    assert described["quote_required"] is True
