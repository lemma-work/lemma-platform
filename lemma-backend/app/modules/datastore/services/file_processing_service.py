from __future__ import annotations

import json
import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.document_processing import (
    DocumentExtraction,
    DocumentImage,
    DocumentPage,
)
from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.domain.ports import DocumentProcessorPort
from app.modules.datastore.infrastructure.document_processor import (
    create_document_processor,
)
from app.modules.datastore.infrastructure.inflight_budget import (
    get_inflight_byte_budget,
)
from app.modules.datastore.infrastructure.markdown_chunker import chunk_markdown
from app.modules.datastore.infrastructure.streaming import stream_to_tempfile
from app.modules.datastore.infrastructure.markdown_images import (
    rewrite_image_references,
)
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_child_artifact_key,
    build_datastore_child_manifest_key,
    build_datastore_child_markdown_key,
    build_datastore_child_user_markdown_key,
    build_datastore_file_storage_key,
)
from app.modules.datastore.services.files.page_markers import parse_page_offsets
from app.modules.datastore.services.files.projection import FileProjection
from app.modules.datastore.services.search.postgres_search_service import (
    PostgresSearchService,
)
import logging

logger = logging.getLogger(__name__)

# Manifest format version for the colocated child container.
_MANIFEST_VERSION = 3

# ``file_metadata["markdown_source"]`` value marking a file whose agent-facing
# markdown is user-provided (bring-your-own) rather than engine-extracted. When
# set, the processor chunks/indexes the stored ``source.md`` and skips the
# document processor entirely.
_USER_MARKDOWN_SOURCE = "user"
# ``file_metadata["markdown_asset_names"]`` — basenames of the companion images
# the user uploaded with their markdown (stored as sibling child artifacts).
_MARKDOWN_ASSET_NAMES_KEY = "markdown_asset_names"

_CONVERTED_MARKDOWN_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.oasis.opendocument.text",
        "text/html",
        "text/rtf",
        "application/rtf",
        "application/epub+zip",
        "message/rfc822",
        "application/vnd.ms-outlook",
    }
)


