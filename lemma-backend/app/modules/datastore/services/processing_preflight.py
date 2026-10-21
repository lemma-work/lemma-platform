"""Limits a document is checked against before the extractor is asked.

A document over a limit can only fail, and failing it after the extractor has
spent minutes on it -- or after it has crashed the extractor -- is the
expensive way to find out. Each check here is cheap (a size, a pypdfium page
count, a text sample) and ends in ``FAILED_PERMANENT`` with a reason a person
can read, before any of that is spent.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pdf_renderer import (
    get_pdf_page_count,
    get_pdf_text_sample,
)

logger = get_logger(__name__)

_PDF = "application/pdf"
#: Rough characters of new text per chunk (chunks overlap), for the estimate.
_CHARS_PER_CHUNK = 800
#: A dense page of text is a few chunks; scanned pages OCR to about the same.
_CHUNKS_PER_PAGE = 4


@dataclass(frozen=True, slots=True)
class TooLarge:
    """Why a document will not be processed, in words the UI shows."""

    reason: str

    @property
    def message(self) -> str:
        return f"too_large: {self.reason}"


def check_size(size_bytes: int) -> TooLarge | None:
    limit = datastore_settings.document_processing_max_file_bytes
    if limit and size_bytes > limit:
        return TooLarge(
            f"the file is {size_bytes} bytes and documents over {limit} bytes "
            "are not processed"
        )
    return None


async def check_original(
    content_path: str, *, mime_type: str | None, size_bytes: int
) -> TooLarge | None:
    """Pages, OCR pages and expected chunks, from the original on disk."""
    pages = 0
    if mime_type == _PDF:
        pages = await _page_count(content_path)
        page_limit = datastore_settings.document_processing_max_pages
        if page_limit and pages > page_limit:
            return TooLarge(
                f"the PDF has {pages} pages and documents over {page_limit} "
                "pages are not processed"
            )
        ocr_limit = datastore_settings.document_processing_max_ocr_pages
        if (
            ocr_limit
            and pages > ocr_limit
            and datastore_settings.document_processing_ocr_enabled
            and await _needs_ocr(content_path)
        ):
            return TooLarge(
                f"the PDF is scanned and has {pages} pages; scanned documents "
                f"over {ocr_limit} pages are not processed"
            )
    estimate = (
        pages * _CHUNKS_PER_PAGE if pages else max(1, size_bytes // _CHARS_PER_CHUNK)
    )
    return check_chunk_count(estimate, estimated=True)


def check_chunk_count(chunks: int, *, estimated: bool = False) -> TooLarge | None:
    limit = datastore_settings.document_processing_max_chunks
    if limit and chunks > limit:
        about = "is expected to produce about" if estimated else "produced"
        return TooLarge(
            f"the document {about} {chunks} search chunks and documents over "
            f"{limit} chunks are not indexed"
        )
    return None


async def _page_count(content_path: str) -> int:
    try:
        return await run_blocking(get_pdf_page_count, content_path, limiter="cpu_bound")
    except Exception:  # noqa: BLE001 - an unreadable PDF is the extractor's call
        # Encrypted or damaged: no count, so no page limit; the extractor
        # decides, and its failure is charged to the document like any other.
        logger.debug(
            "datastore.processing_preflight.page_count_unreadable.diagnostic",
            exc_info=True,
        )
        return 0


async def _needs_ocr(content_path: str) -> bool:
    sample = max(1, datastore_settings.pdf_ocr_detection_sample_pages)
    try:
        pages, chars = await run_blocking(
            get_pdf_text_sample, content_path, max_pages=sample, limiter="cpu_bound"
        )
    except Exception:  # noqa: BLE001 - unreadable means "not known to need OCR"
        logger.debug(
            "datastore.processing_preflight.ocr_probe_unreadable.diagnostic",
            exc_info=True,
        )
        return False
    if pages <= 0:
        return False
    return chars / pages < datastore_settings.pdf_ocr_detection_min_chars_per_page
