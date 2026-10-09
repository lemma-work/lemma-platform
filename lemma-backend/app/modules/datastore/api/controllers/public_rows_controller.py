"""Opening a table to people outside the pod.

Opening a table lets a page -- an app, a website, the page Lemma hosts -- add
rows to it for people who are not members: confirmed contacts, or anyone. Only
the chosen columns, never a read. Reading what is open takes reading the table;
opening and closing it take changing it. Listing what is open shows only the
tables the caller can read.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.public_web import public_web_enabled
from app.modules.datastore.domain.public_rows import PublicAudience, PublicColumn
from app.modules.datastore.services.public_rows import (
    OpeningRefused,
    TableOpening,
    close_table,
    open_table,
    open_tables,
    table_opening,
)

router = APIRouter(
    prefix="/pods/{pod_id}/datastore",
    tags=["tables"],
    redirect_slashes=False,
)


class PublicColumnResponse(BaseModel):
    name: str
    type: str
    required: bool = Field(description="The table needs it, so it must be open.")
    options: list[str]
    description: str | None
    input: str = Field(description="The form control to ask with.")


class TableOpeningResponse(BaseModel):
    table: str
    per_user: bool = Field(
        description="Each member sees only their own rows, so it can't be opened."
    )
    contact_owned: bool = Field(
        description="A confirmed contact's row names them in contact_id."
    )
    offered: list[PublicColumnResponse] = Field(
        description="The columns people outside could be asked to fill."
    )
    audience: PublicAudience | None = Field(
        description="Who outside may add rows; null when the table is closed."
    )
    columns: list[str] = Field(description="The open columns, in order.")


class OpenTableRequest(BaseModel):
    audience: PublicAudience
    columns: list[str] = Field(
        description=(
            "The columns people outside may fill, in the order to ask them. "
            "Checked against the table when it is opened."
        )
    )


class OpenTableSummary(BaseModel):
    table: str
    audience: PublicAudience


class OpenTablesResponse(BaseModel):
    items: list[OpenTableSummary]


def _column(column: PublicColumn) -> PublicColumnResponse:
    return PublicColumnResponse(
        name=column.name,
        type=column.type,
        required=column.required,
        options=list(column.options),
        description=column.description,
        input=column.input,
    )


def _opening(opening: TableOpening) -> TableOpeningResponse:
    return TableOpeningResponse(
        table=opening.name,
        per_user=opening.per_user,
        contact_owned=opening.contact_owned,
        offered=[_column(column) for column in opening.offered],
        audience=opening.audience,
        columns=list(opening.columns),
    )


@router.get(
    "/tables/{table_name}/public-rows",
    response_model=TableOpeningResponse,
    operation_id="table.public_rows.get",
    summary="Who Outside May Add Rows",
)
async def get_public_rows(
    pod_id: UUID, table_name: str, uow: UoWDep, ctx: PodContextDep
) -> TableOpeningResponse:
    return _opening(
        await table_opening(uow, pod_id=pod_id, table_name=table_name, ctx=ctx)
    )


@router.put(
    "/tables/{table_name}/public-rows",
    response_model=TableOpeningResponse,
    operation_id="table.public_rows.open",
    summary="Let People Outside Add Rows",
    description=(
        "Open the table to rows from people outside the pod -- confirmed "
        "contacts, or anyone -- for the chosen columns only. Rows are added as "
        "the member opening it, who must be able to change the table."
    ),
)
async def put_public_rows(
    pod_id: UUID,
    table_name: str,
    request: OpenTableRequest,
    uow: UoWDep,
    ctx: PodContextDep,
) -> TableOpeningResponse:
    if not public_web_enabled():
        # Opening a table is what lets a page add rows to it; on a deployment
        # that serves no pages to people outside, it would open nothing.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "PUBLIC_WEB_DISABLED",
                "message": "Forms are switched off on this deployment",
            },
        )
    try:
        opening = await open_table(
            uow,
            pod_id=pod_id,
            table_name=table_name,
            audience=request.audience,
            columns=request.columns,
            ctx=ctx,
        )
    except OpeningRefused as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await uow.commit()
    return _opening(opening)


@router.delete(
    "/tables/{table_name}/public-rows",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="table.public_rows.close",
    summary="Stop People Outside Adding Rows",
)
async def delete_public_rows(
    pod_id: UUID, table_name: str, uow: UoWDep, ctx: PodContextDep
) -> None:
    await close_table(uow, pod_id=pod_id, table_name=table_name, ctx=ctx)
    await uow.commit()


@router.get(
    "/public-rows",
    response_model=OpenTablesResponse,
    operation_id="table.public_rows.list",
    summary="Tables Open To People Outside",
    description="The open tables of the pod that the caller can read.",
)
async def list_public_rows(
    pod_id: UUID, uow: UoWDep, ctx: PodContextDep
) -> OpenTablesResponse:
    opened = await open_tables(uow, pod_id=pod_id, readable_by_ctx=ctx)
    return OpenTablesResponse(
        items=[
            OpenTableSummary(table=name, audience=audience) for name, audience in opened
        ]
    )
