"""Filling in a form on a web visitor's page, from the conversation.

A web visitor can be looking at a form -- a page that adds a row to a table the
pod opened to visitors -- with the pod's chat beside it. ``fill_form`` puts
answers into that form's fields as the conversation turns them up ("I'm Priya
from Northwind, two seats"). It writes nothing: the visitor sees the fields
fill, checks them, and presses Send themselves.

Only the table's open columns are ever filled, and only with values that fit
them; whatever else the model offers is dropped here, before the page sees it.
The page is told through the tool's *result* (see ``contracts/visitor_stream``),
so what reaches it is always what passed this check, never the raw call.
"""

from __future__ import annotations

from functools import partial
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.datastore.contracts.public_rows import (
    OpenTable,
    PublicColumn,
    open_table_names,
    visitor_table,
)

FILL_FORM_TOOL = "fill_form"
FORM_TOOLSET_ID = "visitor_form"

#: The longest value one field is filled with.
MAX_FILL_CHARS = 2000


class ReadOpenTable(Protocol):
    async def __call__(self, *, pod_id: UUID, table: str) -> OpenTable | None: ...


class ListOpenTables(Protocol):
    async def __call__(self, *, pod_id: UUID) -> list[str]: ...


class FillFormRequest(BaseModel):
    table: str = Field(
        default="",
        description=(
            "The form's table. Leave empty to learn which forms you can fill "
            "and what each asks for."
        ),
    )
    values: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Field name to the answer the person gave. Only what they said.",
    )


def _fits(column: PublicColumn, value: str | int | float | bool) -> object | None:
    if column.type == "BOOLEAN":
        return (
            value
            if isinstance(value, bool)
            else str(value).lower()
            in {
                "true",
                "yes",
                "on",
                "1",
            }
        )
    text = str(value).strip()[:MAX_FILL_CHARS]
    if not text:
        return None
    if column.type == "ENUM" and text not in column.options:
        return None
    return text


def _asks(opened: OpenTable) -> list[dict[str, object]]:
    return [
        {
            "name": column.name,
            "type": column.type,
            "required": column.required,
            **({"options": list(column.options)} if column.options else {}),
        }
        for column in opened.columns
    ]


async def _read_table(
    uow_factory: UnitOfWorkFactory, *, pod_id: UUID, table: str
) -> OpenTable | None:
    async with uow_factory() as uow:
        return await visitor_table(uow, pod_id=pod_id, table_name=table)


async def _list_tables(uow_factory: UnitOfWorkFactory, *, pod_id: UUID) -> list[str]:
    async with uow_factory() as uow:
        return [name for name, _ in await open_table_names(uow, pod_id=pod_id)]


def build_form_toolset(
    *,
    uow_factory: UnitOfWorkFactory,
    read_table: ReadOpenTable | None = None,
    list_tables: ListOpenTables | None = None,
) -> FunctionToolset[BaseAgentContext]:
    read_table = read_table or partial(_read_table, uow_factory)
    list_tables = list_tables or partial(_list_tables, uow_factory)

    async def fill_form(
        ctx: RunContext[BaseAgentContext], request: FillFormRequest
    ) -> dict[str, object]:
        """Fill in fields of the form on the visitor's page with what they told
        you. It does not send the form: they check it and press Send. Call it
        with no table first if you don't know what the form asks for."""
        pod_id = ctx.deps.pod_id
        if pod_id is None:
            return {"success": False, "error": "There is no form here."}
        names = await list_tables(pod_id=pod_id)
        table = request.table or (names[0] if len(names) == 1 else "")
        opened = await read_table(pod_id=pod_id, table=table) if table else None
        if opened is None:
            forms = [
                {"table": name, "asks": _asks(found)}
                for name in names
                if (found := await read_table(pod_id=pod_id, table=name))
            ]
            return {"success": False, "forms": forms, "error": "Choose a form."}
        if not request.values:
            return {"success": False, "table": opened.name, "asks": _asks(opened)}
        by_name = {column.name: column for column in opened.columns}
        filled = {
            name: fitted
            for name, value in request.values.items()
            if name in by_name and (fitted := _fits(by_name[name], value)) is not None
        }
        skipped = sorted(set(request.values) - set(filled))
        return {
            "success": bool(filled),
            "table": opened.name,
            "filled": filled,
            **({"skipped": skipped} if skipped else {}),
            "note": "Filled on their page. Ask them to check it and press Send.",
        }

    return FunctionToolset[BaseAgentContext](tools=[fill_form], id=FORM_TOOLSET_ID)
