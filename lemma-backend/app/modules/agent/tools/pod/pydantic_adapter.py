"""Pod toolset: in-process, grant-checked access to the active pod's datastore.

Every tool runs under the agent's delegated-workload authorization (see
``pod_data_access``). ``pod_id`` comes from the run context, never from a tool
argument. Mutating operations the agent lacks a grant for return a structured
``needs_approval`` result instead of raising, so the model can re-issue the
action through ``request_approval``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import (
    GetFileUrlRequest,
    PodGetRecordsRequest,
    PodListFilesRequest,
    PodReadFileRequest,
    PodTablesRequest,
    PodWriteFileRequest,
    PodWriteRecordRequest,
    QueryRequest,
    SearchFilesRequest,
    ViewDocumentPagesRequest,
)
from app.modules.agent.tools.pod.pod_data_access import PodServices, pod_services
from app.modules.agent.tools.tool_errors import approval_error_result
from app.modules.datastore.domain.errors import DatastoreConflictError
from app.modules.datastore.domain.file_entities import DatastoreFileUpdateEntity
from app.modules.datastore.services.files.file_url import (
    build_file_app_url,
    build_object_url,
)
from app.modules.datastore.services.table_context import TableContext


def _resolve_pod_path(deps: BaseAgentContext, path: str) -> str:
    """Resolve a possibly-relative pod path against the agent's pod cwd."""
    if path.startswith("/"):
        return path
    cwd = deps.get_pod_cwd().rstrip("/")
    return f"{cwd}/{path}" if path else cwd


def _split_pod_path(path: str) -> tuple[str, str]:
    """Split an absolute pod path into (directory_path, name)."""
    normalized = path if path.startswith("/") else f"/{path}"
    trimmed = normalized.rstrip("/") or "/"
    if trimmed == "/":
        raise ValueError("A file path must include a file name.")
    directory, _, name = trimmed.rpartition("/")
    return (directory or "/", name)


def _has_meaningful_data(data: JsonObject | None) -> bool:
    """True if ``data`` has at least one non-null, non-blank value.

    Rejects ``None``, ``{}``, and payloads whose values are all null or empty/
    whitespace strings — the shapes that would otherwise write a blank row. ``0``
    and ``False`` are real values and count as meaningful.
    """
    if not data:
        return False
    for value in data.values():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


async def _run(
    deps: BaseAgentContext,
    *,
    tool_name: str,
    args: JsonObject,
    op: Callable[[PodServices], Awaitable["JsonObject | ToolReturn"]],
) -> "JsonObject | ToolReturn":
    """Run a pod operation, mapping authorization 403s to ``needs_approval``.

    Most ops return a ``JsonObject``; image tools return a ``ToolReturn`` so the
    model receives inline image content while only a reference is persisted.
    """
    try:
        async with pod_services(deps) as services:
            return await op(services)
    except DomainError as exc:
        return approval_error_result(exc, tool_name=tool_name, args=args)


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
            }
            for column in table.columns
        ],
    }


def _file_summary(entity: Any) -> JsonObject:
    """Curated view of a file for listings — surfaces whether it's an indexed
    document and how many pages it has, so the agent knows what to read/view."""
    metadata = getattr(entity, "metadata", None) or {}
    status = getattr(entity, "status", None)
    status_value = status.value if hasattr(status, "value") else status
    kind = getattr(entity, "kind", None)
    kind_value = kind.value if hasattr(kind, "value") else kind
    return {
        "path": entity.path,
        "name": entity.name,
        "kind": kind_value,
        "mime_type": getattr(entity, "mime_type", None),
        "size_bytes": getattr(entity, "size_bytes", None),
        "status": status_value,
        "indexed": status_value == "COMPLETED",
        "page_count": metadata.get("page_count"),
        "has_markdown": metadata.get("has_markdown", False),
        "description": getattr(entity, "description", None),
    }


