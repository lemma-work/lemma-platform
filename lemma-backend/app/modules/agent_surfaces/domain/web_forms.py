"""A web form: a door into one table, and nothing else.

A member picks a table and ticks the columns to ask for. A visitor's submission
is reduced to exactly those columns, checked against what each one asks for,
and handed to ``datastore`` as one insert by the member who looks after the
form. Nothing a visitor sends can name another column, read a row back, or run
code -- there is no code.

The field list is a snapshot taken when the form is saved: what the member saw
is what the visitor gets, even if the table changes afterwards. A column that
has since gone makes the insert fail, and the visitor is told the form did not
go through rather than having their answer quietly dropped.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: The most fields one form asks for.
MAX_FIELDS = 30
#: The longest answer one field takes.
MAX_ANSWER_CHARS = 5000

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[\d\s().-]{6,20}$")
_TRUE = frozenset({"true", "on", "yes", "1"})
_FALSE = frozenset({"false", "off", "no", "0", ""})


class FieldInput(StrEnum):
    """What a visitor types into a field. Chosen from the column, adjustable."""

    TEXT = "text"
    LONG = "long"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    CHECKBOX = "checkbox"
    CHOICE = "choice"


#: Which inputs fit which column type. The first is the default.
INPUTS_FOR_TYPE: dict[str, tuple[FieldInput, ...]] = {
    "TEXT": (FieldInput.TEXT, FieldInput.LONG, FieldInput.EMAIL, FieldInput.PHONE),
    "INTEGER": (FieldInput.NUMBER,),
    "FLOAT": (FieldInput.NUMBER,),
    "BOOLEAN": (FieldInput.CHECKBOX,),
    "DATE": (FieldInput.DATE,),
    "DATETIME": (FieldInput.DATETIME,),
    "ENUM": (FieldInput.CHOICE,),
}


class FormField(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    label: str = Field(max_length=200)
    input: FieldInput
    column_type: str
    required: bool = False
    hint: str | None = Field(default=None, max_length=300)
    options: tuple[str, ...] = ()


class FormSpec(BaseModel):
    """What a form asks for, and what it says before and after."""

    model_config = ConfigDict(frozen=True)

    table: str
    fields: tuple[FormField, ...] = Field(min_length=1, max_length=MAX_FIELDS)
    intro: str | None = Field(default=None, max_length=1000)
    confirmation: str | None = Field(default=None, max_length=1000)


class FormAnswerRefused(ValueError):
    """A submission that does not fit the form, said so the visitor can fix it."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


DEFAULT_CONFIRMATION = "Thanks, we've got it."


def form_values(spec: FormSpec, answers: dict[str, object]) -> dict[str, object]:
    """The row a submission adds: the form's columns only, each checked.

    Anything not on the form is ignored, never passed on. A blank optional
    field is left out, so the column's own default applies.
    """
    row: dict[str, object] = {}
    for field in spec.fields:
        value = _answer(field, answers.get(field.column))
        if value is None:
            if field.required:
                raise FormAnswerRefused(
                    f"{field.label} is required", field=field.column
                )
            continue
        row[field.column] = value
    return row


def _answer(field: FormField, raw: object) -> object | None:
    if field.input is FieldInput.CHECKBOX:
        return _checkbox(field, raw)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > MAX_ANSWER_CHARS:
        raise FormAnswerRefused(f"{field.label} is too long", field=field.column)
    if field.input is FieldInput.NUMBER:
        return _number(field, text)
    if field.input is FieldInput.EMAIL and not _EMAIL.match(text):
        raise FormAnswerRefused(
            f"{field.label} needs to be an email address", field=field.column
        )
    if field.input is FieldInput.PHONE and not _PHONE.match(text):
        raise FormAnswerRefused(
            f"{field.label} needs to be a phone number", field=field.column
        )
    if field.input is FieldInput.CHOICE and text not in field.options:
        raise FormAnswerRefused(
            f"Choose one of the options for {field.label}", field=field.column
        )
    return text


def _checkbox(field: FormField, raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return None if field.required else False
    raise FormAnswerRefused(f"{field.label} is a yes or no", field=field.column)


def _number(field: FormField, text: str) -> int | float:
    try:
        return int(text) if field.column_type == "INTEGER" else float(text)
    except ValueError as exc:
        raise FormAnswerRefused(
            f"{field.label} needs to be a number", field=field.column
        ) from exc


def public_form(spec: FormSpec, *, title: str) -> dict[str, object]:
    """What a page is told about the form: enough to draw it, nothing more.

    Not the table's name: that is the pod's, and a field's ``name`` is all the
    page sends back.
    """
    return {
        "title": title,
        "intro": spec.intro,
        "confirmation": spec.confirmation or DEFAULT_CONFIRMATION,
        "fields": [
            {
                "name": field.column,
                "label": field.label,
                "input": field.input.value,
                "required": field.required,
                "hint": field.hint,
                "options": list(field.options),
            }
            for field in spec.fields
        ],
    }
