from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.core.api.pagination import (
    decode_offset_page_token,
    encode_offset_page_token,
)
from app.core.api.dependencies import CurrentUser
from app.core.authorization.context import Context
from app.core.authorization.dependencies import PodContextDep
from app.modules.datastore.api.dependencies import (
    RecordServiceDep,
    TableServiceDep,
)
from app.modules.datastore.api.record_query import (
    parse_record_filters,
    parse_record_sorts,
)
from app.modules.datastore.api.schemas.datastore_schemas import (
    BulkCreateRecordsRequest,
    BulkDeleteRecordsRequest,
    BulkUpdateRecordsRequest,
    CreateRecordRequest,
    DatastoreCountResponse,
    RecordAccessMode,
    RecordListResponse,
    UpdateRecordRequest,
)
from app.modules.datastore.domain.datastore_entities import ensure_table_mutable
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.table_service import TableService

router = APIRouter(
    prefix="/pods/{pod_id}/datastore/tables/{table_name}/records",
    tags=["records"],
    redirect_slashes=False,
)

_MODE_DESCRIPTION = (
    "Row-visibility mode for RLS-enabled tables. Omitted/`USER` (default) scopes "
    "rows to the signed-in user's own records — the per-user semantics an app app "
    "expects. `ADMIN` returns/operates on every member's rows and requires "
    "permission to administer the table; a caller without it gets a 403. Ignored "
    "for non-RLS tables, whose rows are shared by all members."
)


async def _get_table_context(
    pod_id: UUID,
    table_name: str,
    table_service: TableService,
    ctx: Context,
) -> TableContext:
    table = await table_service.get_table(pod_id, table_name, ctx)
    schema_name = table_service.schema_manager.get_schema_name(pod_id)
    # Every caller of this helper goes on to work against the DATASTORE engine,
    # not this one — so the application connection is finished here. It used to
    # be held anyway, because the request-scoped unit of work lives until the
    # response is written: measured at 2291ms held for 64ms of querying on a
    # bulk record create, in an open transaction, once per request across nine
    # endpoints.
    #
    # Nothing is lost by committing: the reads are done, and a caller that
    # writes to the application database afterwards simply opens its own
    # transaction when it does.
    await table_service.table_repository.uow.commit()
    # Authorization flows through the ambient context (set by PodContextDep),
    # not through TableContext.
    return TableContext.from_table_entity(
        table,
        schema_name,
        events_enabled=True,
    )


@router.post(
    "",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    operation_id="record.create",
    summary="Create Record",
    description=(
        "Insert a record into a table. Returns the created record object keyed by "
        "column name (no envelope). Reserved tables (`reserved_*`) are system-managed "
        "and cannot be mutated through record write endpoints."
    ),
)
async def create_record(
    pod_id: UUID,
    table_name: str,
    data: CreateRecordRequest,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
) -> dict[str, Any]:
    ensure_table_mutable(table_name)
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    record = await record_service.create_record(ctx, data.data, user.id)
    return record.data


