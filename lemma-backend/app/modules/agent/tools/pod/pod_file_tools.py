"""The pod datastore's file half: write, list, read, render, link, search.

Separated from the table tools because they answer different questions and fail
differently. A table read is a query; a file read is a fetch that may be a PDF
the model cannot see, in which case `pod_view_document_pages` either hands the
model images directly or -- when the model has no vision -- delegates to one
that does and returns a description instead.

`pod_get_file_url` is the one to read carefully: it hands out a URL, so what it
returns is reachable by whoever the agent shows it to.
"""

from __future__ import annotations

from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.tools import RunContext

from app.composition.agent_datastore import build_file_app_url, build_object_url
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.file_reads import read_file_text, search_files
from app.modules.agent.tools.pod.models import (
    GetFileUrlRequest,
    PodListFilesRequest,
    PodReadFileRequest,
    PodWriteFileRequest,
    SearchFilesRequest,
    ViewDocumentPagesRequest,
)
from app.modules.agent.tools.pod.pod_common import (
    file_summary,
    resolve_pod_path,
    run_pod_tool,
    split_pod_path,
)
from app.modules.agent.tools.pod.pod_data_access import PodServices
from app.modules.agent.tools.pod.pod_paths import normalize_json_paths, to_me_path
from app.modules.agent.tools.vision_delegation import describe_document_pages
from app.modules.datastore.contracts import (
    DatastoreConflictError,
    DatastoreFileUpdateEntity,
)


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
        resolved_path = resolve_pod_path(ctx.deps, request.path)
        directory_path, name = split_pod_path(resolved_path)
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
                "path": to_me_path(entity.path, services.ctx.user_id),
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
                "path": to_me_path(updated.path, services.ctx.user_id),
                "size_bytes": updated.size_bytes,
                "created": False,
            }

    return await run_pod_tool(
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
        resolved_path = resolve_pod_path(ctx.deps, request.path)
        if request.recursive:
            tree = await services.file.get_directory_tree(
                services.ctx.pod_id,
                services.ctx,
                root_path=resolved_path,
                files_per_directory=request.files_per_directory,
            )
            return {
                "success": True,
                "tree": normalize_json_paths(to_json_value(tree), services.ctx.user_id),
            }
        files, cursor = await services.file.list_files(
            services.ctx.pod_id,
            services.ctx,
            directory_path=resolved_path,
            limit=request.limit,
        )
        return {
            "success": True,
            "files": [file_summary(f, services.ctx.user_id) for f in files],
            "next_cursor": cursor,
        }

    return await run_pod_tool(
        ctx.deps,
        tool_name="pod_list_files",
        args=request.model_dump(),
        op=op,
    )


async def pod_read_file(
    ctx: RunContext[BaseAgentContext],
    request: PodReadFileRequest,
) -> JsonObject:
    """Read a pod file's text.

    A file that has text of its own -- markdown, plain text, HTML, CSV, code,
    email -- is returned exactly as it is. A file that has none, because it is a
    binary document like a PDF or a DOCX, is returned as the markdown it was
    converted into at upload.

    Use ``pod_view_document_pages`` to *see* pages rather than read them.
    """

    async def op(services: PodServices) -> JsonObject:
        return await read_file_text(
            services, request, resolve_pod_path(ctx.deps, request.path)
        )

    return await run_pod_tool(
        ctx.deps, tool_name="pod_read_file", args=request.model_dump(), op=op
    )


async def pod_view_document_pages(
    ctx: RunContext[BaseAgentContext],
    request: ViewDocumentPagesRequest,
) -> "JsonObject | ToolReturn":
    """Render PDF pages as images so you can *see* them (layout, tables, figures).

    Pages are 1-based. Only PDFs can be rendered visually; for other document
    types use ``pod_read_file`` to read the page text.

    Set ``instructions`` to say what you need from the pages. If this agent's
    model reads images itself you get the page images inline; otherwise a vision
    model reads them and returns a description, so this tool works either way.
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
        for page in pages:
            url, _expires = await build_object_url(
                services.file.storage, page.storage_key
            )
            page_refs.append({"page_number": page.page_number, "url": url})

        # This tool used to hand BinaryContent to whatever model was running.
        # `view_image` was withheld from text-only models for exactly that
        # reason; this one was not, so a text-only model asked for a PDF page
        # and the provider rejected the entire request.
        if getattr(ctx.deps, "vision_mode", None) is not AgentVisionMode.DIRECT:
            return await describe_document_pages(
                ctx.deps,
                path=entity.path,
                pages=pages,
                page_refs=page_refs,
                instructions=request.instructions,
            )

        return ToolReturn(
            return_value={
                "success": True,
                "path": entity.path,
                "pages": page_refs,
                "rendered_pages": [p.page_number for p in pages if not p.cached],
                "cached_pages": [p.page_number for p in pages if p.cached],
            },
            content=[
                BinaryContent(data=page.jpeg_bytes, media_type="image/jpeg")
                for page in pages
            ],
        )

    return await run_pod_tool(
        ctx.deps,
        tool_name="pod_view_document_pages",
        args=request.model_dump(),
        op=op,
    )


async def pod_get_file_url(
    ctx: RunContext[BaseAgentContext],
    request: GetFileUrlRequest,
) -> JsonObject:
    """Get a URL for a pod file, to share a link or embed an image.

    ``app`` (default) returns an in-app link for a signed-in member plus a
    short-lived download url. ``public`` mints a signed link anyone can open —
    it expires and dies after ``max_hits`` downloads."""

    async def op(services: PodServices) -> JsonObject:
        if request.url_type == "public":
            (
                entity,
                signed_url,
                expires_at,
                max_hits,
            ) = await services.file.create_signed_url(
                services.ctx.pod_id,
                request.path,
                services.ctx,
                expires_seconds=request.expires_seconds,
                max_hits=request.max_hits,
            )
            return {
                "success": True,
                "path": to_me_path(entity.path, services.ctx.user_id),
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
            "path": to_me_path(entity.path, services.ctx.user_id),
            "url_type": "app",
            "url": url,
            "app_url": build_file_app_url(services.ctx.pod_id, entity.path),
            "expires_at": expires_at.isoformat(),
        }

    return await run_pod_tool(
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
        return await search_files(services, request)

    return await run_pod_tool(
        ctx.deps,
        tool_name="pod_search_files",
        args=request.model_dump(),
        op=op,
    )
