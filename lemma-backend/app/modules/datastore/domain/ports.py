"""Datastore module ports (repositories and required services)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Callable, Optional, Protocol, Sequence, Tuple
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreTableEntity,
    DatastoreTableSummaryEntity,
)
from app.modules.datastore.domain.document_processing import DocumentExtraction
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileSearchResult,
    FileStatus,
    SearchMethod,
)


class DatastoreTableRepositoryPort(Protocol):
    async def create(self, entity: DatastoreTableEntity) -> DatastoreTableEntity: ...

    async def get(self, id: UUID) -> Optional[DatastoreTableEntity]: ...

    async def update(self, entity: DatastoreTableEntity) -> DatastoreTableEntity: ...

    async def delete_entity(self, entity: DatastoreTableEntity) -> bool: ...

    async def get_by_datastore_and_name(
        self, pod_id: UUID, table_name: str, ctx: Context | None = None
    ) -> Optional[DatastoreTableEntity]: ...

    async def list_by_datastore(
        self, pod_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[DatastoreTableEntity], Optional[str]]: ...

    async def list_visible_by_datastore(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreTableEntity], Optional[str]]: ...

    async def list_summaries_visible_by_datastore(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreTableSummaryEntity], Optional[str]]: ...


class DatastoreFileRepositoryPort(Protocol):
    async def create(self, entity: DatastoreFileEntity) -> DatastoreFileEntity: ...

    async def get(self, id: UUID) -> Optional[DatastoreFileEntity]: ...

    async def update(self, entity: DatastoreFileEntity) -> DatastoreFileEntity: ...

    async def delete(self, id: UUID) -> bool: ...

    async def delete_entity(self, entity: DatastoreFileEntity) -> bool: ...

    async def get_by_datastore(
        self,
        pod_id: UUID,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreFileEntity], Optional[str]]: ...

    async def list_visible_by_datastore(
        self,
        pod_id: UUID,
        ctx: Context,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreFileEntity], Optional[str]]: ...

    async def get_by_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context | None = None,
    ) -> Optional[DatastoreFileEntity]: ...

    async def get_descendants(
        self,
        pod_id: UUID,
        path_prefix: str,
    ) -> Sequence[DatastoreFileEntity]: ...

    async def get_all_by_datastore(
        self,
        pod_id: UUID,
        owner_user_id: UUID | None = None,
    ) -> Sequence[DatastoreFileEntity]: ...

    async def get_by_paths(
        self,
        pod_id: UUID,
        paths: Sequence[str],
    ) -> Sequence[DatastoreFileEntity]: ...

    async def filter_visible_ids(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        file_ids: Sequence[UUID],
    ) -> set[UUID]: ...

    async def list_stale_recovery_candidates(
        self,
        *,
        pending_cutoff: datetime,
        processing_cutoff: datetime,
        failed_cutoff: datetime | None = None,
        max_attempts: int = 3,
    ) -> Sequence[DatastoreFileEntity]: ...

    async def list_exhausted_recovery_candidates(
        self,
        *,
        processing_cutoff: datetime,
        failed_cutoff: datetime | None = None,
        max_attempts: int = 3,
    ) -> Sequence[DatastoreFileEntity]: ...

    async def bulk_update_status(
        self,
        *,
        file_ids: Sequence[UUID],
        status: FileStatus,
    ) -> int: ...

    async def bulk_mark_failed_permanent(
        self,
        *,
        file_ids: Sequence[UUID],
        error: str,
    ) -> int: ...

    async def mark_failed_permanent(self, file_id: UUID, *, error: str) -> bool: ...


class DatastoreSchemaPort(Protocol):
    # A sessionmaker for the per-pod datastore DB; call it to open a session
    # (`async with schema.session_factory() as session: ...`).
    session_factory: Callable[[], Any]

    def get_schema_name(self, pod_id: UUID) -> str: ...

    async def create_datastore_schema(self, pod_id: UUID) -> None: ...

    async def drop_datastore_schema(self, pod_id: UUID) -> None: ...

    async def create_table(
        self,
        pod_id: UUID,
        table_name: str,
        primary_key_column: str,
        columns: list[ColumnSchema],
        enable_rls: bool = True,
    ) -> None: ...

    async def drop_table(self, pod_id: UUID, table_name: str) -> None: ...

    async def add_column(
        self,
        pod_id: UUID,
        table_name: str,
        column: ColumnSchema,
        known_columns: set[str] | None = None,
    ) -> None: ...

    async def drop_column(
        self, pod_id: UUID, table_name: str, column_name: str
    ) -> None: ...

    async def set_table_rls(
        self, pod_id: UUID, table_name: str, enable: bool
    ) -> None: ...

    async def set_rls_context(
        self,
        session,
        user_id: UUID,
        *,
        is_pod_admin: bool = False,
    ) -> None: ...


class DatastoreRecordRepositoryPort(Protocol):
    async def create_record(self, ctx, data: dict, user_id: UUID): ...

    async def bulk_create_records(
        self, ctx, records: list[dict], user_id: UUID
    ) -> int: ...

    async def bulk_upsert_records(
        self, ctx, records: list[dict], user_id: UUID
    ) -> int: ...

    async def get_record(
        self,
        ctx,
        record_id,
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
    ): ...

    async def execute_readonly_query(
        self,
        pod_id: UUID,
        query: str,
        user_id: UUID,
        enable_rls: bool = True,
        is_pod_admin: bool = False,
    ) -> Tuple[list[dict], int]: ...

    async def list_records(
        self,
        ctx,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        sorts: list[tuple[str, str]] | None = None,
        filters: list[tuple[str, str, object]] | None = None,
        *,
        enforce_user_scope: bool = True,
    ) -> Tuple[list, int]: ...

    async def update_record(
        self,
        ctx,
        record_id,
        data: dict,
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
    ): ...

    async def delete_record(
        self,
        ctx,
        record_id,
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
    ) -> bool: ...

class DatastoreStoragePort(Protocol):
    async def upload_file(self, destination_blob_name: str, file_content: bytes) -> bool: ...

    async def download_file(self, source_blob_name: str) -> bytes: ...

    def iter_download(
        self, source_blob_name: str
    ) -> AsyncIterator[bytes]: ...

    async def get_signed_url(self, blob_name: str, expires_hours: int = 1) -> str: ...

    async def delete_file(self, blob_name: str) -> bool: ...

    async def delete_prefix(self, prefix: str) -> int: ...


class DocumentProcessorPort(Protocol):
    """The whole document-processing capability the datastore needs: turn a
    document into searchable, page-aware markdown + figures, and rasterize PDF
    pages to images. The default adapter is Kreuzberg-backed (extraction) plus
    an in-process PDF rasterizer; both responsibilities live behind this one
    port so the engine can be swapped/benchmarked as a unit."""

    async def extract(
        self,
        content: bytes | None,
        filename: str,
        *,
        mime_type: str | None = None,
        content_path: str | None = None,
    ) -> DocumentExtraction:
        """Extract from either in-memory ``content`` or an on-disk ``content_path``.

        ``content_path`` lets the caller stream a large file to a temp file and
        hand the path down, so the extractor can stream it (Kreuzberg) rather than
        holding the whole file — plus a multipart copy — in memory. Processors
        that must work in-process (markitdown/docling) read the path into bytes.
        Exactly one of ``content`` / ``content_path`` is provided.
        """
        ...

    def supports_page_rendering(self, mime_type: str | None, filename: str) -> bool: ...

    async def render_pages(
        self,
        pdf_content: bytes,
        page_numbers: list[int],
        *,
        dpi: int,
        max_long_edge: int,
        jpeg_quality: int,
    ) -> dict[int, bytes]: ...


class RerankerPort(Protocol):
    """Optional second-stage reranking of first-stage (hybrid) search results.
    The no-op adapter returns results unchanged."""

    async def rerank(
        self,
        query: str,
        results: list[DatastoreFileSearchResult],
        *,
        top_n: int,
    ) -> list[DatastoreFileSearchResult]: ...


class DatastoreSearchPort(Protocol):
    async def index_file_chunks(
        self,
        file_id: UUID,
        chunks: list[dict],
        metadata: dict | None = None,
    ) -> bool: ...

    async def remove_file(self, file_id: UUID) -> None: ...

    async def update_file_path(
        self,
        file_id: UUID,
        path: str,
        parent_path: str | None,
    ) -> None: ...

    async def search(
        self,
        query: str,
        limit: int = 10,
        method: SearchMethod = SearchMethod.HYBRID,
        scope_path: str | None = None,
        include_descendants: bool = True,
        visible_file_ids: set[UUID] | None = None,
    ) -> list[DatastoreFileSearchResult]: ...


class DatastoreSearchFactoryPort(Protocol):
    def __call__(self, pod_id: UUID) -> DatastoreSearchPort: ...


class DatastoreReindexQueuePort(Protocol):
    async def enqueue(
        self,
        *,
        file_id: UUID,
        pod_id: UUID,
        metadata: dict | None,
        defer_until: datetime | None = None,
    ) -> bool: ...