@router.get(
    "",
    response_model=RecordListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="record.list",
    summary="List Records",
    description=(
        "List table records with token pagination only. Use the datastore query "
        "endpoint for joins, aggregates, or custom read-only SQL."
    ),
)
async def list_records(
    pod_id: UUID,
    table_name: str,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
    limit: int = Query(default=20, ge=1, description="Max number of rows to return."),
    offset: int = Query(
        default=0, ge=0, description="Row offset for direct pagination."
    ),
    filter: list[str] | None = Query(
        default=None,
        description=(
            "Optional repeated JSON filters for advanced comparisons. "
            "Each `filter` value must be a JSON object with shape "
            '`{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. '
            "Allowed operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`. "
            "Repeat the query parameter to combine multiple filters with AND semantics. "
            'Examples: `filter={"field":"amount","op":"gt","value":100}` and '
            '`filter={"field":"status","op":"eq","value":"OPEN"}`.'
        ),
    ),
    sort: list[str] | None = Query(
        default=None,
        description=(
            "Optional repeated JSON sort clauses. "
            "Each `sort` value must be a JSON object with shape "
            '`{"field":"<column_name>","direction":"<direction>"}`. '
            "Allowed directions are: `asc`, `desc`. "
            "Repeat the query parameter to provide multi-column sorting in priority order. "
            'Example: `sort={"field":"created_at","direction":"desc"}`.'
        ),
    ),
    page_token: str | None = Query(
        default=None,
        description="Opaque token from a previous response page.",
    ),
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_MODE_DESCRIPTION,
    ),
) -> RecordListResponse:
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )

    if page_token is not None:
        offset = decode_offset_page_token(page_token)

    filters = parse_record_filters(filter)
    sorts = parse_record_sorts(sort)

    items, total = await record_service.list_records(
        ctx,
        user.id,
        limit,
        offset,
        sorts,
        filters or None,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    next_offset = offset + len(items)
    return RecordListResponse(
        items=[record.data for record in items],
        total=total,
        limit=limit,
        next_page_token=(
            encode_offset_page_token(next_offset) if next_offset < total else None
        ),
    )


@router.get(
    "/{record_id}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    operation_id="record.get",
    summary="Get Record",
    description=(
        "Fetch one record by primary key value (returns the record object, no "
        "envelope). The `record_id` path segment is the table's primary key value as "
        "stored in the table, not necessarily a UUID."
    ),
)
async def get_record(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_MODE_DESCRIPTION,
    ),
) -> dict[str, Any]:
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    record = await record_service.get_record(
        ctx,
        record_id,
        user.id,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    return record.data


@router.patch(
    "/{record_id}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    operation_id="record.update",
    summary="Update Record",
    description="Patch a record by primary key. Returns the updated record object (no envelope).",
)
async def update_record(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    data: UpdateRecordRequest,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_MODE_DESCRIPTION,
    ),
) -> dict[str, Any]:
    ensure_table_mutable(table_name)
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    updated = await record_service.update_record(
        ctx,
        record_id,
        data.data,
        user.id,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    return updated.data


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="record.delete",
    summary="Delete Record",
    description="Delete a record by primary key.",
)
async def delete_record(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_MODE_DESCRIPTION,
    ),
) -> Response:
    ensure_table_mutable(table_name)
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    await record_service.delete_record(
        ctx,
        record_id,
        user.id,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/bulk/create",
    response_model=DatastoreCountResponse,
    status_code=status.HTTP_200_OK,
    operation_id="record.bulk_create",
    summary="Bulk Create",
    description="Insert multiple records in one request. Returns the affected-row count.",
)
async def bulk_create_records(
    pod_id: UUID,
    table_name: str,
    data: BulkCreateRecordsRequest,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
) -> DatastoreCountResponse:
    ensure_table_mutable(table_name)
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    count = await record_service.bulk_create_records(
        ctx,
        data.records,
        user.id,
        upsert=data.upsert,
    )
    return DatastoreCountResponse(count=count)


@router.post(
    "/bulk/update",
    response_model=DatastoreCountResponse,
    status_code=status.HTTP_200_OK,
    operation_id="record.bulk_update",
    summary="Bulk Update",
    description=(
        "Update multiple records in one request (each item needs primary key). "
        "Returns the affected-row count."
    ),
)
async def bulk_update_records(
    pod_id: UUID,
    table_name: str,
    data: BulkUpdateRecordsRequest,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_MODE_DESCRIPTION,
    ),
) -> DatastoreCountResponse:
    ensure_table_mutable(table_name)
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    count = await record_service.bulk_update_records(
        ctx,
        data.records,
        user.id,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    return DatastoreCountResponse(count=count)


@router.post(
    "/bulk/delete",
    response_model=DatastoreCountResponse,
    status_code=status.HTTP_200_OK,
    operation_id="record.bulk_delete",
    summary="Bulk Delete",
    description="Delete multiple records by primary key values. Returns the affected-row count.",
)
async def bulk_delete_records(
    pod_id: UUID,
    table_name: str,
    data: BulkDeleteRecordsRequest,
    table_service: TableServiceDep,
    record_service: RecordServiceDep,
    user: CurrentUser,
    pod_ctx: PodContextDep,
    mode: RecordAccessMode | None = Query(
        default=None,
        description=_MODE_DESCRIPTION,
    ),
) -> DatastoreCountResponse:
    ensure_table_mutable(table_name)
    ctx = await _get_table_context(
        pod_id,
        table_name,
        table_service,
        pod_ctx,
    )
    count = await record_service.bulk_delete_records(
        ctx,
        data.record_ids,
        user.id,
        admin_mode=mode == RecordAccessMode.ADMIN,
    )
    return DatastoreCountResponse(count=count)
