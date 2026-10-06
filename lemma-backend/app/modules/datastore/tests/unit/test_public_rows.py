"""A table open to people outside: which columns, and what an answer becomes."""

from __future__ import annotations

import pytest

from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.public_rows import (
    PublicColumn,
    PublicRowRefused,
    is_fillable,
    opening_problem,
    public_column,
    public_values,
)

pytestmark = pytest.mark.unit


def _col(name: str, type: str = "TEXT", **extra) -> ColumnSchema:
    return ColumnSchema(name=name, type=type, **extra)


SIGNUPS = [
    _col("id", "UUID", default="gen_random_uuid()"),
    _col("full_name", required=True),
    _col("email", required=True),
    _col("seats", "INTEGER"),
    _col("track", "ENUM", options=["Design", "Code"]),
    _col("newsletter", "BOOLEAN"),
    _col("embedding", "VECTOR"),
    _col("owner", "USER"),
    _col("contact_id", "UUID"),
    _col("created_at", "DATETIME"),
    _col("status", required=True, default="new"),
]


def test_only_what_a_stranger_can_type_is_offered():
    offered = [c.name for c in SIGNUPS if is_fillable(c, "id")]
    assert offered == [
        "full_name",
        "email",
        "seats",
        "track",
        "newsletter",
        "status",
    ]


def test_a_column_with_a_default_is_never_required_from_outside():
    status = next(c for c in SIGNUPS if c.name == "status")
    assert public_column(status).required is False


@pytest.mark.parametrize(
    ("chosen", "says"),
    [
        ([], "at least one"),
        (["full_name"], "needs email"),
        (["full_name", "email", "contact_id"], "can't fill in contact_id"),
        (["full_name", "email", "embedding"], "can't fill in embedding"),
        (["full_name", "email", "email"], "once"),
    ],
)
def test_opening_refuses_what_could_never_work(chosen, says):
    assert says in (opening_problem(SIGNUPS, "id", chosen) or "")


def test_opening_the_needed_columns_is_enough():
    assert opening_problem(SIGNUPS, "id", ["email", "full_name"]) is None


OPEN = tuple(
    public_column(c)
    for c in SIGNUPS
    if c.name in {"full_name", "email", "seats", "track", "newsletter"}
)


def test_only_the_open_columns_are_written_whatever_is_sent():
    row = public_values(
        OPEN,
        {
            "full_name": " Ana ",
            "email": "ana@example.com",
            "seats": "2",
            "track": "Code",
            "newsletter": "on",
            "contact_id": "someone-else",
            "status": "vip",
        },
    )
    assert row == {
        "full_name": "Ana",
        "email": "ana@example.com",
        "seats": 2,
        "track": "Code",
        "newsletter": True,
    }


def test_a_blank_optional_answer_leaves_the_default():
    row = public_values(OPEN, {"full_name": "Ana", "email": "a@b.co", "seats": " "})
    assert "seats" not in row
    assert row["newsletter"] is False


@pytest.mark.parametrize(
    ("answers", "column", "says"),
    [
        ({"email": "a@b.co"}, "full_name", "Full name is required"),
        ({"full_name": "Ana", "email": "a@b.co", "seats": "two"}, "seats", "number"),
        (
            {"full_name": "Ana", "email": "a@b.co", "track": "Cooking"},
            "track",
            "options",
        ),
        ({"full_name": "x" * 6000, "email": "a@b.co"}, "full_name", "too long"),
        (
            {"full_name": "Ana", "email": "a@b.co", "newsletter": "maybe"},
            "newsletter",
            "yes or no",
        ),
    ],
)
def test_an_answer_that_does_not_fit_says_which(answers, column, says):
    with pytest.raises(PublicRowRefused) as refused:
        public_values(OPEN, answers)
    assert refused.value.column == column
    assert says in refused.value.message


def test_a_required_yes_needs_a_yes():
    agree = (PublicColumn(name="agree", type="BOOLEAN", required=True),)
    with pytest.raises(PublicRowRefused):
        public_values(agree, {"agree": False})
    assert public_values(agree, {"agree": "on"}) == {"agree": True}
