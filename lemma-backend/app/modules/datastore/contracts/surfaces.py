"""What a chat surface reads and writes in a pod's datastore.

Six operations, not the three service *factories* `app/composition/surface_datastore.py`
handed out. `build_table_service` was the worse half: a table preview needed a
`TableContext`, and the only way to build one was
`table_service.schema_manager.get_schema_name(pod_id)` -- a collaborator reached
off a service, in another module, to learn a schema name that datastore alone
should have to know. `agent_surfaces` also had to hold `TableContext` itself,
for one call.

The file half is the same shape as `provisioning`'s, with the two reads that
provisioning has no use for: a surface card shows what a document *looks* like,
so it renders a page to an image, and it needs the entity before the bytes
because a file over the platform's cap is described rather than attached.

A submodule for the same reason as its siblings here and in `schedule` and
`connectors`: these reach the service layer, and `contracts/__init__` is imported
by anything that wants any contract at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.api.dependencies import (
    build_file_service,
    build_record_service,
    build_table_service,
    get_schema_manager,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.table_context import TableContext


@dataclass(frozen=True, slots=True)
class TableRows:
    """One page of a table, as a card renders it.

    ``total`` is ``None`` when the number is not knowable -- an ad-hoc query
    that hit its row cap counts what it returned, not what exists, and a card
    that printed that number would state a row count that is simply wrong.
    """

    rows: list[dict[str, object]]
    total: int | None
    #: Column order for the preview table; ``None`` for an ad-hoc query, whose
    #: columns are whatever the statement selected.
    columns: list[str] | None


async def read_pod_file(
    uow, *, pod_id: UUID, path: str, ctx: Context
) -> DatastoreFileEntity:
    """The file at this path, without its bytes.

    Separate from the download because size and MIME type decide whether the
    bytes may be fetched at all: a platform caps an image and a document
    differently, and an oversize file is described rather than attached.
    """
    return await build_file_service(uow).get_file_by_path(pod_id, path, ctx)


async def download_pod_file(
    uow, *, pod_id: UUID, path: str, ctx: Context
) -> tuple[DatastoreFileEntity, bytes]:
    """The file at this path, with its bytes."""
    return await build_file_service(uow).download_file_content_by_path(
        pod_id, path, ctx
    )


async def render_pod_file_page(
    uow, *, pod_id: UUID, path: str, ctx: Context, page: int
) -> bytes | None:
    """One page of a document as a JPEG, or ``None`` when it has no such page."""
    _entity, pages = await build_file_service(uow).render_document_page_images(
        pod_id, path, ctx, page_start=page
    )
    return pages[0].jpeg_bytes if pages else None


async def create_pod_file(
    uow,
    *,
    pod_id: UUID,
    name: str,
    content: bytes,
    ctx: Context,
    directory_path: str,
    search_enabled: bool = True,
) -> DatastoreFileEntity:
    """Store one inbound attachment as a pod file."""
    return await build_file_service(uow).create_file(
        pod_id,
        name,
        content,
        ctx,
        directory_path=directory_path,
        search_enabled=search_enabled,
    )


async def read_table_preview(
    uow,
    *,
    pod_id: UUID,
    table_name: str,
    user_id: UUID,
    ctx: Context,
    limit: int,
    filters: list[tuple[str, str, object]] | None = None,
) -> TableRows:
    """The first rows of a named table, with the count behind them.

    Events off, because a preview is a read and the surface is not writing
    anything -- and `TableContext`, the schema name it needs, and the table
    lookup that produces it all stay on this side of the boundary.
    """
    table = await build_table_service(uow).get_table(pod_id, table_name, ctx)
    table_ctx = TableContext.from_table_entity(
        table, get_schema_manager().get_schema_name(pod_id), events_enabled=False
    )
    records, total = await build_record_service(uow).list_records(
        table_ctx, user_id, limit=limit, filters=filters or None
    )
    return TableRows(
        rows=[dict(record.data) for record in records],
        total=total,
        columns=[column.name for column in getattr(table, "columns", [])] or None,
    )


async def run_readonly_query(
    uow, *, pod_id: UUID, query: str, user_id: UUID, ctx: Context, limit: int
) -> TableRows:
    """Rows for an ad-hoc read-only statement, capped at ``limit`` for display.

    ``total`` comes back ``None`` on a truncated result: see :class:`TableRows`.
    """
    rows, total, truncated = await build_record_service(uow).execute_readonly_query(
        pod_id=pod_id,
        query=query,
        user_id=user_id,
        table_service=build_table_service(uow),
        ctx=ctx,
    )
    return TableRows(
        rows=[dict(row) for row in rows[:limit]],
        total=None if truncated else total,
        columns=None,
    )


__all__ = [
    "TableRows",
    "create_pod_file",
    "download_pod_file",
    "read_pod_file",
    "read_table_preview",
    "render_pod_file_page",
    "run_readonly_query",
]
