"""Pod toolset: in-process, grant-checked access to the active pod's datastore.

Every tool runs under the agent's delegated-workload authorization (see
``pod_data_access``). ``pod_id`` comes from the run context, never from a tool
argument. Mutating operations the agent lacks a grant for return a structured
``needs_approval`` result instead of raising, so the model can re-issue the
action through ``request_approval``.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets import FunctionToolset

from app.core.authorization.permissions import Permissions
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_payload_limits import bounded_rows
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
from app.modules.agent.tools.pod.pod_outcome_tool import set_what_you_own
from app.modules.agent.tools.pod.pod_file_tools import (
    pod_edit_file,
    pod_get_file_url,
    pod_list_files,
    pod_read_file,
    pod_search_files,
    pod_view_document_pages,
    pod_write_file,
)
from app.modules.datastore.contracts import (
    TableContext,
    describe_column,
)


def _table_summary(table: Any) -> JsonObject:
    return {
        "name": table.table_name,
        "description": getattr(table, "description", None),
        "primary_key": table.primary_key_column,
        "rls_enabled": table.enable_rls,
        # Everything a write depends on -- length, allowed values, default,
        # uniqueness, the column it references, and whether its name must be
        # quoted in `pod_query` -- so a valid record or query can be built from
        # the schema alone rather than learned from the error.
        "columns": [describe_column(column).as_json() for column in table.columns],
    }


#: What reading and writing a table's records ask the authorizer for, in turn.
#: Named up front so an agent refused one is told about both and the person is
#: asked once (``run_pod_tool``'s ``needs``).
_READS_RECORDS = frozenset(
    {Permissions.DATASTORE_TABLE_READ, Permissions.DATASTORE_RECORD_READ}
)
_WRITES_RECORDS = frozenset(
    {Permissions.DATASTORE_TABLE_READ, Permissions.DATASTORE_RECORD_WRITE}
)
#: A delete is its own permission, and a destructive one: an agent asks before
#: it, whatever it may write.
_DELETES_RECORDS = frozenset(
    {Permissions.DATASTORE_TABLE_READ, Permissions.DATASTORE_RECORD_DELETE}
)


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
        shown, left_out = bounded_rows([to_json_value(r.data) for r in records])
        result: JsonObject = {"success": True, "records": shown, "total": total}
        if left_out:
            result["truncated"] = True
            result["note"] = _too_large_note(left_out, shown=len(shown))
        return result

    return await run_pod_tool(
        ctx.deps,
        tool_name="pod_get_records",
        args=request.model_dump(),
        op=op,
        needs=_READS_RECORDS,
    )


async def pod_write_record(
    ctx: RunContext[BaseAgentContext],
    request: PodWriteRecordRequest,
) -> JsonObject:
    """Create, update, or delete a record in a pod table.

    - ``action="create"`` — needs ``data`` (column -> value).
    - ``action="update"`` — needs ``record_id`` and ``data``.
    - ``action="delete"`` — needs ``record_id``. Deleting is destructive: it
      needs ``datastore.record.delete``, not ``record.write``, and asks the
      person for approval unless that was granted.
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
            # Keyed on the call: a resumed turn re-running this create under
            # its recorded id gets the row back, not a second one (KEYED).
            record = await services.record.create_record(
                table_ctx,
                dict(request.data or {}),
                services.ctx.user_id,
                idempotency_key=(
                    f"run:{ctx.deps.agent_run_id}:{ctx.tool_call_id}"
                    if ctx.deps.agent_run_id and ctx.tool_call_id
                    else None
                ),
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
        needs=_DELETES_RECORDS if request.action == "delete" else _WRITES_RECORDS,
    )


async def pod_query(
    ctx: RunContext[BaseAgentContext],
    request: QueryRequest,
) -> JsonObject:
    """Run a read-only SQL query against the pod.

    Reads across tables (joins, aggregates, subqueries) including RLS-enabled
    tables; rows of an RLS table are always scoped to the agent's user (the same
    per-user view its other record reads get). Only a single read-only SELECT is
    allowed. A column shown in double quotes, or with `quote_required`, must be
    written in double quotes here too: `SELECT "check" FROM audits`.
    """

    async def op(services: PodServices) -> JsonObject:
        rows, row_count, truncated = await services.record.execute_readonly_query(
            pod_id=services.ctx.pod_id,
            query=request.sql,
            user_id=services.ctx.user_id,
            table_service=services.table,
            ctx=services.ctx,
        )
        shown, left_out = bounded_rows([to_json_value(row) for row in rows])
        result: JsonObject = {
            "success": True,
            "rows": shown,
            # Not "total": this is how many rows came back, which is only the
            # total when nothing was cut. Reporting the capped count as a total
            # is how an agent tells someone they have 1000 orders when they have
            # forty thousand.
            "row_count": len(shown),
            "truncated": truncated or bool(left_out),
        }
        if left_out:
            result["note"] = _too_large_note(left_out, shown=len(shown))
        elif truncated:
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


def _too_large_note(left_out: int, *, shown: int) -> str:
    """Said when rows were cut for size, which the row cap never sees.

    A row count bounds how many rows come back, not how large they are: rows
    with a wide text or JSON column are megabytes long, and whatever a tool
    returns rides in the conversation for the rest of the run.
    """
    return (
        f"Only the first {shown} rows are shown: {left_out} more matched but "
        "would have made the result too large to carry. Do not treat this as a "
        "total -- select fewer or narrower columns, page with a smaller limit, "
        "or aggregate in SQL."
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
        pod_edit_file,
        pod_view_document_pages,
        pod_get_file_url,
        pod_search_files,
        # Behind ToolSearch on its own: setting what the pod owns happens in the
        # setup chat and seldom after, so it carries no schema on every turn.
        # In this toolset rather than one of its own so it reaches the pod MCP
        # surface and every agent with POD, like the rest.
        Tool(set_what_you_own, defer_loading=True),
    ]
)
