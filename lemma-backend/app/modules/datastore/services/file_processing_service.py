from __future__ import annotations

import json
import mimetypes
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.core.api.uploads import upload_source_sha256
from app.core.log.log import get_logger
from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.document_processing import (
    DocumentExtraction,
    DocumentImage,
    IndexingMetrics,
    DocumentPage,
    chunks_for_index,
)
from app.modules.datastore.domain.errors import (
    DatastoreObjectIntegrityError,
    DatastoreObjectNotFoundError,
)
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

logger = get_logger(__name__)


class _StaleProcessingClaim(Exception):
    pass


_MANIFEST_VERSION = 3

_USER_MARKDOWN_SOURCE = "user"
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
        search_service: PostgresSearchService,
        storage: DatastoreStoragePort | None = None,
        document_processor: DocumentProcessorPort | None = None,
    ):
        self.pod_id = pod_id
        self._uow_factory = uow_factory
        self.search_service = search_service
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

    @staticmethod
    def _exceeds_size_limit(size_bytes: int, max_file_bytes: int) -> bool:
        return bool(max_file_bytes and size_bytes > max_file_bytes)

    def _should_store_converted_projection(self, file_entity: DatastoreFile) -> bool:
        mime_type = self._base_mime_type(file_entity)
        return mime_type in _CONVERTED_MARKDOWN_MIME_TYPES if mime_type else False

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        """Return a safe, user-facing error string for storage in the DB.

        Provider bodies, object keys, SQL, URLs, and credentials may all appear
        in an exception message. Persist only the failure class and a stable
        summary; detailed diagnostics belong in redacted structured telemetry.
        """
        return f"{type(exc).__name__}: document processing failed"

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
                logger.debug(
                    'datastore.file_processing_service.file_s_not_found_processing.diagnostic',
                    file_id=file_id,
                )
                return

            if file_entity.status != FileStatus.PENDING.value:
                return

            if file_entity.kind != "FILE":
                await files.mark_not_required(file_id)
                return

            search_enabled = bool(file_entity.search_enabled)
            content_sha256 = getattr(file_entity, "content_sha256", None)

        # Safety net: the indexing-eligibility policy is applied early (at write
        # time) and the reindex queue only enqueues PENDING + search_enabled
        # files, so a non-indexable file never reaches here as PENDING. This
        # branch only fires if search was disabled after the job was enqueued.
        if not search_enabled:
            try:
                await self.search_service.remove_file(file_id)
            except Exception:
                logger.debug(
                    'datastore.file_processing_service.removing_search_projection_s.diagnostic',
                    file_id=file_id,
                    exc_info=True,
                )
            await self._file_projection.delete_child_artifacts(
                self.pod_id, file_entity.path
            )
            async with self._file_repo() as files:
                await files.mark_not_required(file_id)
            return

        max_file_bytes = datastore_settings.document_processing_max_file_bytes
        size_bytes = int(getattr(file_entity, "size_bytes", 0) or 0)
        if self._exceeds_size_limit(size_bytes, max_file_bytes):
            logger.debug(
                'datastore.file_processing_service.file_s_d_bytes_exceeds.diagnostic',
                file_id=file_id,
                size_bytes=size_bytes,
                max_file_bytes=max_file_bytes,
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
            processing_attempt = await files.claim_for_processing(
                file_id, content_sha256=content_sha256
            )
        if processing_attempt is None:
            return

        try:
            # Reserve this file's bytes against the aggregate in-flight budget
            # (soft cap; disabled by default) so concurrent large documents can't
            # stack to an OOM. Held only for the memory-heavy extract+index span,
            async with get_inflight_byte_budget().reserve(size_bytes):
                current_metadata = dict(metadata or {})
                current_metadata.update(file_entity.file_metadata or {})
                search_metadata = await self._build_search_metadata(
                    file_entity, current_metadata
                )
                extraction_started = time.perf_counter()
                extraction, user_markdown_bytes = await self._build_extraction(
                    file_entity
                )
                extraction_seconds = time.perf_counter() - extraction_started
                chunks = chunks_for_index(extraction)
                page_count = 0
                has_markdown = False
                projection_started = time.perf_counter()
                if self._should_store_converted_projection(file_entity):
                    page_count = extraction.page_count
                    has_markdown = extraction.has_markdown
                    await self._ensure_claim_current(
                        file_id, content_sha256, processing_attempt
                    )
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
                projection_seconds = time.perf_counter() - projection_started
                await self._ensure_claim_current(
                    file_id, content_sha256, processing_attempt
                )
                indexing_started = time.perf_counter()
                index_result = await self.search_service.index_file_chunks(
                    file_id,
                    chunks,
                    search_metadata,
                )
                indexing_seconds = time.perf_counter() - indexing_started
                indexing_stages = (
                    index_result if isinstance(index_result, IndexingMetrics) else None
                )
            merged_metadata = {
                **(file_entity.file_metadata or {}),
                "page_count": page_count,
                "has_markdown": has_markdown,
                "processing_metrics": {
                    "extraction_seconds": round(extraction_seconds, 6),
                    "projection_seconds": round(projection_seconds, 6),
                    "indexing_seconds": round(indexing_seconds, 6),
                    **(indexing_stages.as_metadata() if indexing_stages else {}),
                    "page_count": page_count,
                    "chunk_count": len(chunks),
                },
            }
            async with self._file_repo() as files:
                await files.mark_completed(
                    file_id,
                    content_sha256=content_sha256,
                    processing_attempt=processing_attempt,
                    file_metadata=merged_metadata,
                )
            logger.debug(
                "datastore.file_processing_service.datastore_completion_persisted_s_file.observed",
                file_id=file_id,
                page_count=page_count,
                count=len(chunks),
                extraction_seconds=extraction_seconds,
                projection_seconds=projection_seconds,
                indexing_seconds=indexing_seconds,
            )
        except _StaleProcessingClaim:
            return
        except Exception as exc:
            logger.debug(
                'datastore.file_processing_service.search_processing_s.propagated',
                file_id=file_id,
            exc_info=True,
        )
            async with self._file_repo() as files:
                missing_original = isinstance(
                    exc, (DatastoreObjectNotFoundError, DatastoreObjectIntegrityError)
                )
                method_name = {
                    True: "mark_missing_original",
                    False: "mark_failed",
                }[missing_original]
                mark_failure = getattr(files, method_name)
                await mark_failure(
                    file_id,
                    content_sha256=content_sha256,
                    processing_attempt=processing_attempt,
                    error=self._sanitize_error(exc),
                )
            logger.debug(
                "datastore.file_processing_service.datastore_persisted_s_file_s.observed",
                file_id=file_id,
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
        if (file_entity.file_metadata or {}).get(
            "markdown_source"
        ) == _USER_MARKDOWN_SOURCE:
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
            logger.debug(
                'datastore.file_processing_service.file_s_flagged_markdown_source.diagnostic'
            )

        # Stream the source to a temp file instead of buffering the whole file in
        # memory. The processor extracts from the path (Kreuzberg streams it to
        # its multipart body; markitdown/docling read it off the loop), so peak
        # memory stays ~one chunk rather than the file plus a BytesIO copy.
        storage_key = build_datastore_file_storage_key(self.pod_id, file_entity.path)
        tmp_path = await stream_to_tempfile(self.storage.iter_download(storage_key))
        try:
            expected_sha256 = getattr(file_entity, "content_sha256", None)
            if expected_sha256:
                actual_sha256 = await run_blocking(
                    upload_source_sha256,
                    Path(tmp_path),
                    limiter="cpu_bound",
                )
                if actual_sha256 != expected_sha256:
                    raise DatastoreObjectIntegrityError()
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

    async def _ensure_claim_current(
        self,
        file_id: UUID,
        content_sha256: str | None,
        processing_attempt: int,
    ) -> None:
        async with self._file_repo() as files:
            current = await files.is_processing_claim_current(
                file_id,
                content_sha256=content_sha256,
                processing_attempt=processing_attempt,
            )
        if not current:
            raise _StaleProcessingClaim

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
                logger.debug(
                    'datastore.file_processing_service.user_markdown_asset_s_missing.diagnostic'
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
        pages = [
            DocumentPage(page_number=number) for number in range(1, page_count + 1)
        ]
        return DocumentExtraction(
            markdown=markdown,
            chunks=chunks,
            images=images,
            pages=pages,
            detected_languages=[],
            extraction_mode="user_markdown",
        )

    async def _build_search_metadata(
        self,
        file_entity: DatastoreFile,
        metadata: dict,
    ) -> dict:
        enriched = dict(metadata)
        enriched.pop("processing_metrics", None)
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
                build_datastore_child_user_markdown_key(self.pod_id, file_entity.path),
                user_markdown_bytes,
            )

        manifest = {
            "version": _MANIFEST_VERSION,
            "source_path": file_entity.path,
            "source_name": file_entity.name,
            "source_mime_type": file_entity.mime_type,
            "source_sha256": getattr(file_entity, "content_sha256", None),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "markdown_source": (
                _USER_MARKDOWN_SOURCE
                if user_markdown_bytes is not None
                else "extracted"
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
