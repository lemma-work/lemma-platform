"""Pod toolset: in-process, grant-checked access to the active pod's datastore.

Every tool runs under the agent's delegated-workload authorization (see
``pod_data_access``). ``pod_id`` comes from the run context, never from a tool
argument. Mutating operations the agent lacks a grant for return a structured
``needs_approval`` result instead of raising, so the model can re-issue the
action through ``request_approval``.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import (
    PodGetRecordsRequest,
    PodTablesRequest,
    PodWriteRecordRequest,
    QueryRequest,
)
from app.modules.agent.tools.pod.pod_data_access import (
    PodServices,
    empty_data_error,
)
from app.modules.agent.tools.pod.pod_common import has_meaningful_data, run_pod_tool
from app.modules.agent.tools.pod.pod_file_tools import (
    pod_get_file_url,
    pod_list_files,
    pod_read_file,
    pod_search_files,
    pod_view_document_pages,
    pod_write_file,
)
from app.modules.datastore.contracts import (
    TableContext,
)


def _table_summary(table: Any) -> JsonObject:
    return {
        "name": table.table_name,
        "description": getattr(table, "description", None),
        "primary_key": table.primary_key_column,
        "rls_enabled": table.enable_rls,
        "columns": [
            {
                "name": column.name,
                "type": column.type.value
                if hasattr(column.type, "value")
                else str(column.type),
                "required": column.required,
                "description": column.description,
                # ENUM columns only accept one of a fixed set; surface it here so
                # a valid record can be built from the schema alone, without
                # having to trip the validation error to learn the options.
                **(
                    {"options": getattr(column, "options", None)}
                    if getattr(column, "options", None)
                    else {}
                ),
            }
            for column in table.columns
        ],
    }


async def _table_context(services: PodServices, table_name: str) -> TableContext:
    table = await services.table.get_table(
        services.ctx.pod_id, table_name, services.ctx
    )
    schema_name = services.table.schema_manager.get_schema_name(services.ctx.pod_id)
    return TableContext.from_table_entity(table, schema_name, events_enabled=True)


# --- Tables -----------------------------------------------------------------


async def pod_tables(
    ctx: RunContext[BaseAgentContext],
    request: PodTablesRequest,
) -> JsonObject:
    """List the pod's tables with their column schemas, or describe one table.

    Omit ``table_name`` to list every table (each with columns, types, PK, RLS
    flag). Pass ``table_name`` to describe just that one.
    """

    async def op(services: PodServices) -> JsonObject:
        if request.table_name:
            table = await services.table.get_table(
                services.ctx.pod_id, request.table_name, services.ctx
            )
            return {"success": True, "table": _table_summary(table)}
        tables, _ = await services.table.list_tables(
            services.ctx.pod_id, services.ctx, limit=request.limit
        )
        return {"success": True, "tables": [_table_summary(t) for t in tables]}

    return await run_pod_tool(
        ctx.deps, tool_name="pod_tables", args=request.model_dump(), op=op
    )


async def pod_get_records(
    ctx: RunContext[BaseAgentContext],
    request: PodGetRecordsRequest,
) -> JsonObject:
    """Read records from a pod table.

    Pass ``record_id`` to fetch a single record; omit it to list records with
    optional ``filters`` and ``sorts``.
    """

    async def op(services: PodServices) -> JsonObject:
        table_ctx = await _table_context(services, request.table_name)
        if request.record_id is not None:
            record = await services.record.get_record(
                table_ctx, request.record_id, services.ctx.user_id
            )
            return {"success": True, "record": to_json_value(record.data)}
        records, total = await services.record.list_records(
            table_ctx,
            services.ctx.user_id,
            limit=request.limit,
            offset=request.offset,
            sorts=[(s.column, s.direction) for s in request.sorts] or None,
            filters=[(f.column, f.op, f.value) for f in request.filters] or None,
        )
        return {
            "success": True,
            "records": [to_json_value(record.data) for record in records],
            "total": total,
        }

    return await run_pod_tool(
        ctx.deps,
        tool_name="pod_get_records",
        args=request.model_dump(),
        op=op,
    )


async def pod_write_record(
    ctx: RunContext[BaseAgentContext],
    request: PodWriteRecordRequest,
) -> JsonObject:
    """Create, update, or delete a record in a pod table (requires record.write grant).

    - ``action="create"`` — needs ``data`` (column -> value).
    - ``action="update"`` — needs ``record_id`` and ``data``.
    - ``action="delete"`` — needs ``record_id``.
    """

    async def op(services: PodServices) -> JsonObject:
        if request.action in ("create", "update") and not has_meaningful_data(
            request.data
        ):
            # Guard against silent blank-row writes: an empty/all-null `data`
            # (a frequent failure on smaller models) used to pass the `is None`
            # check and create a row of only system columns. Reject it instead.
            # Naming the table's real columns is what makes the retry land: a
            # model that sent `{}` once tends to send it again (a dogfood run
            # burned three identical retries), and "must be non-empty" doesn't
            # say what to put there.
            return {
                "success": False,
                "error": await empty_data_error(services, request),
            }
        if request.action in ("update", "delete") and not request.record_id:
            return {
                "success": False,
                "error": f"`record_id` is required for action='{request.action}'.",
            }

        table_ctx = await _table_context(services, request.table_name)
        if request.action == "create":
            record = await services.record.create_record(
                table_ctx, dict(request.data or {}), services.ctx.user_id
            )
            return {"success": True, "record": to_json_value(record.data)}
        if request.action == "update":
            record = await services.record.update_record(
                table_ctx,
                request.record_id,
                dict(request.data or {}),
                services.ctx.user_id,
            )
            return {"success": True, "record": to_json_value(record.data)}
        deleted = await services.record.delete_record(
            table_ctx, request.record_id, services.ctx.user_id
        )
        return {"success": bool(deleted), "deleted": bool(deleted)}

    return await run_pod_tool(
        ctx.deps,
        tool_name="pod_write_record",
        args=request.model_dump(),
        op=op,
    )


async def pod_query(
    ctx: RunContext[BaseAgentContext],
    request: QueryRequest,
) -> JsonObject:
    """Run a read-only SQL query against the pod.

    Reads across tables (joins, aggregates, subqueries) including RLS-enabled
    tables; rows of an RLS table are always scoped to the agent's user (the same
    per-user view its other record reads get). Only a single read-only SELECT is
    allowed.
    """

    async def op(services: PodServices) -> JsonObject:
        rows, row_count, truncated = await services.record.execute_readonly_query(
            pod_id=services.ctx.pod_id,
            query=request.sql,
            user_id=services.ctx.user_id,
            table_service=services.table,
            ctx=services.ctx,
        )
        result: JsonObject = {
            "success": True,
            "rows": to_json_value(rows),
            # Not "total": this is how many rows came back, which is only the
            # total when nothing was cut. Reporting the capped count as a total
            # is how an agent tells someone they have 1000 orders when they have
            # forty thousand.
            "row_count": row_count,
            "truncated": truncated,
        }
        if truncated:
            result["note"] = (
                f"Only the first {row_count} rows are shown; the result was cut "
                "short by the row cap. Do not treat this count as a total -- "
                "narrow the query or aggregate in SQL (for example COUNT(*)) if "
                "you need one."
            )
        return result

    return await run_pod_tool(
        ctx.deps, tool_name="pod_query", args=request.model_dump(), op=op
    )


pod_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        pod_tables,
        pod_get_records,
        pod_write_record,
        pod_query,
        pod_list_files,
        pod_read_file,
        pod_write_file,
        pod_view_document_pages,
        pod_get_file_url,
        pod_search_files,
    ]
)
