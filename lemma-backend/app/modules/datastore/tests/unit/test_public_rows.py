"""A table open to people outside: which columns, and what an answer becomes."""

from __future__ import annotations

import pytest

from app.modules.datastore.domain.datastore_entities import ColumnSchema
from app.modules.datastore.domain.public_rows import (
    PublicAudience,
    PublicColumn,
    PublicRowRefused,
    input_kind,
    is_fillable,
    opening_problem,
    public_column,
    public_values,
    refusal_for,
)

pytestmark = pytest.mark.unit

ANYONE = PublicAudience.ANYONE
CONTACTS = PublicAudience.CONTACTS


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
    assert says in (opening_problem(SIGNUPS, "id", chosen, audience=ANYONE) or "")


def test_opening_the_needed_columns_is_enough():
    assert (
        opening_problem(SIGNUPS, "id", ["email", "full_name"], audience=ANYONE) is None
    )


def test_a_needed_column_nobody_outside_could_fill_keeps_the_table_shut():
    columns = [*SIGNUPS, _col("profile", "JSON", required=True)]
    problem = opening_problem(columns, "id", ["email", "full_name"], audience=ANYONE)
    assert problem is not None and "profile" in problem
    assert "can't fill in" in problem


def test_a_unique_column_opens_to_confirmed_contacts_only():
    columns = [*SIGNUPS, _col("badge", unique=True)]
    chosen = ["email", "full_name", "badge"]
    problem = opening_problem(columns, "id", chosen, audience=ANYONE)
    assert problem is not None and "only confirmed contacts" in problem
    assert opening_problem(columns, "id", chosen, audience=CONTACTS) is None


def test_a_required_contact_id_is_the_platforms_to_fill_for_contacts():
    columns = [
        _col("id", "UUID"),
        _col("note"),
        _col("contact_id", "UUID", required=True),
    ]
    assert (
        opening_problem(columns, "id", ["note"], audience=CONTACTS, contact_owned=True)
        is None
    )
    assert opening_problem(columns, "id", ["note"], audience=ANYONE, contact_owned=True)


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
    # Typed by the record validator on insert, as every other write is.
    assert row == {
        "full_name": "Ana",
        "email": "ana@example.com",
        "seats": "2",
        "track": "Code",
        "newsletter": True,
    }


def test_a_blank_optional_answer_leaves_the_default():
    row = public_values(
        OPEN, {"full_name": "Ana", "email": "a@b.co", "seats": " ", "newsletter": ""}
    )
    assert "seats" not in row
    assert "newsletter" not in row


def test_an_explicit_no_is_written_as_no():
    row = public_values(
        OPEN, {"full_name": "Ana", "email": "a@b.co", "newsletter": "off"}
    )
    assert row["newsletter"] is False


@pytest.mark.parametrize(
    ("answers", "column", "says"),
    [
        ({"email": "a@b.co"}, "full_name", "Full name is required"),
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
    with pytest.raises(PublicRowRefused):
        public_values(agree, {})
    assert public_values(agree, {"agree": "on"}) == {"agree": True}


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        (_col("seats", "INTEGER"), "number"),
        (_col("price", "FLOAT"), "number"),
        (_col("newsletter", "BOOLEAN"), "checkbox"),
        (_col("starts", "DATE"), "date"),
        (_col("starts_at", "DATETIME"), "datetime-local"),
        (_col("track", "ENUM", options=["a"]), "select"),
        (_col("work_email"), "email"),
        (_col("mobile_number"), "tel"),
        (_col("message"), "textarea"),
        (_col("full_name"), "text"),
    ],
)
def test_each_column_is_asked_with_one_control(column, expected):
    assert input_kind(column) == expected
    assert public_column(column).input == expected


def test_a_validator_finding_is_said_in_the_persons_words():
    seats = next(c for c in OPEN if c.name == "seats")
    track = next(c for c in OPEN if c.name == "track")
    assert refusal_for(seats, "type").message == "Seats needs to be a number"
    assert "options" in refusal_for(track, "enum").message
    assert refusal_for(None, None).column is None