async def _table_context(services: PodServices, table_name: str) -> TableContext:
    table = await services.table.get_table(services.ctx.pod_id, table_name, services.ctx)
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

    return await _run(
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

    return await _run(
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
        if request.action in ("create", "update") and not _has_meaningful_data(
            request.data
        ):
            # Guard against silent blank-row writes: an empty/all-null `data`
            # (a frequent failure on smaller models) used to pass the `is None`
            # check and create a row of only system columns. Reject it instead.
            return {
                "success": False,
                "error": (
                    f"`data` must be a non-empty object of column->value for "
                    f'action=\'{request.action}\', e.g. {{"title": "..."}}. The '
                    "payload was empty, so nothing was written."
                ),
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

    return await _run(
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
        rows, total = await services.record.execute_readonly_query(
            pod_id=services.ctx.pod_id,
            query=request.sql,
            user_id=services.ctx.user_id,
            table_service=services.table,
            ctx=services.ctx,
        )
        return {"success": True, "rows": to_json_value(rows), "total": total}

    return await _run(
        ctx.deps, tool_name="pod_query", args=request.model_dump(), op=op
    )


# --- Files ------------------------------------------------------------------


async def pod_write_file(
    ctx: RunContext[BaseAgentContext],
    request: PodWriteFileRequest,
) -> JsonObject:
    """Write text content to a pod file, creating or overwriting it.

    Without an absolute path, the file lands in your default pod working
    directory (`/me/c/{date}/{slug}`) — a stable, private location scoped to
    this conversation. Writes under your own `/me/...` (including that default
    location) never need approval; writes to a shared pod path may.
    """

    async def op(services: PodServices) -> JsonObject:
        resolved_path = _resolve_pod_path(ctx.deps, request.path)
        directory_path, name = _split_pod_path(resolved_path)
        content_bytes = request.content.encode("utf-8")
        try:
            entity = await services.file.create_file(
                services.ctx.pod_id,
                name,
                content_bytes,
                services.ctx,
                description=request.description,
                directory_path=directory_path,
            )
            return {
                "success": True,
                "path": entity.path,
                "size_bytes": entity.size_bytes,
                "created": True,
            }
        except DatastoreConflictError:
            if not request.overwrite:
                return {
                    "success": False,
                    "path": resolved_path,
                    "error": (
                        f"A file already exists at '{resolved_path}'. Pass "
                        "overwrite=true to replace it."
                    ),
                }
            update_entity = DatastoreFileUpdateEntity(
                path=resolved_path,
                content=content_bytes,
                description=request.description,
            )
            plan = await services.file.resolve_update_file(
                services.ctx.pod_id, update_entity, services.ctx
            )
            await services.file.write_update_storage(plan, update_entity)
            updated = await services.file.persist_update_file(plan)
            await services.file.finalize_update_file(plan, updated)
            return {
                "success": True,
                "path": updated.path,
                "size_bytes": updated.size_bytes,
                "created": False,
            }

    return await _run(
        ctx.deps, tool_name="pod_write_file", args=request.model_dump(), op=op
    )


async def pod_list_files(
    ctx: RunContext[BaseAgentContext],
    request: PodListFilesRequest,
) -> JsonObject:
    """List pod files under a path.

    ``recursive=false`` (default) lists the immediate files and folders in
    ``path``. ``recursive=true`` returns a file tree rooted at ``path`` (folders
    plus a sample of files per directory). Without an absolute path, resolves
    against your default pod working directory (`/me/c/{date}/{slug}`).
    """

    async def op(services: PodServices) -> JsonObject:
        resolved_path = _resolve_pod_path(ctx.deps, request.path)
        if request.recursive:
            tree = await services.file.get_directory_tree(
                services.ctx.pod_id,
                services.ctx,
                root_path=resolved_path,
                files_per_directory=request.files_per_directory,
            )
            return {"success": True, "tree": to_json_value(tree)}
        files, cursor = await services.file.list_files(
            services.ctx.pod_id,
            services.ctx,
            directory_path=resolved_path,
            limit=request.limit,
        )
        return {
            "success": True,
            "files": [_file_summary(f) for f in files],
            "next_cursor": cursor,
        }

    return await _run(
        ctx.deps,
        tool_name="pod_list_files",
        args=request.model_dump(),
        op=op,
    )


async def pod_read_file(
    ctx: RunContext[BaseAgentContext],
    request: PodReadFileRequest,
) -> JsonObject:
    """Read a pod file's content as text or as converted document markdown.

    - ``format="text"`` (default) — the file's raw UTF-8 text, truncated to
      ``max_chars``.
    - ``format="markdown"`` — converted markdown for a document (PDF, DOCX, ...),
      optionally a 1-based page range (``page_start``/``page_end``).

    Documents are auto-converted to markdown at upload and cached, so reading is
    instant — read them here directly; do not download and re-parse/OCR them.
    ``pod_list_files`` reports ``has_markdown``/``page_count`` so you know what's
    ready. To SEE pages visually (layout, tables, figures), use
    ``pod_view_document_pages``.
    """

    async def op(services: PodServices) -> JsonObject:
        resolved_path = _resolve_pod_path(ctx.deps, request.path)
        if request.format == "markdown":
            entity, markdown, page_count = await services.file.get_document_markdown(
                services.ctx.pod_id,
                resolved_path,
                services.ctx,
                page_start=request.page_start,
                page_end=request.page_end,
            )
            return {
                "success": True,
                "path": entity.path,
                "format": "markdown",
                "page_count": page_count,
                "page_start": request.page_start,
                "page_end": request.page_end,
                "truncated": len(markdown) > request.max_chars,
                "markdown": markdown[: request.max_chars],
            }

        entity, content = await services.file.download_file_content_by_path(
            services.ctx.pod_id, resolved_path, services.ctx
        )
        try:
            text = content.decode("utf-8")
            return {
                "success": True,
                "path": entity.path,
                "format": "text",
                "mime_type": entity.mime_type,
                "size_bytes": entity.size_bytes,
                "truncated": len(text) > request.max_chars,
                "text": text[: request.max_chars],
            }
        except UnicodeDecodeError:
            return {
                "success": True,
                "path": entity.path,
                "mime_type": entity.mime_type,
                "size_bytes": entity.size_bytes,
                "binary": True,
                "hint": "Binary file; read it with format='markdown' for documents.",
            }

    return await _run(
        ctx.deps, tool_name="pod_read_file", args=request.model_dump(), op=op
    )


async def pod_view_document_pages(
    ctx: RunContext[BaseAgentContext],
    request: ViewDocumentPagesRequest,
) -> "JsonObject | ToolReturn":
    """Render PDF pages as images so you can *see* them (layout, tables, figures).

    Pages are 1-based. Only PDFs can be rendered visually; for other document
    types use ``pod_read_file`` with ``format="markdown"`` to read the page text.
    You receive the page images inline; the transcript keeps only short-lived URLs.
    """

    async def op(services: PodServices) -> "JsonObject | ToolReturn":
        entity, pages = await services.file.render_document_page_images(
            services.ctx.pod_id,
            request.path,
            services.ctx,
            page_start=request.page_start,
            page_end=request.page_end,
        )
        if not pages:
            return {
                "success": False,
                "path": entity.path,
                "error": "No pages rendered — the requested pages are out of range.",
            }

        page_refs = []
        content: list[Any] = []
        for page in pages:
            url, _expires = await build_object_url(
                services.file.storage, page.storage_key
            )
            page_refs.append({"page_number": page.page_number, "url": url})
            content.append(BinaryContent(data=page.jpeg_bytes, media_type="image/jpeg"))

        return ToolReturn(
            return_value={
                "success": True,
                "path": entity.path,
                "pages": page_refs,
                "rendered_pages": [p.page_number for p in pages if not p.cached],
                "cached_pages": [p.page_number for p in pages if p.cached],
            },
            content=content,
        )

    return await _run(
        ctx.deps,
        tool_name="pod_view_document_pages",
        args=request.model_dump(),
        op=op,
    )


async def pod_get_file_url(
    ctx: RunContext[BaseAgentContext],
    request: GetFileUrlRequest,
) -> JsonObject:
    """Get a URL for a pod file, to hand a user a link or embed an image.

    ``url_type="app"`` (default) returns an authenticated in-app link
    (``app_url``) for a signed-in pod member, plus a short-lived direct-download
    ``url``. ``url_type="public"`` mints a public, hit-capped ``signed_url``
    anyone can open without logging in (e.g. to email/message a file to someone
    outside the pod) — it expires and stops after ``max_hits`` downloads, so a
    leaked link can't run up egress."""

    async def op(services: PodServices) -> JsonObject:
        if request.url_type == "public":
            entity, signed_url, expires_at, max_hits = (
                await services.file.create_signed_url(
                    services.ctx.pod_id,
                    request.path,
                    services.ctx,
                    expires_seconds=request.expires_seconds,
                    max_hits=request.max_hits,
                )
            )
            return {
                "success": True,
                "path": entity.path,
                "url_type": "public",
                "signed_url": signed_url,
                "expires_at": expires_at.isoformat(),
                "max_hits": max_hits,
            }

        entity, url, expires_at = await services.file.get_file_url(
            services.ctx.pod_id,
            request.path,
            services.ctx,
            expires_seconds=request.expires_seconds,
        )
        return {
            "success": True,
            "path": entity.path,
            "url_type": "app",
            "url": url,
            "app_url": build_file_app_url(services.ctx.pod_id, entity.path),
            "expires_at": expires_at.isoformat(),
        }

    return await _run(
        ctx.deps,
        tool_name="pod_get_file_url",
        args=request.model_dump(),
        op=op,
    )


async def pod_search_files(
    ctx: RunContext[BaseAgentContext],
    request: SearchFilesRequest,
) -> JsonObject:
    """Semantic/keyword search across indexed pod files."""

    async def op(services: PodServices) -> JsonObject:
        results = await services.file.search_files(
            services.ctx.pod_id,
            request.query,
            services.ctx,
            limit=request.limit,
            search_method=request.method,
            scope_path=request.scope_path,
        )
        return {"success": True, "results": to_json_value(results)}

    return await _run(
        ctx.deps,
        tool_name="pod_search_files",
        args=request.model_dump(),
        op=op,
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
