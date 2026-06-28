"""Request models for the pod toolset."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.modules.agent.domain.value_objects import JsonObject


class RecordFilter(BaseModel):
    column: str
    op: str = Field(default="eq", description="eq, ne, gt, gte, lt, lte, like, in.")
    # Explicit scalar/list union (not bare `Any`): a bare Any field serializes to
    # a typeless `{"default": null}` JSON-schema node that strict providers
    # (e.g. Fireworks) reject with "could not understand the instance".
    value: str | int | float | bool | list[str | int | float | bool] | None = None


class RecordSort(BaseModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


# --- Datastore --------------------------------------------------------------


class PodTablesRequest(BaseModel):
    table_name: str | None = Field(
        default=None,
        description=(
            "Omit to list all tables with their column schemas; pass a name to "
            "describe just that table."
        ),
    )
    limit: int = Field(default=100, ge=1, le=500, description="Max tables when listing.")


class PodGetRecordsRequest(BaseModel):
    table_name: str
    record_id: str | None = Field(
        default=None,
        description="Fetch a single record by id; omit to list with filters/sorts.",
    )
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    filters: list[RecordFilter] = Field(default_factory=list)
    sorts: list[RecordSort] = Field(default_factory=list)


class PodWriteRecordRequest(BaseModel):
    action: Literal["create", "update", "delete"] = Field(
        description="'create' a new record, 'update' an existing one, or 'delete' one."
    )
    table_name: str
    record_id: str | None = Field(
        default=None,
        description="Target record id. Required for 'update' and 'delete'.",
    )
    # Accepts a JSON object OR a JSON-encoded string of that object. The `str`
    # member exists so the tool schema advertises the string form: OpenAI
    # requires every object schema to carry a `properties` map, and a free-form
    # (dynamic-column) object necessarily serializes with `properties: {}` —
    # which many models read as "no fields" and fill with `{}`, silently
    # dropping the row. The string form is an unambiguous escape hatch. The
    # `_coerce_data` validator decodes any string back to a dict, so downstream
    # code only ever sees `dict | None`.
    data: JsonObject | str | None = Field(
        default=None,
        description=(
            "The row's column name -> value mapping. Pass EITHER a JSON object, "
            'e.g. {"title": "Q3 report", "amount": 42, "tags": ["a", "b"]}, OR a '
            "JSON-encoded string of that same object if you cannot emit a "
            'populated object directly, e.g. "{\\"title\\": \\"Q3 report\\", '
            '\\"amount\\": 42}". Values may be scalars, nested objects, or '
            "arrays. REQUIRED and must be non-empty for 'create' and 'update' — "
            "an empty object writes nothing and is rejected."
        ),
    )

    @field_validator("data", mode="before")
    @classmethod
    def _coerce_data(cls, value: object) -> object:
        """Decode a JSON-encoded string payload into an object.

        Models on OpenAI-compatible providers often pass ``data`` as a JSON
        string instead of a native object (see the field comment). Parse it here
        so the rest of the toolset always receives a ``dict``; a blank string
        becomes ``None`` (caught by the non-empty guard), and a string that is
        not a JSON object is rejected with an actionable message.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                '`data` was a string but not valid JSON; pass a JSON object like '
                '{"title": "Q3 report"} (or a JSON-encoded string of it).'
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "`data` must be (or JSON-decode to) an object of column->value, "
                f"not {type(parsed).__name__}."
            )
        return parsed


class QueryRequest(BaseModel):
    sql: str = Field(
        ...,
        description=(
            "A single read-only SELECT over the pod's tables. Joins/aggregates "
            "across tables are allowed, including RLS tables (rows scoped to you "
            "unless you administer the table). Mutations are rejected."
        ),
    )


# --- Files ------------------------------------------------------------------


class PodListFilesRequest(BaseModel):
    path: str = Field(default="/", description="Folder path, e.g. /pod or /me.")
    recursive: bool = Field(
        default=False,
        description=(
            "False (default) = immediate files/folders in `path`. True = a file "
            "tree rooted at `path` (folders + sample files per directory)."
        ),
    )
    limit: int = Field(
        default=100, ge=1, le=500, description="Max entries when not recursive."
    )
    files_per_directory: int = Field(
        default=20, ge=1, le=200, description="Sample files per directory when recursive."
    )


class PodReadFileRequest(BaseModel):
    path: str = Field(..., description="Absolute pod file path, e.g. /pod/notes.txt.")
    format: Literal["text", "markdown"] = Field(
        default="text",
        description=(
            "'text' = raw file text. 'markdown' = converted document text (PDF, "
            "DOCX, ...); supports a page range. To SEE pages as images instead, "
            "use pod_view_document_pages."
        ),
    )
    page_start: int | None = Field(
        default=None,
        ge=1,
        description="markdown only: first page (1-based). Omit for the whole document.",
    )
    page_end: int | None = Field(
        default=None,
        ge=1,
        description="markdown only: last page (1-based, inclusive). Defaults to page_start.",
    )
    max_chars: int = Field(default=50000, ge=1, le=400000)


class ViewDocumentPagesRequest(BaseModel):
    path: str = Field(..., description="Path of a PDF document in the pod.")
    page_start: int = Field(
        ..., ge=1, description="First page (1-based) to render as an image."
    )
    page_end: int | None = Field(
        default=None,
        ge=1,
        description="Last page (1-based, inclusive). Defaults to page_start.",
    )


class GetFileUrlRequest(BaseModel):
    path: str = Field(..., description="Absolute pod file path.")
    url_type: Literal["app", "public"] = Field(
        default="app",
        description=(
            "'app' = authenticated in-app link for a signed-in pod member "
            "(returns app_url + a short-lived download url). "
            "'public' = a public, hit-capped signed link anyone can open without "
            "logging in (returns signed_url) — use to email/message a file to "
            "someone outside the pod."
        ),
    )
    expires_seconds: int | None = Field(
        default=None,
        ge=1,
        le=86400,
        description=(
            "Link lifetime in seconds. 'app': lifetime of the download url "
            "(default ~1 hour). 'public': lifetime of the signed link "
            "(default 3 hours, max 24 hours)."
        ),
    )
    max_hits: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "'public' links only: maximum downloads before the link stops working "
            "(default 50, max 100). Bounds egress if the link leaks. Ignored for 'app'."
        ),
    )


class SearchFilesRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    method: Literal["VECTOR", "TEXT", "HYBRID"] = "HYBRID"
    scope_path: str | None = Field(
        default=None, description="Restrict search to this folder subtree."
    )
