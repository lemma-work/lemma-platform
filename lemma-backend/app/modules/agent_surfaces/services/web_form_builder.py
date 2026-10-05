"""Turning "this table, these columns" into a form, as the member building it.

The table is read as the member saving the form, so they can only build a form
on a table they can see. Every column the table cannot do without must be on
the form -- otherwise every submission would fail -- and every column on the
form must be one a person can fill in.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.context import Context
from app.modules.agent_surfaces.domain.web_forms import (
    INPUTS_FOR_TYPE,
    MAX_FIELDS,
    FieldInput,
    FormField,
    FormSpec,
)
from app.modules.datastore.contracts.forms import (
    FormColumn,
    FormTableUnavailable,
    form_table,
)


class FormFieldRequest(BaseModel):
    column: str = Field(max_length=255)
    label: str | None = Field(default=None, max_length=200)
    input: FieldInput | None = None
    required: bool = False
    hint: str | None = Field(default=None, max_length=300)


class FormRequest(BaseModel):
    """A form on one table: the columns to ask for, in order."""

    table: str = Field(max_length=255)
    fields: list[FormFieldRequest] = Field(min_length=1, max_length=MAX_FIELDS)
    intro: str | None = Field(default=None, max_length=1000)
    confirmation: str | None = Field(default=None, max_length=1000)


class FormNotBuildable(ValueError):
    """Why this form cannot be saved, said to the member building it."""


def _label(column: str) -> str:
    return column.replace("_", " ").strip().capitalize() or column


def guessed_input(column: FormColumn) -> FieldInput:
    """The input a column asks for unless the member says otherwise."""
    if column.type == "TEXT":
        name = column.name.lower()
        if "email" in name:
            return FieldInput.EMAIL
        if "phone" in name or "mobile" in name:
            return FieldInput.PHONE
        if any(word in name for word in ("message", "note", "description", "detail")):
            return FieldInput.LONG
    return INPUTS_FOR_TYPE[column.type][0]


def _field(column: FormColumn, asked: FormFieldRequest) -> FormField:
    choice = asked.input or guessed_input(column)
    if choice not in INPUTS_FOR_TYPE[column.type]:
        raise FormNotBuildable(f"{column.name} can't be asked for as {choice.value}")
    return FormField(
        column=column.name,
        label=(asked.label or "").strip() or _label(column.name),
        input=choice,
        column_type=column.type,
        required=asked.required or column.required,
        hint=(asked.hint or "").strip() or None,
        options=column.options,
    )


async def build_form(
    uow, *, pod_id: UUID, request: FormRequest, ctx: Context
) -> FormSpec:
    """The form to save, or :class:`FormNotBuildable` saying what is wrong."""
    try:
        table = await form_table(uow, pod_id=pod_id, table_name=request.table, ctx=ctx)
    except FormTableUnavailable as exc:
        raise FormNotBuildable(str(exc)) from exc
    columns = {column.name: column for column in table.columns}
    asked = [field.column for field in request.fields]
    if len(set(asked)) != len(asked):
        raise FormNotBuildable("Each column can be on the form once")
    unknown = [name for name in asked if name not in columns]
    if unknown:
        raise FormNotBuildable(f"A form can't ask for {', '.join(unknown)}")
    missing = [
        column.name
        for column in table.columns
        if column.required and column.name not in asked
    ]
    if missing:
        raise FormNotBuildable(
            f"{table.name} needs {', '.join(missing)}, so the form must ask for it"
        )
    return FormSpec(
        table=table.name,
        fields=tuple(_field(columns[field.column], field) for field in request.fields),
        intro=(request.intro or "").strip() or None,
        confirmation=(request.confirmation or "").strip() or None,
    )
