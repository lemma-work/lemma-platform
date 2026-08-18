"""Shared PDF page rasterization for document-processor adapters.

Rendering PDF pages to images is engine-agnostic — it reads the original PDF
with pypdfium2 and never touches the extraction engine — so every
``DocumentProcessorPort`` adapter (Kreuzberg, xberg, …) inherits it from
here and produces identical page images. The concurrency gate lives at module
scope so it actually bounds rasterizations across every adapter instance.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from functools import partial


from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pdf_renderer import render_pdf_pages
from app.core.concurrency.offload import run_blocking

_PDF_MIME = "application/pdf"

# Process-wide gate (adapters may be constructed per request, so the semaphore
# must live at module scope to actually bound concurrency). PDF rasterization is
# CPU/memory-heavy; this stops bursts from stacking renders.
_render_semaphore = asyncio.Semaphore(max(1, datastore_settings.pdf_render_concurrency))


class PdfPageRenderingMixin:
    """Adds pypdfium2-backed page rendering to a document-processor adapter."""

    def supports_page_rendering(self, mime_type: str | None, filename: str) -> bool:
        base = (mime_type or "").split(";")[0].strip().lower()
        return base == _PDF_MIME or filename.lower().endswith(".pdf")

    async def render_pages(
        self,
        pdf_content: bytes,
        page_numbers: list[int],
        *,
        dpi: int,
        max_long_edge: int,
        jpeg_quality: int,
    ) -> dict[int, bytes]:
        if not page_numbers:
            return {}
        # Write to a temp file so pypdfium mmaps it (peak ≈ one page bitmap, not
        # the whole document held twice). Rasterize off the event loop under the
        # process-wide gate.
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(pdf_content)
            tmp.flush()
            tmp.close()
            render = partial(
                render_pdf_pages,
                dpi=dpi,
                max_long_edge=max_long_edge,
                jpeg_quality=jpeg_quality,
            )
            async with _render_semaphore:
                return await run_blocking(render, tmp.name, page_numbers)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
