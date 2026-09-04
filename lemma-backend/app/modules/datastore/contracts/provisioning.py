"""What another module does to a pod's tables, rows and files when it builds one.

Twelve operations, not the three service classes the composition root used to
forward. Handing out `TableService`/`RecordService`/`DatastoreFileService` made
every caller's build step depend on how datastore wires its own
collaborators, and two of the things
the bundle importer needs were reachable only by walking off a service: the
schema name for a `TableContext` came out of `table_service.schema_manager`, and
"does this file exist" was a `get_file_by_path` call wrapped in a bare
`except Exception` by the caller, which answers "no such file" when the truth is
"you may not read it" -- and the caller responds to that by creating a second
one.

Absence is answered here, by the module that owns the error that means it.
`get_table` and `file_exists` catch the one typed not-found and nothing else; a
denial or a downed database still raises, because retrying an import is
recoverable and silently duplicating every resource in it is not.

A submodule for the same reason as its siblings in `schedule` and `connectors`:
these reach the service layer, and `contracts/__init__` is imported by anything
that wants any contract at all.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.api.dependencies import (
    build_file_service,
    build_record_service,
    build_table_service,
    get_schema_manager,
)
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreTableEntity,
)
from app.modules.datastore.domain.errors import (
    DatastoreFileNotFoundError,
    DatastoreTableNotFoundError,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.table_context import TableContext


async def list_table_names(uow, *, pod_id: UUID, ctx: Context) -> list[str]:
    """Every table in the pod this reader may see."""
    tables, _ = await build_table_service(uow).list_tables(pod_id, ctx, limit=1000)
    return [str(table.name or "") for table in tables]


async def get_table(
    uow, *, pod_id: UUID, name: str, ctx: Context
) -> DatastoreTableEntity | None:
    """The named table, or ``None`` when the pod does not have one."""
    try:
        return await build_table_service(uow).get_table(pod_id, name, ctx)
    except DatastoreTableNotFoundError:
        return None


async def create_table(
    uow,
    *,
    pod_id: UUID,
    name: str,
    primary_key_column: str,
    columns: list[ColumnSchema],
    config: dict[str, object] | None,
    enable_rls: bool,
    visibility: str | None,
    ctx: Context,
) -> DatastoreTableEntity:
    """Create a table with its columns."""
    return await build_table_service(uow).create_table(
        pod_id,
        name,
        primary_key_column,
        columns,
        config,
        enable_rls,
        visibility=visibility,
        ctx=ctx,
    )


async def add_table_column(
    uow, *, pod_id: UUID, table_name: str, column: ColumnSchema, ctx: Context
) -> DatastoreTableEntity:
    """Add one column to an existing table."""
    return await build_table_service(uow).add_column(pod_id, table_name, column, ctx)


async def remove_table_column(
    uow, *, pod_id: UUID, table_name: str, column_name: str, ctx: Context
) -> DatastoreTableEntity:
    """Drop one column, and the data in it."""
    return await build_table_service(uow).remove_column(
        pod_id, table_name, column_name, ctx
    )


def _table_context(pod_id: UUID, table: DatastoreTableEntity) -> TableContext:
    """Address a table's rows without record events.

    Both row operations below are bulk machine writes against a table nobody is
    watching yet, and a seeded import would otherwise publish one change event
    per row.
    """
    return TableContext.from_table_entity(
        table, get_schema_manager().get_schema_name(pod_id), events_enabled=False
    )


async def seed_table_rows(
    uow,
    *,
    pod_id: UUID,
    table: DatastoreTableEntity,
    rows: list[dict[str, object]],
    user_id: UUID,
) -> int:
    """Upsert rows by primary key, returning how many were written.

    Upsert rather than insert so a caller replaying the same rows after a crash
    converges instead of raising on duplicates.
    """
    return await build_record_service(uow).bulk_create_records(
        _table_context(pod_id, table), rows, user_id, upsert=True
    )


async def read_table_rows(
    uow,
    *,
    pod_id: UUID,
    table: DatastoreTableEntity,
    user_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, object]], int]:
    """One page of a table's rows, with the total available behind it."""
    items, total = await build_record_service(uow).list_records(
        _table_context(pod_id, table), user_id, limit=limit, offset=offset
    )
    return [dict(item.data) for item in items], int(total or 0)


async def file_exists(uow, *, pod_id: UUID, path: str, ctx: Context) -> bool:
    """Whether the pod already holds a file or folder at this path."""
    try:
        await build_file_service(uow).get_file_by_path(pod_id, path, ctx)
    except DatastoreFileNotFoundError:
        return False
    return True


async def list_files(
    uow,
    *,
    pod_id: UUID,
    ctx: Context,
    directory_path: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[DatastoreFileEntity], str | None]:
    """One page of a directory's immediate children."""
    items, next_cursor = await build_file_service(uow).list_files(
        pod_id, ctx, directory_path=directory_path, limit=limit, cursor=cursor
    )
    return list(items), next_cursor


async def download_file(uow, *, pod_id: UUID, path: str, ctx: Context) -> bytes:
    """The bytes of the file at this path."""
    _entity, content = await build_file_service(uow).download_file_content_by_path(
        pod_id, path, ctx
    )
    return content


async def create_folder(
    uow,
    *,
    pod_id: UUID,
    path: str,
    ctx: Context,
    description: str | None,
    visibility: str | None,
) -> DatastoreFileEntity:
    """Create one folder. Its parent must already exist."""
    return await build_file_service(uow).create_folder(
        pod_id, path, ctx, description=description, visibility=visibility
    )


async def create_file(
    uow,
    *,
    pod_id: UUID,
    name: str,
    content: bytes | Path,
    ctx: Context,
    description: str | None,
    directory_path: str,
    search_enabled: bool,
    visibility: str | None,
) -> DatastoreFileEntity:
    """Create one file in an existing directory."""
    return await build_file_service(uow).create_file(
        pod_id,
        name,
        content,
        ctx,
        description=description,
        directory_path=directory_path,
        search_enabled=search_enabled,
        visibility=visibility,
    )


__all__ = [
    "add_table_column",
    "create_file",
    "create_folder",
    "create_table",
    "download_file",
    "file_exists",
    "get_table",
    "list_files",
    "list_table_names",
    "read_table_rows",
    "remove_table_column",
    "seed_table_rows",
]
