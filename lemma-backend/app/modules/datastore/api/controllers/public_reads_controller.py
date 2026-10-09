"""Opening a table for reads from outside the pod.

Opening a table for reads lets a page -- an app, a website -- show its rows to
people who are not members: confirmed contacts, or anyone. Only the chosen
columns, and never on a table that takes rows from outside. Reading what is
open takes reading the table; opening and closing it take changing it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.public_web import public_web_enabled
from app.modules.datastore.domain.public_rows import PublicAudience
from app.modules.datastore.services.public_reads import (
    ReadsOpening,
    close_reads,
    open_reads,
    reads_opening,
)
from app.modules.datastore.services.public_rows import OpeningRefused

router = APIRouter(
    prefix="/pods/{pod_id}/datastore",
    tags=["tables"],
    redirect_slashes=False,
)


class ReadColumnResponse(BaseModel):
    name: str
    type: str


class ReadsOpeningResponse(BaseModel):
    table: str
    per_user: bool = Field(
        description="Each member sees only their own rows, so it can't be opened."
    )
    contact_owned: bool = Field(
        description="Each row is one contact's, so it can't be opened."
    )
    takes_rows: bool = Field(
        description="It takes rows from outside, so it can't be opened for reads."
    )
    offered: list[ReadColumnResponse] = Field(
        description="The columns people outside could be shown."
    )
    audience: PublicAudience | None = Field(
        description="Who outside may read it; null when it is closed."
    )
    columns: list[str] = Field(description="The open columns, in order.")
    order_by: str | None = Field(
        description="The open column rows are read in ascending order of."
    )


class OpenReadsRequest(BaseModel):
    audience: PublicAudience
    columns: list[str] = Field(
        description=(
            "The columns people outside may see, in the order to show them. "
            "Checked against the table when it is opened."
        )
    )
    order_by: str | None = Field(
        default=None,
        description="One of the open columns, to read rows in ascending order of.",
    )


def _opening(opening: ReadsOpening) -> ReadsOpeningResponse:
    return ReadsOpeningResponse(
        table=opening.name,
        per_user=opening.per_user,
        contact_owned=opening.contact_owned,
        takes_rows=opening.takes_rows,
        offered=[
            ReadColumnResponse(name=column.name, type=column.type)
            for column in opening.offered
        ],
        audience=opening.audience,
        columns=list(opening.columns),
        order_by=opening.order_by,
    )


@router.get(
    "/tables/{table_name}/public-reads",
    response_model=ReadsOpeningResponse,
    operation_id="table.public_reads.get",
    summary="Who Outside May Read Rows",
)
async def get_public_reads(
    pod_id: UUID, table_name: str, uow: UoWDep, ctx: PodContextDep
) -> ReadsOpeningResponse:
    return _opening(
        await reads_opening(uow, pod_id=pod_id, table_name=table_name, ctx=ctx)
    )


@router.put(
    "/tables/{table_name}/public-reads",
    response_model=ReadsOpeningResponse,
    operation_id="table.public_reads.open",
    summary="Let People Outside Read Rows",
    description=(
        "Open the table's rows to people outside the pod -- confirmed contacts, "
        "or anyone -- for the chosen columns only. Rows are read as the member "
        "opening it, who must be able to change the table. A table that takes "
        "rows from outside can't be opened for reads."
    ),
)
async def put_public_reads(
    pod_id: UUID,
    table_name: str,
    request: OpenReadsRequest,
    uow: UoWDep,
    ctx: PodContextDep,
) -> ReadsOpeningResponse:
    if not public_web_enabled():
        # On a deployment that serves no pages to people outside, opening a
        # table for reads would open it to nobody.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "PUBLIC_WEB_DISABLED",
                "message": "Forms are switched off on this deployment",
            },
        )
    try:
        opening = await open_reads(
            uow,
            pod_id=pod_id,
            table_name=table_name,
            audience=request.audience,
            columns=request.columns,
            order_by=request.order_by,
            ctx=ctx,
        )
    except OpeningRefused as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await uow.commit()
    return _opening(opening)


@router.delete(
    "/tables/{table_name}/public-reads",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="table.public_reads.close",
    summary="Stop People Outside Reading Rows",
)
async def delete_public_reads(
    pod_id: UUID, table_name: str, uow: UoWDep, ctx: PodContextDep
) -> None:
    await close_reads(uow, pod_id=pod_id, table_name=table_name, ctx=ctx)
    await uow.commit()
