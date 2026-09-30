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

from typing import Protocol

from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.tools import RunContext

from app.modules.datastore.contracts.agent_tools import (
    build_file_app_url,
    build_object_url,
)
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.services.agent_memory_brief import invalidate_memory_brief
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.file_reads import read_file_text, search_files
from app.modules.agent.tools.pod.models import (
    FileEdit,
    GetFileUrlRequest,
    PodEditFileRequest,
    PodListFilesRequest,
    PodReadFileRequest,
    PodWriteFileRequest,
    SearchFilesRequest,
    ViewDocumentPagesRequest,
)
from app.core.infrastructure.db.transaction_locks import connection_released
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


async def _after_memory_write(
    services: PodServices,
    *,
    agent_name: str | None,
    stored_path: str,
    content_length: int,
) -> JsonObject:
    """Bookkeeping for a write that may have landed in the agent's memory.

    Two things the file service cannot do for us. First, drop the cached memory
    section so a fact written this turn is in the brief on the next one rather
    than up to a TTL later -- this is the reliable half of that invalidation,
    inline and in-process; the stream subscriber covers writers that never
    reach here. Second, tell the agent when it has written an auto-loaded index
    past the point where the prompt will carry all of it, which is the one
    thing the read-side truncation cannot say back to whoever caused it.

    Returns extra keys to merge into the tool result -- empty for the ordinary
    write, which is almost all of them.
    """
    user_id = services.ctx.user_id
    await invalidate_memory_brief(
        pod_id=services.ctx.pod_id, path=stored_path, user_id=user_id
    )
    # From the run context, not `services.ctx`: that one is the authorization
    # context, which knows the principal but not the agent's name.
    paths = agent_memory_paths_for_name(agent_name)
    indexes = {
        paths.pod_index,
        paths.pod_agent_index,
        paths.personal_index,
        paths.personal_agent_index,
    }
    limit = agent_settings.agent_memory_index_max_chars
    if to_me_path(stored_path, user_id) not in indexes or content_length <= limit:
        return {}
    return {
        "warning": (
            f"This index is {content_length} characters; only the first {limit} "
            "reach your Runtime Context, and the rest is truncated on every "
            "run. Move the detail into a topic file and leave a one-line "
            "pointer here."
        )
    }


class _StoredFile(Protocol):
    """What a write reports back: where the file now lives and how big it is."""

    path: str
    size_bytes: int


async def _overwrite(
    services: PodServices,
    resolved_path: str,
    content_bytes: bytes,
    description: str | None,
) -> _StoredFile:
    update_entity = DatastoreFileUpdateEntity(
        path=resolved_path, content=content_bytes, description=description
    )
    plan = await services.file.resolve_update_file(
        services.ctx.pod_id, update_entity, services.ctx
    )
    await services.file.write_update_storage(plan, update_entity)
    updated = await services.file.persist_update_file(plan)
    await services.file.finalize_update_file(plan, updated)
    return updated


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
                **await _after_memory_write(
                    services,
                    agent_name=ctx.deps.agent_name,
                    stored_path=entity.path,
                    content_length=len(request.content),
                ),
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
            updated = await _overwrite(
                services, resolved_path, content_bytes, request.description
            )
            return {
                "success": True,
                "path": to_me_path(updated.path, services.ctx.user_id),
                "size_bytes": updated.size_bytes,
                "created": False,
                **await _after_memory_write(
                    services,
                    agent_name=ctx.deps.agent_name,
                    stored_path=updated.path,
                    content_length=len(request.content),
                ),
            }

    return await run_pod_tool(
        ctx.deps, tool_name="pod_write_file", args=request.model_dump(), op=op
    )


def _apply_edits(text: str, edits: list[FileEdit]) -> tuple[str, int] | str:
    """Apply the replacements in order, or say which one could not be placed."""
    replaced = 0
    for number, edit in enumerate(edits, start=1):
        count = text.count(edit.old_text)
        if count == 0:
            return (
                f"Edit {number}: `old_text` is not in the file. Copy it exactly "
                "from the file, including whitespace; nothing was changed."
            )
        if count > 1 and not edit.replace_all:
            return (
                f"Edit {number}: `old_text` appears {count} times. Include a "
                "neighbouring line to make it unique, or set replace_all; "
                "nothing was changed."
            )
        text = text.replace(edit.old_text, edit.new_text)
        replaced += count
    return text, replaced


async def pod_edit_file(
    ctx: RunContext[BaseAgentContext],
    request: PodEditFileRequest,
) -> JsonObject:
    """Change part of an existing pod text file by exact-text replacement.

    The way to edit a doc, a page or any file you did not just write: name the
    text to replace and what goes there, and the rest of the file is kept byte
    for byte. Every edit is placed before anything is written, so either all of
    them land or none do. No need to download the file or rewrite it whole.
    """

    async def op(services: PodServices) -> JsonObject:
        resolved_path = resolve_pod_path(ctx.deps, request.path)
        entity, content = await services.file.download_file_content_by_path(
            services.ctx.pod_id, resolved_path, services.ctx
        )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "success": False,
                "path": resolved_path,
                "error": "This file is not text; it cannot be edited in place.",
            }
        applied = _apply_edits(text, request.edits)
        if isinstance(applied, str):
            return {"success": False, "path": resolved_path, "error": applied}
        new_text, replaced = applied
        updated = await _overwrite(
            services, entity.path, new_text.encode("utf-8"), None
        )
        return {
            "success": True,
            "path": to_me_path(updated.path, services.ctx.user_id),
            "size_bytes": updated.size_bytes,
            "replacements": replaced,
            **await _after_memory_write(
                services,
                agent_name=ctx.deps.agent_name,
                stored_path=updated.path,
                content_length=len(new_text),
            ),
        }

    return await run_pod_tool(
        ctx.deps, tool_name="pod_edit_file", args=request.model_dump(), op=op
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

        # One Redis lookup per page, and on GCS a signing round trip each, for
        # up to `pdf_render_max_pages_per_call` pages — all of it with nothing
        # left to ask the database. `build_file_url` grew a `session` parameter
        # for exactly this; this caller reaches `build_object_url` directly, so
        # it releases the connection itself.
        page_refs = []
        async with connection_released(services.uow.session):
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
            # A vision model reads the pages, which is a model round trip on a
            # document -- seconds, and the slowest thing this tool does. Every
            # platform read is finished by now and nothing below touches that
            # database, so the connection goes back before the call rather than
            # sitting idle in an open transaction for the length of it.
            #
            # This was the worst single hold in a week of production: 47.2s
            # held, 47.1s of it idle, 24ms of querying, one statement.
            async with connection_released(services.uow.session):
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
