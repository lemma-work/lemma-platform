from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.encoders import jsonable_encoder

from app.core.api.dependencies import CurrentUser
from app.core.authorization.dependencies import PodContextDep
from app.modules.datastore.api.dependencies import RecordServiceDep, TableServiceDep
from app.modules.datastore.api.schemas.datastore_schemas import (
    DatastoreQueryRequest,
    DatastoreQueryResponse,
    RecordAccessMode,
)

router = APIRouter(
    prefix="/pods/{pod_id}/datastore",
    tags=["query"],
    redirect_slashes=False,
)

_QUERY_MODE_DESCRIPTION = (
    "Row-visibility mode for RLS-enabled tables referenced by the query. "
    "Omitted/`USER` (default) scopes their rows to the signed-in user — the "
    "per-user data apps and functions expect. `ADMIN` returns every member's "
    "rows and requires permission to administer every RLS table the query "
    "touches; a caller without it gets a 403. Non-RLS tables are unaffected."
)


@router.post(
    "/query",
    response_model=DatastoreQueryResponse,
    status_code=status.HTTP_200_OK,
    operation_id="query.execute",
    summary="Execute Query",
    description=(
        "Execute a read-only SQL query inside the datastore schema. Joins, "
        "aggregates, subqueries, and cross-table reads are allowed, including "
        "across RLS-enabled tables — rows of RLS tables are scoped to the caller "
        "by default (pod admins included). Pass `mode=admin` to read every "
        "member's rows, which requires permission to administer each referenced "
        "RLS table. Only a single read-only statement is permitted; mutating "
        "statements and cross-schema references are rejected."
    ),
)
async def execute_query(
    pod_id: UUID,
    data: DatastoreQueryRequest,
    record_service: RecordServiceDep,
    table_service: TableServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_QUERY_MODE_DESCRIPTION,
    ),
) -> DatastoreQueryResponse:
    rows, total, _truncated = await record_service.execute_readonly_query(
        pod_id=pod_id,
        query=data.query,
        user_id=user.id,
        table_service=table_service,
        ctx=ctx,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    return DatastoreQueryResponse(
        # Postgres numerics (avg/sum/… and NUMERIC columns) arrive as Decimal,
        # which the default encoder renders as a string — so `avg(x)` came back
        # as "120.0000000000000000". Encode them as JSON numbers instead; the
        # tradeoff is float precision, acceptable for the aggregate reads this
        # endpoint serves.
        items=jsonable_encoder(rows, custom_encoder={Decimal: float}),
        total=total,
    )
