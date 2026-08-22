from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import PodContextDep, require_pod_membership
from app.modules.datastore.api.dependencies import (
    TableServiceDep,
)
from app.modules.datastore.api.responses import detail_response
from app.modules.datastore.api.schemas.datastore_schemas import (
    AddColumnRequest,
    CreateTableRequest,
    TableDetailResponse,
    TableListResponse,
    TableResponse,
    TableSummaryResponse,
    UpdateTableRequest,
)
from app.modules.datastore.domain.datastore_entities import (
    RESERVED_TABLE_PREFIX,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import (
    DatastoreTableNotFoundError,
    DatastoreValidationError,
)

router = APIRouter(
    prefix="/pods/{pod_id}/datastore",
    tags=["tables"],
    redirect_slashes=False,
)


async def _table_detail_response(
    table: DatastoreTableEntity,
) -> TableDetailResponse:
    return detail_response(TableDetailResponse, TableResponse, table)


@router.post(
    "/tables",
    response_model=TableDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="table.create",
    summary="Create Table",
    description=(
        "Create a table in a datastore. Define primary key, column schema, and optional "
        "RLS behavior."
    ),
)
async def create_table(
    pod_id: UUID,
    data: CreateTableRequest,
    table_service: TableServiceDep,
    ctx: PodContextDep,
) -> TableDetailResponse:
    table = await table_service.create_table(
        pod_id=pod_id,
        table_name=data.table_name,
        primary_key_column=data.primary_key_column,
        columns=data.columns,
        config=data.config,
        enable_rls=data.enable_rls,
        visibility=data.visibility,
        ctx=ctx,
    )
    return await _table_detail_response(table)


@router.get(
    "/tables",
    response_model=TableListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="table.list",
    summary="List Tables",
    description="List tables in a datastore.",
    dependencies=[require_pod_membership("list tables", enumerates=True)],
)
async def list_tables(
    pod_id: UUID,
    table_service: TableServiceDep,
    ctx: PodContextDep,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Max number of tables to return.",
    ),
    page_token: Optional[str] = Query(
        default=None,
        description="Cursor from a previous response for pagination.",
    ),
) -> TableListResponse:
    try:
        parse_uuid_page_token(page_token)
    except ValueError as exc:
        raise DatastoreValidationError("Invalid page_token") from exc

    tables, cursor = await table_service.list_table_summaries(
        pod_id,
        ctx=ctx,
        limit=limit,
        cursor=page_token,
    )

    return TableListResponse(
        items=[
            TableSummaryResponse(
                id=table.id,
                pod_id=table.pod_id,
                name=table.name,
                primary_key_column=table.primary_key_column,
                column_count=table.column_count,
                enable_rls=table.enable_rls,
                visibility=table.visibility,
                created_at=table.created_at,
                updated_at=table.updated_at,
                allowed_actions=table.allowed_actions,
            )
            for table in tables
        ],
        limit=limit,
        next_page_token=cursor,
    )


@router.get(
    "/tables/{table_name}",
    response_model=TableDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="table.get",
    summary="Get Table",
    description="Get table schema metadata by table name.",
)
async def get_table(
    pod_id: UUID,
    table_name: str,
    table_service: TableServiceDep,
    ctx: PodContextDep,
) -> TableDetailResponse:
    # System-managed reserved_* tables are never surfaced through the API — hide
    # them by name too, so a reserved table can't be fetched even when guessed.
    if table_name.startswith(RESERVED_TABLE_PREFIX):
        raise DatastoreTableNotFoundError("Table not found")
    table = await table_service.get_table(
        pod_id,
        table_name,
        ctx=ctx,
    )
    return await _table_detail_response(table)


@router.delete(
    "/tables/{table_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="table.delete",
    summary="Delete Table",
    description="Delete a table and all records in it.",
)
async def delete_table(
    pod_id: UUID,
    table_name: str,
    table_service: TableServiceDep,
    ctx: PodContextDep,
) -> Response:
    await table_service.delete_table(
        pod_id,
        table_name,
        ctx=ctx,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/tables/{table_name}",
    response_model=TableDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="table.update",
    summary="Update Table",
    description=(
        "Update table metadata/configuration, visibility, or toggle row-level "
        "security (enable_rls, empty tables only)."
    ),
)
async def update_table(
    pod_id: UUID,
    table_name: str,
    data: UpdateTableRequest,
    table_service: TableServiceDep,
    ctx: PodContextDep,
) -> TableDetailResponse:
    updated_table = await table_service.update_table(
        pod_id,
        table_name,
        data.config,
        ctx=ctx,
        visibility=data.visibility,
        enable_rls=data.enable_rls,
    )
    return await _table_detail_response(updated_table)


@router.post(
    "/tables/{table_name}/columns",
    response_model=TableDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="table.column.add",
    summary="Add Column",
    description=(
        "Add a new column to a table. Column names must be unique and compatible with "
        "existing table schema rules."
    ),
)
async def add_column(
    pod_id: UUID,
    table_name: str,
    data: AddColumnRequest,
    table_service: TableServiceDep,
    ctx: PodContextDep,
) -> TableDetailResponse:
    updated_table = await table_service.add_column(
        pod_id,
        table_name,
        data.column,
        ctx=ctx,
    )
    return await _table_detail_response(updated_table)


@router.delete(
    "/tables/{table_name}/columns/{column_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="table.column.remove",
    summary="Remove Column",
    description=(
        "Remove a non-primary, non-system column from a table. System columns and the "
        "primary key cannot be removed."
    ),
)
async def remove_column(
    pod_id: UUID,
    table_name: str,
    column_name: str,
    table_service: TableServiceDep,
    ctx: PodContextDep,
) -> Response:
    await table_service.remove_column(
        pod_id,
        table_name,
        column_name,
        ctx=ctx,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
