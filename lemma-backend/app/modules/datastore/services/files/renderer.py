"""On-demand PDF page rendering with a bucket-backed cache.

Renders only the pages an agent asks for, via the document processor, and caches
each rendered JPEG as a hidden child artifact next to the source file
(``…/.{name}/pages/page_NNNN.jpg``) so repeat access is a cheap download rather
than a re-render. The rasterization engine + its memory/concurrency discipline
live behind ``DocumentProcessorPort``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.config import datastore_settings
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError, DatastoreValidationError
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.ports import DatastoreStoragePort, DocumentProcessorPort
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_child_page_key,
    build_datastore_file_storage_key,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class RenderedPage:
    page_number: int
    jpeg_bytes: bytes
    cached: bool
    storage_key: str


class FilePageRenderer:
    def __init__(
        self,
        storage: DatastoreStoragePort,
        reader,
        document_processor: DocumentProcessorPort,
    ):
        self.storage = storage
        self.reader = reader
        self.document_processor = document_processor

    def is_pdf(self, entity: DatastoreFileEntity) -> bool:
        return self.document_processor.supports_page_rendering(
            entity.mime_type, entity.name
        )

    async def render_document_page_images(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        *,
        page_start: int,
        page_end: int | None = None,
        ctx: Context | None = None,
    ) -> tuple[DatastoreFileEntity, list[RenderedPage]]:
        # Authorize + resolve via the reader (raises if no access / not found).
        entity = await self.reader.get_file_by_path(
            pod_id, path, requester_user_id, ctx=ctx
        )
        if entity.is_folder:
            raise DatastoreValidationError("Folders cannot be rendered to images")
        if not self.is_pdf(entity):
            raise DatastoreValidationError(
                "Visual page rendering is only supported for PDFs; "
                f"'{entity.name}' is {entity.mime_type or 'an unknown type'}. "
                "Use the markdown projection (get_document_markdown) instead."
            )

        page_numbers = self._clamp_pages(page_start, page_end)

        results: dict[int, RenderedPage] = {}
        missing: list[int] = []
        for page_number in page_numbers:
            key = build_datastore_child_page_key(entity.pod_id, entity.path, page_number)
            cached = await self._load_cached(key)
            if cached is not None:
                results[page_number] = RenderedPage(page_number, cached, True, key)
            else:
                missing.append(page_number)

        if missing:
            rendered = await self._render_missing(entity, missing)
            for page_number, jpeg in rendered.items():
                key = build_datastore_child_page_key(
                    entity.pod_id, entity.path, page_number
                )
                await self._store_cached(key, jpeg, entity.path, page_number)
                results[page_number] = RenderedPage(page_number, jpeg, False, key)

        ordered = [results[p] for p in page_numbers if p in results]
        return entity, ordered

    def _clamp_pages(self, page_start: int, page_end: int | None) -> list[int]:
        start = max(1, page_start)
        end = page_end if page_end is not None else start
        if end < start:
            end = start
        end = min(end, start + datastore_settings.pdf_render_max_pages_per_call - 1)
        return list(range(start, end + 1))

    async def _load_cached(self, key: str) -> bytes | None:
        try:
            return await self.storage.download_file(key)
        except DatastoreObjectNotFoundError:
            return None
        except Exception:
            logger.debug(
                "Failed to load cached page image; will re-render",
                exc_info=True,
            )
            return None

    async def _store_cached(
        self, key: str, jpeg: bytes, path: str, page_number: int
    ) -> None:
        try:
            await self.storage.upload_file(key, jpeg)
        except Exception as exc:
            logger.warning(
                "Failed caching rendered page %s of %s: %s",
                page_number,
                path,
                exc,
            )

    async def _render_missing(
        self, entity: DatastoreFileEntity, page_numbers: list[int]
    ) -> dict[int, bytes]:
        source_key = build_datastore_file_storage_key(entity.pod_id, entity.path)
        pdf_content = await self.storage.download_file(source_key)
        return await self.document_processor.render_pages(
            pdf_content,
            page_numbers,
            dpi=datastore_settings.pdf_render_dpi,
            max_long_edge=datastore_settings.pdf_render_max_long_edge,
            jpeg_quality=datastore_settings.pdf_render_jpeg_quality,
        )
