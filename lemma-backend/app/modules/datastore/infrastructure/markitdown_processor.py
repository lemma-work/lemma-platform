"""In-process ``DocumentProcessorPort`` adapter backed by Microsoft markitdown.

This adapter needs no external service — it converts documents to markdown in the
backend process — so a local stack can run without the Kreuzberg container. It is
strongest on office formats (docx/pptx/xlsx/html); for PDFs it produces plain,
page-marker-free markdown (no OCR, weaker tables/figures than Kreuzberg). Page
*images* still work: they are rendered on demand by the shared pypdfium2 mixin,
and we populate the PDF page count so those page artifacts stay addressable.

``markitdown`` is an optional dependency (extra: ``lemma-backend[markitdown]``);
it is imported lazily so the module stays importable without it (tests inject a
fake converter). Chunking is done in-process by ``markdown_chunker``.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile

import anyio

from app.core.concurrency.offload import run_blocking
from app.modules.datastore.infrastructure.streaming import read_file_bytes
from app.core.log.log import get_logger
from app.modules.datastore.domain.document_processing import (
    DocumentExtraction,
    DocumentPage,
)
from app.modules.datastore.infrastructure.markdown_chunker import chunk_markdown
from app.modules.datastore.infrastructure.pdf_page_rendering import (
    PdfPageRenderingMixin,
)
from app.modules.datastore.infrastructure.pdf_renderer import get_pdf_page_count

logger = get_logger(__name__)

_PDF_MIME = "application/pdf"
_INSTALL_HINT = (
    "The 'markitdown' document processor is selected but the markitdown library "
    "is not installed. Install the optional extra (pip install "
    "'lemma-backend[markitdown]') or set DOCUMENT_PROCESSOR=kreuzberg."
)


class MarkItDownDocumentProcessor(PdfPageRenderingMixin):
    """Document processor: in-process markitdown extraction + pypdfium rendering."""

    def __init__(self, converter: object | None = None):
        self._converter = converter
        # Fail fast (at construction) with an actionable message when the optional
        # dep is absent — but only when we would actually need it (no injected
        # converter, as used by tests). find_spec avoids importing markitdown here.
        if converter is None and importlib.util.find_spec("markitdown") is None:
            raise ImportError(_INSTALL_HINT)

    def _get_converter(self):
        if self._converter is None:
            from markitdown import MarkItDown

            self._converter = MarkItDown(enable_plugins=False)
        return self._converter

    async def extract(
        self,
        content: bytes | None,
        filename: str,
        *,
        mime_type: str | None = None,
        content_path: str | None = None,
    ) -> DocumentExtraction:
        # markitdown converts in-process, so it needs the bytes; when handed a
        # streamed temp path, read it off the loop.
        if content is None:
            content = await run_blocking(
                read_file_bytes, content_path, limiter="cpu_bound"
            )
        markdown = (await anyio.to_thread.run_sync(self._convert_sync, content, filename)).strip()
        chunks = (
            await run_blocking(chunk_markdown, markdown, limiter="cpu_bound")
            if markdown
            else []
        )
        pages = await anyio.to_thread.run_sync(
            self._pdf_pages, content, mime_type, filename
        )
        return DocumentExtraction(
            markdown=markdown,
            chunks=chunks,
            images=[],
            pages=pages,
            detected_languages=[],
            extraction_mode="markitdown",
        )

    # -- helpers -----------------------------------------------------------

    def _convert_sync(self, content: bytes, filename: str) -> str:
        # markitdown dispatches by file extension, so preserve the source suffix.
        suffix = os.path.splitext(filename)[1] or ""
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            result = self._get_converter().convert(tmp.name)
            # ``text_content`` is the stable attribute; newer versions also expose
            # ``markdown`` — accept either.
            return (
                getattr(result, "text_content", None)
                or getattr(result, "markdown", None)
                or ""
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _is_pdf(self, mime_type: str | None, filename: str) -> bool:
        base = (mime_type or "").split(";")[0].strip().lower()
        return base == _PDF_MIME or filename.lower().endswith(".pdf")

    def _pdf_pages(
        self, content: bytes, mime_type: str | None, filename: str
    ) -> list[DocumentPage]:
        """Per-page summaries for PDFs (page count via pypdfium2) so page-image
        child artifacts stay addressable even though markitdown emits no page
        markers. Non-PDFs (and probe failures) yield no pages."""
        if not self._is_pdf(mime_type, filename):
            return []
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            count = get_pdf_page_count(tmp.name)
        except Exception:
            logger.debug("markitdown: PDF page-count probe failed", exc_info=True)
            return []
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return [DocumentPage(page_number=number) for number in range(1, count + 1)]
