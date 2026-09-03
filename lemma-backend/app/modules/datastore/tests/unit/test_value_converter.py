"""What a DATETIME column actually stores.

The column is ``TIMESTAMP WITH TIME ZONE``, so a value carrying no zone is not
an instant: PostgreSQL resolves it against the session's ``TimeZone``, which
nothing in this module used to pin. The same string therefore meant different
moments on two deployments, filter comparisons inherited the ambiguity, and the
value read back was not the value sent -- with nothing to notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)
from app.modules.datastore.services.value_converter import ValueConverter


def _datetime_column() -> ColumnSchema:
    return ColumnSchema(name="due_at", type=DatastoreDataType.DATETIME)


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01 09:00:00",
        "2026-01-01 09:00:00.500000",
        "2026-01-01T09:00:00",
        "2026-01-01T09:00:00.500000",
    ],
)
def test_a_zoneless_string_is_read_as_utc(value: str) -> None:
    parsed = ValueConverter.parse_datetime(value)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_an_offset_the_caller_gave_is_kept() -> None:
    """Only the missing zone is supplied; a stated one is the caller's answer."""
    parsed = ValueConverter.parse_datetime("2026-01-01T09:00:00+05:30")

    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


def test_a_trailing_z_still_means_utc() -> None:
    assert ValueConverter.parse_datetime("2026-01-01T09:00:00Z").utcoffset() == (
        timedelta(0)
    )


def test_a_naive_datetime_object_is_pinned_too() -> None:
    """A caller can hand over a `datetime` rather than a string -- the SDKs do
    -- and that route bypassed the parser entirely."""
    converted = ValueConverter.convert_value(
        datetime(2026, 1, 1, 9, 0), _datetime_column()
    )

    assert converted.tzinfo is not None
    assert converted.utcoffset() == timedelta(0)


def test_an_aware_datetime_object_is_untouched() -> None:
    aware = datetime(2026, 1, 1, 9, 0, tzinfo=timezone(timedelta(hours=-5)))

    assert ValueConverter.convert_value(aware, _datetime_column()) == aware


def test_an_unparseable_value_is_still_rejected() -> None:
    with pytest.raises(ValueError):
        ValueConverter.parse_datetime("last tuesday")