class DatastoreFileProcessingService:
    def __init__(
        self,
        pod_id: UUID,
        *,
        uow_factory: UnitOfWorkFactory,
        search_service: PostgresSearchService | None = None,
        storage: DatastoreStoragePort | None = None,
        document_processor: DocumentProcessorPort | None = None,
    ):
        self.pod_id = pod_id
        self._uow_factory = uow_factory
        self.search_service = search_service or PostgresSearchService(pod_id)
        self.storage = storage or create_datastore_storage()
        self.document_processor = document_processor or create_document_processor()

    @asynccontextmanager
    async def _file_repo(self) -> AsyncIterator[DatastoreFileRepository]:
        """Yield a file repository scoped to a short-lived DB session.

        Each call opens a fresh UoW that commits and releases its pooled
        connection on exit — so no main DB connection is held during the slow
        storage/extraction I/O between repository calls.
        """
        async with self._uow_factory() as uow:
            yield DatastoreFileRepository(uow)

    @property
    def _file_projection(self) -> FileProjection:
        # Built per access so a reassigned ``self.storage`` (e.g. in tests) is
        # honoured. Single home for child-artifact deletion, shared with the
        # file writer.
        return FileProjection(self.storage, file_repository=None)

    def _base_mime_type(self, file_entity: DatastoreFile) -> str | None:
        mime_type = getattr(file_entity, "mime_type", None)
        if mime_type:
            return mime_type.split(";")[0].strip().lower()
        guessed, _ = mimetypes.guess_type(getattr(file_entity, "name", "") or "")
        return guessed.lower() if guessed else None

    def _should_store_converted_projection(self, file_entity: DatastoreFile) -> bool:
        mime_type = self._base_mime_type(file_entity)
        return mime_type in _CONVERTED_MARKDOWN_MIME_TYPES if mime_type else False

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        """Return a safe, user-facing error string for storage in the DB.

        The full exception (with HTTP bodies, object keys, SQL) is logged at
        error level; only a short class-based summary is persisted so file-status
        queries don't leak internal infrastructure details.
        """
        exc_name = type(exc).__name__
        message = str(exc).split("\n")[0].strip()
        if len(message) > 200:
            message = message[:200] + "..."
        return f"{exc_name}: {message}" if message else exc_name

    async def process_file_async(
        self,
        file_id: UUID,
        metadata: dict | None = None,
    ):
        await self._process(file_id, metadata or {})

    async def _process(self, file_id, metadata):
        # Load + early-exit decisions in one short UoW. The returned model is
        # read-only hereafter; with expire_on_commit=False its loaded columns
        # stay readable after the session closes, so the slow I/O below holds no
        # DB connection.
        async with self._file_repo() as files:
            file_entity = await files.get_model(file_id)
            if file_entity is None:
                logger.warning("File %s not found for processing", file_id)
                return

            if file_entity.status != FileStatus.PENDING.value:
                logger.info(
                    "Skipping processing for %s because status is %s",
                    file_id,
                    file_entity.status,
                )
                return

            if file_entity.kind != "FILE":
                await files.mark_not_required(file_id)
                return

            search_enabled = bool(file_entity.search_enabled)

        # Safety net: the indexing-eligibility policy is applied early (at write
        # time) and the reindex queue only enqueues PENDING + search_enabled
        # files, so a non-indexable file never reaches here as PENDING. This
        # branch only fires if search was disabled after the job was enqueued.
        if not search_enabled:
            try:
                await self.search_service.remove_file(file_id)
            except Exception:
                logger.warning("Failed removing search projection for %s", file_id, exc_info=True)
            await self._file_projection.delete_child_artifacts(
                self.pod_id, file_entity.path
            )
            async with self._file_repo() as files:
                await files.mark_not_required(file_id)
            return

        # Size guard: extraction buffers the whole document in memory, so an
        # oversized file risks OOMing the worker. Terminally fail it (rather than
        # claim + attempt) so it never enters the processing/recovery loop.
        max_file_bytes = datastore_settings.document_processing_max_file_bytes
        size_bytes = int(getattr(file_entity, "size_bytes", 0) or 0)
        if max_file_bytes and size_bytes > max_file_bytes:
            logger.warning(
                "File %s (%d bytes) exceeds document_processing_max_file_bytes "
                "(%d); marking FAILED_PERMANENT without processing",
                file_id,
                size_bytes,
                max_file_bytes,
            )
            async with self._file_repo() as files:
                await files.mark_failed_permanent(
                    file_id,
                    error=(
                        f"file exceeds max processing size "
                        f"({size_bytes} > {max_file_bytes} bytes)"
                    ),
                )
            return

        # Claim PENDING -> PROCESSING in its own committed transaction so the
        # claim is durable before the long extraction begins. A crash mid-work
        # then leaves a recoverable PROCESSING row for recover_stuck_processing_files.
        async with self._file_repo() as files:
            claimed = await files.claim_for_processing(file_id)
        if not claimed:
            logger.info(
                "Skipping processing for %s because another worker already claimed it",
                file_id,
            )
            return

        try:
            # --- External I/O: NO DB connection held across any of this. ---
            # Reserve this file's bytes against the aggregate in-flight budget
            # (soft cap; disabled by default) so concurrent large documents can't
            # stack to an OOM. Held only for the memory-heavy extract+index span,
            # then released before the DB write below.
            async with get_inflight_byte_budget().reserve(size_bytes):
                current_metadata = dict(metadata or {})
                current_metadata.update(file_entity.file_metadata or {})
                search_metadata = await self._build_search_metadata(file_entity, current_metadata)
                extraction, user_markdown_bytes = await self._build_extraction(file_entity)
                chunks = self._chunks_for_index(extraction)
                page_count = 0
                has_markdown = False
                if self._should_store_converted_projection(file_entity):
                    page_count = extraction.page_count
                    has_markdown = extraction.has_markdown
                    await self._write_converted_projection(
                        file_entity,
                        extraction,
                        search_metadata,
                        user_markdown_bytes=user_markdown_bytes,
                    )
                else:
                    await self._file_projection.delete_child_artifacts(
                        self.pod_id, file_entity.path
                    )
                await self.search_service.index_file_chunks(
                    file_id,
                    chunks,
                    search_metadata,
                )
            # Persist page metadata so listing/markdown tools can report page
            # count without a storage round-trip.
            merged_metadata = {
                **(file_entity.file_metadata or {}),
                "page_count": page_count,
                "has_markdown": has_markdown,
            }
            async with self._file_repo() as files:
                completed = await files.mark_completed(
                    file_id, file_metadata=merged_metadata
                )
            if not completed:
                logger.info(
                    "Skipped marking %s as COMPLETED because a newer update already reset it",
                    file_id,
                )
        except Exception as exc:
            logger.error("Search processing failed for %s: %s", file_id, exc)
            async with self._file_repo() as files:
                failed = await files.mark_failed(
                    file_id, error=self._sanitize_error(exc)
                )
            if not failed:
                logger.info(
                    "Skipped marking %s as FAILED because a newer update already reset it",
                    file_id,
                )
            raise

    async def _build_extraction(
        self, file_entity: DatastoreFile
    ) -> tuple[DocumentExtraction, bytes | None]:
        """Produce the extraction to index for a file.

        Bring-your-own path: a file flagged ``markdown_source=user`` is indexed
        from its stored ``source.md`` (chunked in-process) with NO document-
        processor call and NO original download — the user's markdown IS the
        agent-facing document. The raw bytes are returned so the projection write
        can re-persist ``source.md`` (delete_child_artifacts wipes the container).
        Everything else goes through the configured document processor.
        """
        if (file_entity.file_metadata or {}).get("markdown_source") == _USER_MARKDOWN_SOURCE:
            try:
                raw = await self.storage.download_file(
                    build_datastore_child_user_markdown_key(
                        self.pod_id, file_entity.path
                    )
                )
            except DatastoreObjectNotFoundError:
                raw = None
            if raw is not None:
                markdown = raw.decode("utf-8", "replace")
                if markdown.strip():
                    images = await self._load_user_markdown_images(file_entity)
                    extraction = await self._extraction_from_user_markdown(
                        markdown, images
                    )
                    return extraction, raw
            logger.warning(
                "File %s is flagged markdown_source=user but its source.md is "
                "missing/empty; falling back to document extraction",
                file_entity.path,
            )

        # Stream the source to a temp file instead of buffering the whole file in
        # memory. The processor extracts from the path (Kreuzberg streams it to
        # its multipart body; markitdown/docling read it off the loop), so peak
        # memory stays ~one chunk rather than the file plus a BytesIO copy.
        storage_key = build_datastore_file_storage_key(self.pod_id, file_entity.path)
        tmp_path = await stream_to_tempfile(self.storage.iter_download(storage_key))
        try:
            extraction = await self.document_processor.extract(
                None,
                file_entity.name,
                mime_type=self._base_mime_type(file_entity),
                content_path=tmp_path,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return extraction, None

    async def _load_user_markdown_images(
        self, file_entity: DatastoreFile
    ) -> list[DocumentImage]:
        """Download the companion images the user attached with their markdown
        (basenames recorded in ``file_metadata``). They live as sibling child
        artifacts and are re-persisted by the projection write. A missing asset
        is skipped (its markdown reference simply won't resolve)."""
        names = (file_entity.file_metadata or {}).get(_MARKDOWN_ASSET_NAMES_KEY) or []
        images: list[DocumentImage] = []
        for name in names:
            try:
                content = await self.storage.download_file(
                    build_datastore_child_artifact_key(
                        self.pod_id, file_entity.path, name
                    )
                )
            except DatastoreObjectNotFoundError:
                logger.warning(
                    "User markdown asset %s missing for %s", name, file_entity.path
                )
                continue
            images.append(
                DocumentImage(
                    name=name,
                    content=content,
                    mime_type=mimetypes.guess_type(name)[0]
                    or "application/octet-stream",
                )
            )
        return images

    async def _extraction_from_user_markdown(
        self, markdown: str, images: list[DocumentImage]
    ) -> DocumentExtraction:
        """Build a ``DocumentExtraction`` from user-provided markdown: rewrite its
        image references to the companion-image basenames (so they resolve as
        sibling child artifacts), chunk it in-process, and derive per-page
        summaries from any ``<!-- PAGE n -->`` markers."""
        markdown = rewrite_image_references(markdown, {image.name for image in images})
        # chunk_markdown is a pure-Python loop over the whole document; keep it
        # off the event loop so a large doc doesn't stall the worker.
        chunks = await run_blocking(chunk_markdown, markdown, limiter="cpu_bound")
        page_count = max((page for _, page in parse_page_offsets(markdown)), default=0)
        pages = [DocumentPage(page_number=number) for number in range(1, page_count + 1)]
        return DocumentExtraction(
            markdown=markdown,
            chunks=chunks,
            images=images,
            pages=pages,
            detected_languages=[],
            extraction_mode="user_markdown",
        )

    def _chunks_for_index(self, extraction: DocumentExtraction) -> list[dict]:
        """Flatten domain chunks into the ``{text, metadata}`` shape the search
        index expects, surfacing native page spans as ``page_number``/``page_end``
        (the columns the search SQL reads)."""
        chunks: list[dict] = []
        for chunk in extraction.chunks:
            metadata = dict(chunk.metadata or {})
            if chunk.page_start is not None:
                metadata["page_number"] = chunk.page_start
            if chunk.page_end is not None:
                metadata["page_end"] = chunk.page_end
            chunks.append({"text": chunk.text, "metadata": metadata})
        return chunks

    async def _build_search_metadata(
        self,
        file_entity: DatastoreFile,
        metadata: dict,
    ) -> dict:
        enriched = dict(metadata)
        enriched["parent_path"] = None
        enriched["path"] = file_entity.path
        owner_user_id = getattr(file_entity, "owner_user_id", None)
        if owner_user_id is not None:
            enriched["owner_user_id"] = str(owner_user_id)
        if file_entity.path and "/" in file_entity.path[1:]:
            enriched["parent_path"] = file_entity.path.rsplit("/", 1)[0]
        return enriched

    async def _write_converted_projection(
        self,
        file_entity: DatastoreFile,
        extraction: DocumentExtraction,
        search_metadata: dict,
        *,
        user_markdown_bytes: bytes | None = None,
    ) -> None:
        """Write the file's derived child artifacts into its hidden colocated
        container: page-marked ``document.md``, extracted figures, and a
        ``manifest.json`` index. The markdown already carries native page markers
        and rewritten inline image references (the processor owns that).

        When ``user_markdown_bytes`` is given (bring-your-own markdown), the
        user's source ``source.md`` is re-persisted here — delete_child_artifacts
        above wipes the whole container, so this is the single place that keeps it
        alive across reprocesses."""
        await self._file_projection.delete_child_artifacts(
            self.pod_id, file_entity.path
        )

        document_bytes = extraction.markdown.encode("utf-8")
        await self.storage.upload_file(
            build_datastore_child_markdown_key(self.pod_id, file_entity.path),
            document_bytes,
        )
        if user_markdown_bytes is not None:
            await self.storage.upload_file(
                build_datastore_child_user_markdown_key(
                    self.pod_id, file_entity.path
                ),
                user_markdown_bytes,
            )

        manifest = {
            "version": _MANIFEST_VERSION,
            "source_path": file_entity.path,
            "source_name": file_entity.name,
            "source_mime_type": file_entity.mime_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "markdown_source": (
                _USER_MARKDOWN_SOURCE if user_markdown_bytes is not None else "extracted"
            ),
            "extraction_mode": extraction.extraction_mode,
            "detected_languages": extraction.detected_languages,
            "page_count": extraction.page_count,
            "pages": [
                {
                    "page_number": page.page_number,
                    "is_blank": page.is_blank,
                    "image_count": page.image_count,
                    "table_count": page.table_count,
                }
                for page in extraction.pages
            ],
            "artifacts": [
                {
                    "name": "document.md",
                    "content_type": "text/markdown; charset=utf-8",
                    "size_bytes": len(document_bytes),
                    "kind": "markdown",
                }
            ],
            "search_metadata": search_metadata,
        }

        # Drain the list rather than iterating it: each image's ``content`` bytes
        # can be large (base64-decoded figures from a scanned doc), and holding the
        # whole list resident until this method returns is the biggest avoidable
        # chunk of steady-state memory during ingestion. Popping each image before
        # uploading lets the GC reclaim its bytes as soon as the upload completes,
        # so only one image's bytes are held at a time instead of all of them.
        while extraction.images:
            image = extraction.images.pop(0)
            await self.storage.upload_file(
                build_datastore_child_artifact_key(
                    self.pod_id,
                    file_entity.path,
                    image.name,
                ),
                image.content,
            )
            manifest["artifacts"].append(
                {
                    "name": image.name,
                    "content_type": image.mime_type,
                    "size_bytes": len(image.content),
                    "kind": "image",
                    "page_number": image.page_number,
                }
            )

        await self.storage.upload_file(
            build_datastore_child_manifest_key(self.pod_id, file_entity.path),
            json.dumps(manifest).encode("utf-8"),
        )
