"""What a page sends and reads to draw a form for an open table, add a row to
it, or read a table the pod marked Public (``public_web_controller``)."""

from __future__ import annotations

from pydantic import BaseModel, Field, JsonValue


class TableColumn(BaseModel):
    name: str
    type: str
    required: bool
    options: list[str]
    description: str | None
    #: The form input that asks for this column (``email``, ``select``, ...),
    #: decided once by the datastore so every page draws it the same way.
    input: str


class TableResponse(BaseModel):
    table: str
    contacts_only: bool
    columns: list[TableColumn]


class PublicColumnItem(BaseModel):
    name: str
    type: str


class PublicRowsResponse(BaseModel):
    table: str
    columns: list[PublicColumnItem]
    rows: list[dict[str, JsonValue]] = Field(
        description=(
            "Every row, at most 500. Dates and times are ISO 8601; a JSON column "
            "is its own lists and objects."
        )
    )
    truncated: bool = Field(
        description="More than 500 rows matched, so these are the first of them."
    )


class RowRequest(BaseModel):
    table: str = Field(max_length=255)
    values: dict[str, object] = Field(default_factory=dict)
    altcha: str | None = Field(
        default=None,
        max_length=4096,
        description=(
            "A solved `row` challenge. Every answer from somebody the pod has "
            "not confirmed needs its own; a confirmed contact sends none."
        ),
    )
