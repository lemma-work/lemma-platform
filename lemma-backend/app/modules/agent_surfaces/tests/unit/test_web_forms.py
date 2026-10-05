"""A table form's answers: only its columns, each checked, before anything is written."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.web_forms import (
    FieldInput,
    FormAnswerRefused,
    FormField,
    FormSpec,
    form_values,
    public_form,
)
from app.modules.agent_surfaces.services.web_form_builder import guessed_input
from app.modules.datastore.contracts.forms import FormColumn

pytestmark = pytest.mark.unit


def _field(column: str, input: FieldInput, **extra) -> FormField:
    column_type = {
        FieldInput.NUMBER: "INTEGER",
        FieldInput.CHECKBOX: "BOOLEAN",
        FieldInput.CHOICE: "ENUM",
    }.get(input, "TEXT")
    return FormField(
        column=column,
        label=extra.pop("label", column.title()),
        input=input,
        column_type=extra.pop("column_type", column_type),
        **extra,
    )


SIGNUP = FormSpec(
    table="signups",
    fields=(
        _field("name", FieldInput.TEXT, required=True),
        _field("email", FieldInput.EMAIL, required=True),
        _field("seats", FieldInput.NUMBER),
        _field("newsletter", FieldInput.CHECKBOX),
        _field("track", FieldInput.CHOICE, options=("Design", "Code")),
    ),
    confirmation="See you Saturday.",
)


def test_only_the_forms_columns_are_written_whatever_is_sent():
    row = form_values(
        SIGNUP,
        {
            "name": " Ana ",
            "email": "ana@example.com",
            "seats": "2",
            "newsletter": "on",
            "track": "Code",
            "contact_id": "someone-else",
            "is_admin": True,
        },
    )
    assert row == {
        "name": "Ana",
        "email": "ana@example.com",
        "seats": 2,
        "newsletter": True,
        "track": "Code",
    }


def test_a_blank_optional_field_is_left_to_the_columns_default():
    row = form_values(SIGNUP, {"name": "Ana", "email": "ana@example.com", "seats": ""})
    assert "seats" not in row
    assert row["newsletter"] is False


@pytest.mark.parametrize(
    ("answers", "field", "says"),
    [
        ({"email": "ana@example.com"}, "name", "Name is required"),
        ({"name": "Ana", "email": "not-an-email"}, "email", "email address"),
        ({"name": "Ana", "email": "a@b.co", "seats": "two"}, "seats", "number"),
        ({"name": "Ana", "email": "a@b.co", "track": "Cooking"}, "track", "options"),
        ({"name": "x" * 6000, "email": "a@b.co"}, "name", "too long"),
    ],
)
def test_an_answer_that_does_not_fit_says_which_field(answers, field, says):
    with pytest.raises(FormAnswerRefused) as refused:
        form_values(SIGNUP, answers)
    assert refused.value.field == field
    assert says in refused.value.message


def test_the_page_is_told_how_to_draw_the_form_and_not_the_table():
    drawn = public_form(SIGNUP, title="Workshop sign-up")
    assert drawn["title"] == "Workshop sign-up"
    assert drawn["confirmation"] == "See you Saturday."
    assert [f["name"] for f in drawn["fields"]] == [
        "name",
        "email",
        "seats",
        "newsletter",
        "track",
    ]
    assert drawn["fields"][4]["options"] == ["Design", "Code"]
    assert "signups" not in str(drawn)


def _column(name: str, type: str = "TEXT") -> FormColumn:
    return FormColumn(
        name=name, type=type, required=False, options=(), description=None
    )


def test_a_columns_name_suggests_what_to_ask_for():
    assert guessed_input(_column("work_email")) is FieldInput.EMAIL
    assert guessed_input(_column("mobile")) is FieldInput.PHONE
    assert guessed_input(_column("message")) is FieldInput.LONG
    assert guessed_input(_column("company")) is FieldInput.TEXT
    assert guessed_input(_column("starts_on", "DATE")) is FieldInput.DATE
    assert guessed_input(_column("level", "ENUM")) is FieldInput.CHOICE
