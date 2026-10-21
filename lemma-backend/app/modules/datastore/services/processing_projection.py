"""Writing a processed document's derived artifacts: markdown, figures, manifest.

Split from ``file_processing_service`` to keep it under the size ratchet.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.datastore.domain.document_processing import DocumentExtraction
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage_paths import (
    FileKeys,
    is_object_layout,
)
from app.modules.datastore.services.processing_run import _FileSnapshot

logger = get_logger(__name__)

_MANIFEST_VERSION = 3
_USER_MARKDOWN_SOURCE = "user"


async def write_converted_projection(
    storage: DatastoreStoragePort,
    file_entity: _FileSnapshot,
    keys: FileKeys,
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
    if not is_object_layout(file_entity):
        # The path layout has one container per file, so it is replaced in
        # place. The object layout writes this revision's own prefix and
        # never touches the one readers are using.
        try:
            await storage.delete_prefix(keys.child_prefix)
        except DatastoreDomainError:
            logger.debug(
                "datastore.processing_projection.child_container_delete.diagnostic",
                exc_info=True,
            )

    document_bytes = extraction.markdown.encode("utf-8")
    await storage.upload_file(keys.markdown, document_bytes)
    if user_markdown_bytes is not None:
        await storage.upload_file(keys.user_markdown, user_markdown_bytes)

    manifest = {
        "version": _MANIFEST_VERSION,
        "source_path": file_entity.path,
        "source_name": file_entity.name,
        "source_mime_type": file_entity.mime_type,
        "source_sha256": file_entity.content_sha256,
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
        await storage.upload_file(keys.child(image.name), image.content)
        manifest["artifacts"].append(
            {
                "name": image.name,
                "content_type": image.mime_type,
                "size_bytes": len(image.content),
                "kind": "image",
                "page_number": image.page_number,
            }
        )

    # One entry per page and image: a long document's manifest is not small.
    encoded = await run_blocking(json.dumps, manifest)
    await storage.upload_file(keys.manifest, encoded.encode("utf-8"))
