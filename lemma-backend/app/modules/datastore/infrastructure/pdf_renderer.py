"""In-process PDF page rasterization (pypdfium2 + Pillow).

PDFium is the same engine Kreuzberg uses internally; pypdfium2 is Apache/BSD
licensed. Rendering is CPU-bound and synchronous — callers must run it off the
event loop (e.g. ``anyio.to_thread.run_sync``) under a concurrency gate.

We open the document from a *file path* so PDFium mmaps/seeks rather than holding
the whole file in memory; peak memory is roughly one page bitmap.
"""

from __future__ import annotations

import io
import threading

import pypdfium2 as pdfium
from PIL import Image

# 72 PDF points == 1 inch, so a render scale of dpi/72 yields the target DPI.
_PDF_POINTS_PER_INCH = 72.0

# PDFium is NOT thread-safe: concurrent use from multiple worker threads (these
# functions run via anyio.to_thread) corrupts its global state and segfaults the
# process. Serialize every document lifecycle through one process-wide lock. The
# work here is short (text sampling) or on-demand and already concurrency-gated
# (rendering), so single-threading PDFium costs little and prevents the crash.
_PDFIUM_LOCK = threading.Lock()


def get_pdf_page_count(pdf_path: str) -> int:
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            return len(pdf)
        finally:
            pdf.close()


def _sample_page_indices(page_count: int, max_pages: int) -> list[int]:
    """Pick up to ``max_pages`` distinct page indices spread across the document.

    A spread (first / middle / last) beats the leading N: a scanned cover page
    on an otherwise-native report shouldn't flip the scanned-vs-native verdict.
    """
    if max_pages <= 0 or page_count <= 0:
        return []
    if page_count <= max_pages:
        return list(range(page_count))
    if max_pages == 1:
        return [0]
    step = (page_count - 1) / (max_pages - 1)
    return sorted({min(page_count - 1, round(i * step)) for i in range(max_pages)})


def get_pdf_text_sample(
    pdf_path: str,
    *,
    max_pages: int,
    password: str | None = None,
) -> tuple[int, int]:
    """Sample embedded text from a spread of pages to gauge scanned-vs-native.

    Returns ``(pages_sampled, total_text_chars)``. Native PDFs carry a text
    layer (high char count); scanned ones return ~0. Text extraction does not
    rasterize, so peak memory stays tiny regardless of page size. Opens by path
    so PDFium mmaps rather than holding the whole file in memory.

    Raises on encrypted/corrupt input — callers decide the safe default.
    """
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_path, password=password)
        try:
            page_count = len(pdf)
            indices = _sample_page_indices(page_count, max_pages)
            total_chars = 0
            for index in indices:
                page = pdf[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        total_chars += len(textpage.get_text_range())
                    finally:
                        textpage.close()
                finally:
                    page.close()
            return (len(indices), total_chars)
        finally:
            pdf.close()


def render_pdf_pages(
    pdf_path: str,
    page_numbers: list[int],
    *,
    dpi: int = 150,
    max_long_edge: int = 1568,
    jpeg_quality: int = 80,
    password: str | None = None,
) -> dict[int, bytes]:
    """Render the given 1-based pages of a PDF to JPEG bytes.

    Returns a ``{page_number: jpeg_bytes}`` map. Pages outside the document are
    skipped (absent from the result). Each page is rendered at ``dpi``, downscaled
    so its long edge is ≤ ``max_long_edge``, and JPEG-encoded at ``jpeg_quality``.
    """
    if not page_numbers:
        return {}

    scale = dpi / _PDF_POINTS_PER_INCH
    out: dict[int, bytes] = {}
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_path, password=password)
        try:
            page_count = len(pdf)
            for page_number in page_numbers:
                index = page_number - 1
                if index < 0 or index >= page_count:
                    continue
                page = pdf[index]
                try:
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                out[page_number] = _encode_jpeg(image, max_long_edge, jpeg_quality)
        finally:
            pdf.close()
    return out


def _encode_jpeg(image: Image.Image, max_long_edge: int, jpeg_quality: int) -> bytes:
    if image.mode not in ("RGB", "L"):
        # JPEG has no alpha; flatten onto white so transparency reads cleanly.
        image = image.convert("RGB")

    long_edge = max(image.size)
    if long_edge > max_long_edge:
        ratio = max_long_edge / long_edge
        new_size = (
            max(1, round(image.width * ratio)),
            max(1, round(image.height * ratio)),
        )
        image = image.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
    return buffer.getvalue()
