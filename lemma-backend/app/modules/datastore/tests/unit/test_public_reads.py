"""A table people outside may read: which columns, and what a value becomes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.public_reads import (
    ReadColumn,
    is_readable,
    public_row,
    public_value,
    reads_problem,
)

pytestmark = pytest.mark.unit


def _col(name: str, type: str = "TEXT", **extra) -> ColumnSchema:
    return ColumnSchema(name=name, type=type, **extra)


SLOTS = [
    _col("id", "UUID", default="gen_random_uuid()"),
    _col("starts_at", "DATETIME", required=True),
    _col("ends_at", "DATETIME", required=True),
    _col("kind", "ENUM", options=["chat", "demo"]),
    _col("notes", "JSON"),
    _col("owner", "USER"),
    _col("contact_id", "UUID"),
    _col("user_id", "UUID"),
    _col("created_at", "DATETIME"),
]


def test_only_plain_values_and_when_a_row_was_written_can_be_shown():
    shown = [c.name for c in SLOTS if is_readable(c)]
    assert shown == ["id", "starts_at", "ends_at", "kind", "created_at"]


def test_a_member_or_a_contact_is_never_shown():
    by_name = {c.name: c for c in SLOTS}
    for name in ("owner", "user_id", "contact_id"):
        assert not is_readable(by_name[name]), name


def test_opening_names_what_cannot_be_shown():
    assert reads_problem(SLOTS, ["starts_at", "notes", "owner"], None) == (
        "People outside can't be shown notes, owner"
    )


def test_the_order_must_be_one_of_the_open_columns():
    assert reads_problem(SLOTS, ["starts_at"], "ends_at") == (
        "Order the rows by one of the columns people can see"
    )
    assert reads_problem(SLOTS, ["starts_at", "ends_at"], "starts_at") is None


def test_nothing_chosen_or_chosen_twice_is_refused():
    assert reads_problem(SLOTS, [], None) == "Choose at least one column people can see"
    assert reads_problem(SLOTS, ["kind", "kind"], None) == (
        "Each column can be chosen once"
    )


def test_a_row_reaches_the_page_as_json_with_only_the_open_columns():
    columns = (ReadColumn("starts_at", "DATETIME"), ReadColumn("kind", "ENUM"))
    row = {
        "starts_at": datetime(2026, 10, 15, 13, 30, tzinfo=timezone.utc),
        "kind": "chat",
        "notes": {"private": True},
    }
    assert public_row(columns, row) == {
        "starts_at": "2026-10-15T13:30:00+00:00",
        "kind": "chat",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (3, 3),
        (date(2026, 10, 15), "2026-10-15"),
        (Decimal("2.5"), 2.5),
        (UUID(int=7), "00000000-0000-0000-0000-000000000007"),
    ],
)
def test_values_become_what_json_can_carry(value, expected):
    assert public_value(value) == expected
