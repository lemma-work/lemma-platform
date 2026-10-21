"""Turning one stored document into chunks and derived artifacts, once per revision.

Run by the ``file.process`` work item (see ``work/file_kinds.py``), which owns
retries; this owns what one run does and how it reports what happened:

* returns normally when the file is done -- indexed, not required, stale (a
  newer write has its own item), or terminally failed for a reason that
  retrying cannot change (too large, original missing). Those terminal states
  are written here, fenced by the run's claim.
* raises ``Unavailable`` when the extractor or embedding provider is down, with
  the evidence for it. The row goes back to PENDING with a note and the work
  item waits without being charged.
* raises ``ItemFailure`` when it failed on this document -- including the
  extractor failing on it while serving others. The row goes back to PENDING
  with the reason; the work item charges an attempt, and its dead hook writes
  ``FAILED_PERMANENT`` when the attempts run out.

Every write is fenced by the claim token taken at the start, so a run that lost
its lease cannot overwrite a newer one.
"""

from __future__ import annotations

import mimetypes
import os
import time
from contextlib import suppress
from pathlib import Path
from uuid import UUID


from app.core.api.uploads import upload_source_sha256
from app.core.authorization.authz_epoch import bump_pod_files_epoch
from app.core.log.log import get_logger
from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import (
    ItemFailure,
    LeaseLost,
    Released,
)
from app.modules.datastore.domain.document_processing import (
    DocumentExtraction,
    DocumentImage,
    IndexingMetrics,
    DocumentPage,
    chunks_for_index,
)
from app.modules.datastore.domain.errors import (
    DatastoreDomainError,
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
from app.modules.datastore.infrastructure.kreuzberg_circuit import document_scope
from app.modules.datastore.infrastructure.markdown_chunker import chunk_markdown
from app.modules.datastore.infrastructure.streaming import stream_to_tempfile
from app.modules.datastore.infrastructure.markdown_images import (
    rewrite_image_references,
)
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.infrastructure.storage_paths import (
    FileKeys,
    content_keys,
    fallback_keys,
    is_object_layout,
    object_keys,
)
from app.modules.datastore.services import processing_preflight
from app.modules.datastore.services.processing_projection import (
    write_converted_projection,
)
from app.modules.datastore.services.processing_run import (
    DirectRun,
    ProcessingRun,
    _FileSnapshot,
    _waiting_note,
    unavailable_for,
)
from app.modules.datastore.services.files.page_markers import parse_page_offsets
from app.modules.datastore.services.search.indexing_availability import (
    sanitize_processing_error,
    warn_if_a_facility_is_absent,
)
from app.modules.datastore.services.search.postgres_search_service import (
    PostgresSearchService,
)

logger = get_logger(__name__)


class _StaleProcessingClaim(Exception):
    pass


class _TooLargeError(Exception):
    def __init__(self, too_large: processing_preflight.TooLarge) -> None:
        super().__init__(too_large.message)
        self.too_large = too_large


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

    def _base_mime_type(self, file_entity: _FileSnapshot) -> str | None:
        mime_type = file_entity.mime_type
        if mime_type:
            return mime_type.split(";")[0].strip().lower()
        guessed, _ = mimetypes.guess_type(file_entity.name or "")
        return guessed.lower() if guessed else None

    def _should_store_converted_projection(self, file_entity: _FileSnapshot) -> bool:
        mime_type = self._base_mime_type(file_entity)
        return mime_type in _CONVERTED_MARKDOWN_MIME_TYPES if mime_type else False

    async def process_file_async(self, file_id: UUID, metadata: dict | None = None):
        """Process now, in this process, with no work item: tests and tools."""
        del metadata
        await self.process(file_id, sha256=None, run=DirectRun(self._uow_factory))

    async def process(
        self, file_id: UUID, *, sha256: str | None, run: ProcessingRun
    ) -> None:
        """One run for the revision ``sha256`` (None: whatever the row holds)."""
        async with run.checkpoint() as uow:
            files = DatastoreFileRepository(uow)
            model = await files.get_model(file_id)
            if model is None:
                return
            snapshot = _FileSnapshot.of(model)
            if sha256 is not None and snapshot.content_sha256 != sha256:
                # A newer write replaced these bytes and enqueued its own run.
                return
            if snapshot.status not in (
                FileStatus.PENDING.value,
                FileStatus.PROCESSING.value,
            ):
                return
            if snapshot.kind != "FILE":
                await files.mark_not_required(file_id)
                return

        if not snapshot.search_enabled:
            await self._drop_index(snapshot)
            async with run.completion() as uow:
                await DatastoreFileRepository(uow).mark_not_required(file_id)
            return

        too_large = processing_preflight.check_size(snapshot.size_bytes)
        if too_large is not None:
            async with run.completion() as uow:
                await DatastoreFileRepository(uow).mark_failed_permanent_for(
                    file_id,
                    content_sha256=snapshot.content_sha256,
                    error=too_large.message,
                )
            return

        async with run.checkpoint() as uow:
            token = await DatastoreFileRepository(uow).claim_for_processing(
                file_id, content_sha256=snapshot.content_sha256
            )
        if token is None:
            return

        try:
            with document_scope(str(file_id)):
                async with get_inflight_byte_budget().reserve(snapshot.size_bytes):
                    merged_metadata = await self._extract_and_index(
                        snapshot, token=token, run=run
                    )
            async with run.completion() as uow:
                completed = await DatastoreFileRepository(uow).mark_completed(
                    file_id,
                    content_sha256=snapshot.content_sha256,
                    claim=token,
                    file_metadata=merged_metadata,
                )
                if completed:
                    uow.after_commit(lambda: bump_pod_files_epoch(self.pod_id))
            if completed:
                await self._drop_previous_revision(snapshot)
        except _StaleProcessingClaim:
            return
        except LeaseLost, Released:
            # Not this document's doing: the runtime settles both without
            # charging an attempt, and the row is the next run's to claim.
            raise
        except _TooLargeError as exc:
            async with run.completion() as uow:
                await DatastoreFileRepository(uow).mark_failed_permanent_for(
                    file_id,
                    content_sha256=snapshot.content_sha256,
                    error=exc.too_large.message,
                    claim=token,
                )
        except (DatastoreObjectNotFoundError, DatastoreObjectIntegrityError) as exc:
            # The original is gone or not what was accepted: no retry can
            # bring it back, so this is terminal now, not after three tries.
            async with run.completion() as uow:
                await DatastoreFileRepository(uow).mark_failed_permanent_for(
                    file_id,
                    content_sha256=snapshot.content_sha256,
                    error=f"missing_original: {sanitize_processing_error(exc)}",
                    claim=token,
                )
        except Exception as exc:
            unavailable = unavailable_for(exc)
            if unavailable is not None:
                async with run.checkpoint() as uow:
                    await DatastoreFileRepository(uow).release_to_pending(
                        file_id,
                        content_sha256=snapshot.content_sha256,
                        claim=token,
                        note=_waiting_note(exc, unavailable),
                        charged=False,
                    )
                logger.warning(
                    "datastore.file_processing_service.dependency_unavailable.degraded",
                    file_id=str(file_id),
                    evidence=unavailable.evidence.value,
                )
                raise unavailable from exc
            summary = sanitize_processing_error(exc)
            async with run.checkpoint() as uow:
                await DatastoreFileRepository(uow).release_to_pending(
                    file_id,
                    content_sha256=snapshot.content_sha256,
                    claim=token,
                    note=summary,
                    charged=True,
                )
            warn_if_a_facility_is_absent(exc, pod_id=self.pod_id)
            logger.info(
                "datastore.file_processing_service.document_failed.observed",
                file_id=str(file_id),
                error_type=type(exc).__name__,
            )
            raise ItemFailure(summary) from exc

    async def _extract_and_index(
        self, snapshot: _FileSnapshot, *, token: int, run: ProcessingRun
    ) -> dict[str, object]:
        keys = content_keys(snapshot)
        search_metadata = self._build_search_metadata(snapshot)
        extraction_started = time.perf_counter()
        extraction, user_markdown_bytes = await self._build_extraction(snapshot, keys)
        extraction_seconds = time.perf_counter() - extraction_started
        chunks = chunks_for_index(extraction)
        over = processing_preflight.check_chunk_count(len(chunks))
        if over is not None:
            raise _TooLargeError(over)
        run.raise_if_stopping()
        page_count = 0
        has_markdown = False
        projection_started = time.perf_counter()
        if self._should_store_converted_projection(snapshot):
            page_count = extraction.page_count
            has_markdown = extraction.has_markdown
            await self._ensure_claim_current(snapshot, token)
            await write_converted_projection(
                self.storage,
                snapshot,
                keys,
                extraction,
                search_metadata,
                user_markdown_bytes=user_markdown_bytes,
            )
        elif not is_object_layout(snapshot):
            await self._delete_child_container(keys)
        projection_seconds = time.perf_counter() - projection_started
        await self._ensure_claim_current(snapshot, token)
        run.raise_if_stopping()
        indexing_started = time.perf_counter()
        index_result = await self.search_service.index_file_chunks(
            snapshot.id,
            chunks,
            search_metadata,
            sha256=snapshot.content_sha256,
        )
        indexing_seconds = time.perf_counter() - indexing_started
        indexing_stages = (
            index_result if isinstance(index_result, IndexingMetrics) else None
        )
        logger.debug(
            "datastore.file_processing_service.datastore_completion_persisted_s_file.observed",
            file_id=snapshot.id,
            page_count=page_count,
            count=len(chunks),
            extraction_seconds=extraction_seconds,
            projection_seconds=projection_seconds,
            indexing_seconds=indexing_seconds,
        )
        return {
            **snapshot.file_metadata,
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

    async def _drop_index(self, snapshot: _FileSnapshot) -> None:
        """Search was turned off after the run was queued: leave nothing findable."""
        try:
            await self.search_service.remove_file(snapshot.id)
        except Exception:
            logger.warning(
                "datastore.file_processing_service.search_index_purge.degraded",
                file_id=str(snapshot.id),
                exc_info=True,
            )
        if is_object_layout(snapshot):
            await self._delete_child_container(
                object_keys(
                    snapshot.pod_id,
                    snapshot.id,
                    snapshot.indexed_sha256 or snapshot.content_sha256,
                )
            )
        else:
            await self._delete_child_container(content_keys(snapshot))

    async def _drop_previous_revision(self, snapshot: _FileSnapshot) -> None:
        """Once the new revision is indexed, the one it replaced can go.

        Object layout only: in the path layout the container was replaced in
        place. The previous revision's original went when the write that
        replaced it committed; its derived container stayed until now so
        readers kept a conversion to read.
        """
        previous = snapshot.indexed_sha256
        if (
            not is_object_layout(snapshot)
            or previous is None
            or previous == snapshot.content_sha256
        ):
            return
        old = object_keys(snapshot.pod_id, snapshot.id, previous)
        with suppress(DatastoreDomainError):
            await self.storage.delete_prefix(old.child_prefix)
        with suppress(DatastoreDomainError):
            await self.storage.delete_file(old.original)

    async def _delete_child_container(self, keys: FileKeys) -> None:
        try:
            await self.storage.delete_prefix(keys.child_prefix)
        except DatastoreDomainError:
            logger.debug(
                "datastore.file_processing_service.child_container_delete.diagnostic",
                exc_info=True,
            )

    async def _ensure_claim_current(self, snapshot: _FileSnapshot, token: int) -> None:
        async with self._uow_factory() as uow:
            current = await DatastoreFileRepository(uow).is_processing_claim_current(
                snapshot.id,
                content_sha256=snapshot.content_sha256,
                claim=token,
            )
        if not current:
            raise _StaleProcessingClaim

    async def _build_extraction(
        self, file_entity: _FileSnapshot, keys: FileKeys
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
                raw = await self.storage.download_file(keys.user_markdown)
            except DatastoreObjectNotFoundError:
                raw = None
            if raw is not None:
                markdown = raw.decode("utf-8", "replace")
                if markdown.strip():
                    images = await self._load_user_markdown_images(file_entity, keys)
                    extraction = await self._extraction_from_user_markdown(
                        markdown, images
                    )
                    return extraction, raw

        # Stream the source to a temp file instead of buffering the whole file in
        # memory. The processor extracts from the path (Kreuzberg streams it to
        # its multipart body; xberg/docling read it off the loop), so peak
        # memory stays ~one chunk rather than the file plus a BytesIO copy.
        tmp_path = await self._download_original(file_entity, keys)
        try:
            expected_sha256 = file_entity.content_sha256
            if expected_sha256:
                actual_sha256 = await run_blocking(
                    upload_source_sha256,
                    Path(tmp_path),
                    limiter="cpu_bound",
                )
                if actual_sha256 != expected_sha256:
                    raise DatastoreObjectIntegrityError()
            too_large = await processing_preflight.check_original(
                tmp_path,
                mime_type=self._base_mime_type(file_entity),
                size_bytes=file_entity.size_bytes,
            )
            if too_large is not None:
                raise _TooLargeError(too_large)
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

    async def _download_original(
        self, file_entity: _FileSnapshot, keys: FileKeys
    ) -> str:
        """Stream the original to a temp file, from the other layout if it moved.

        Streamed rather than buffered so peak memory stays about one chunk. The
        fallback covers a row read just before the background copy flipped it,
        or just after.
        """
        try:
            return await stream_to_tempfile(self.storage.iter_download(keys.original))
        except DatastoreObjectNotFoundError:
            other = fallback_keys(file_entity)
            if other is None:
                raise
            return await stream_to_tempfile(self.storage.iter_download(other.original))

    async def _load_user_markdown_images(
        self, file_entity: _FileSnapshot, keys: FileKeys
    ) -> list[DocumentImage]:
        """Download the companion images the user attached with their markdown
        (basenames recorded in ``file_metadata``). They live as sibling child
        artifacts and are re-persisted by the projection write. A missing asset
        is skipped (its markdown reference simply won't resolve)."""
        names = (file_entity.file_metadata or {}).get(_MARKDOWN_ASSET_NAMES_KEY) or []
        images: list[DocumentImage] = []
        for name in names:
            try:
                content = await self.storage.download_file(keys.child(name))
            except DatastoreObjectNotFoundError:
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

    def _build_search_metadata(self, file_entity: _FileSnapshot) -> dict:
        enriched: dict = dict(file_entity.file_metadata)
        enriched.pop("processing_metrics", None)
        enriched["parent_path"] = None
        enriched["path"] = file_entity.path
        owner_user_id = file_entity.owner_user_id
        if owner_user_id is not None:
            enriched["owner_user_id"] = str(owner_user_id)
        if file_entity.path and "/" in file_entity.path[1:]:
            enriched["parent_path"] = file_entity.path.rsplit("/", 1)[0]
        return enriched
