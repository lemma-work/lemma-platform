"""Default ``DocumentProcessorPort`` adapter.

Composes the two engines we use today behind a single port: Kreuzberg (over its
REST API) for text/structure/figure extraction, and an in-process PDF
rasterizer (pypdfium2) for page images — Kreuzberg's REST server has no page
render endpoint on the 4.9.x line. A future adapter could satisfy the whole
port with Kreuzberg v5 alone; callers don't change.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.document_processing import (
    DocumentChunk,
    DocumentExtraction,
    DocumentImage,
    DocumentPage,
)
from app.modules.datastore.domain.ports import DocumentProcessorPort
from app.modules.datastore.infrastructure.kreuzberg_helper import (
    KreuzbergExtractionResult,
    KreuzbergHelper,
)
from app.modules.datastore.infrastructure.markdown_images import (
    rewrite_image_references,
)
from app.modules.datastore.infrastructure.pdf_page_rendering import (
    PdfPageRenderingMixin,
)

logger = get_logger(__name__)

_PAGE_MARKER = "<!-- PAGE "


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class KreuzbergDocumentProcessor(PdfPageRenderingMixin):
    """Default document processor: Kreuzberg extraction + pypdfium rendering."""

    def __init__(self, client: KreuzbergHelper | None = None):
        self._client = client or KreuzbergHelper()

    async def extract(
        self,
        content: bytes | None,
        filename: str,
        *,
        mime_type: str | None = None,
        content_path: str | None = None,
    ) -> DocumentExtraction:
        result = await self._client.process_file(
            content, filename, mime_type=mime_type, content_path=content_path
        )
        images = self._build_images(result)
        pages = self._build_pages(result)
        markdown = self._build_markdown(result, images)
        chunks = self._build_chunks(result)
        return DocumentExtraction(
            markdown=markdown,
            chunks=chunks,
            images=images,
            pages=pages,
            detected_languages=list(getattr(result, "detected_languages", []) or []),
            extraction_mode=getattr(result, "extraction_mode", "direct"),
        )

    # -- normalization -----------------------------------------------------

    def _build_images(self, result: KreuzbergExtractionResult) -> list[DocumentImage]:
        return [
            DocumentImage(
                name=image["name"],
                content=image["content"],
                mime_type=image.get("mime_type") or "application/octet-stream",
                page_number=_int_or_none(image.get("page_number")),
            )
            for image in result.get_images()
        ]

    def _build_pages(self, result: KreuzbergExtractionResult) -> list[DocumentPage]:
        pages: list[DocumentPage] = []
        for page in result.get_pages():
            pages.append(
                DocumentPage(
                    page_number=int(page["page_number"]),
                    is_blank=page.get("is_blank"),
                    image_count=len(page.get("images") or []),
                    table_count=len(page.get("tables") or []),
                )
            )
        return pages

    def _build_chunks(self, result: KreuzbergExtractionResult) -> list[DocumentChunk]:
        # Page numbers: prefer the engine's native chunk page spans; otherwise
        # map the chunk's byte range onto the document's per-page byte
        # boundaries (metadata.pages.boundaries) — robust and text-match-free.
        boundaries = self._page_boundaries(result)
        chunks: list[DocumentChunk] = []
        for chunk in result.get_chunks():
            metadata = dict(chunk.get("metadata") or {})
            page_start = _int_or_none(metadata.get("first_page", metadata.get("page_number")))
            page_end = _int_or_none(metadata.get("last_page"))
            if page_start is None:
                page_start = self._page_for_byte(boundaries, _int_or_none(metadata.get("byte_start")))
            if page_end is None:
                page_end = self._page_for_byte(boundaries, _int_or_none(metadata.get("byte_end")))
            page_end = page_end or page_start
            chunks.append(
                DocumentChunk(
                    text=chunk.get("text", ""),
                    page_start=page_start,
                    page_end=page_end,
                    metadata=metadata,
                )
            )
        return chunks

    @staticmethod
    def _page_boundaries(result: KreuzbergExtractionResult) -> list[tuple[int, int, int]]:
        page_struct = (getattr(result, "metadata", None) or {}).get("pages") or {}
        boundaries: list[tuple[int, int, int]] = []
        for entry in page_struct.get("boundaries") or []:
            if not isinstance(entry, dict):
                continue
            try:
                boundaries.append(
                    (
                        int(entry["byte_start"]),
                        int(entry["byte_end"]),
                        int(entry["page_number"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return boundaries

    @staticmethod
    def _page_for_byte(
        boundaries: list[tuple[int, int, int]], offset: int | None
    ) -> int | None:
        if not boundaries or offset is None:
            return None
        if offset < boundaries[0][0]:
            return boundaries[0][2]
        page = boundaries[0][2]
        for byte_start, byte_end, page_number in boundaries:
            if offset < byte_start:
                return page  # in a gap → belongs to the preceding page
            page = page_number
            if offset <= byte_end:
                return page_number
        return page  # beyond the last boundary → last page

    def _build_markdown(
        self,
        result: KreuzbergExtractionResult,
        images: list[DocumentImage],
    ) -> str:
        content = result.content or ""
        if _PAGE_MARKER in content:
            # Kreuzberg already inserted native page markers — trust them.
            markdown = content
        elif content.strip():
            # Preserve the rich top-level markdown (headings, tables, lists) and
            # insert page markers at the per-page byte boundaries. Do NOT rebuild
            # from pages[].content — that is plain text without markdown structure.
            boundaries = self._page_boundaries(result)
            markdown = (
                self._insert_page_markers_by_bytes(content, boundaries)
                if boundaries
                else content
            )
        else:
            # No top-level content (rare) — assemble from the per-page text.
            markdown = self._markdown_from_pages(result.get_pages(), images)
        return self._rewrite_image_references(markdown, {image.name for image in images})

    @staticmethod
    def _byte_to_char_index(content: str, byte_offsets: list[int]) -> dict[int, int]:
        """Map UTF-8 byte offsets (Kreuzberg's page boundaries index into the
        UTF-8 bytes of ``content``) to character indices in ``content``."""
        targets = sorted(set(byte_offsets))
        mapping: dict[int, int] = {}
        cursor = 0
        nbytes = 0
        for char_index, char in enumerate(content):
            while cursor < len(targets) and targets[cursor] <= nbytes:
                mapping[targets[cursor]] = char_index
                cursor += 1
            nbytes += len(char.encode("utf-8"))
        while cursor < len(targets):
            mapping[targets[cursor]] = len(content)
            cursor += 1
        return mapping

    def _insert_page_markers_by_bytes(
        self,
        content: str,
        boundaries: list[tuple[int, int, int]],
    ) -> str:
        page_starts = sorted(((b[0], b[2]) for b in boundaries), key=lambda item: item[0])
        if not page_starts:
            return content
        mapping = self._byte_to_char_index(content, [start for start, _ in page_starts])
        pieces: list[str] = []
        cursor = 0
        for index, (byte_start, page_number) in enumerate(page_starts):
            # The first page absorbs any leading frontmatter so all content is paged.
            char_index = 0 if index == 0 else mapping.get(byte_start, len(content))
            char_index = max(cursor, min(char_index, len(content)))
            if char_index > cursor:
                pieces.append(content[cursor:char_index].rstrip())
            pieces.append(f"\n\n<!-- PAGE {page_number} -->\n\n")
            cursor = char_index
        pieces.append(content[cursor:])
        return "".join(pieces).strip()

    def _markdown_from_pages(
        self,
        pages: list[dict],
        images: list[DocumentImage],
    ) -> str:
        images_by_page: dict[int, list[str]] = {}
        unplaced: list[str] = []
        for image in images:
            ref = f"![]({image.name})"
            if image.page_number is None:
                unplaced.append(ref)
            else:
                images_by_page.setdefault(image.page_number, []).append(ref)

        sections: list[str] = []
        for page in pages:
            page_number = int(page["page_number"])
            parts = [
                f"<!-- PAGE {page_number} -->",
                page.get("content") or "",
                *images_by_page.get(page_number, []),
            ]
            sections.append("\n\n".join(part for part in parts if part).strip())
        if unplaced:
            sections.append("\n\n".join(unplaced))
        return "\n\n".join(section for section in sections if section)

    def _rewrite_image_references(self, markdown: str, image_names: set[str]) -> str:
        return rewrite_image_references(markdown, image_names)


def create_document_processor() -> DocumentProcessorPort:
    """Composition helper mirroring ``create_datastore_storage`` — the single
    place the document-processing adapter is chosen.

    The adapter is selected by ``DATASTORE_DOCUMENT_PROCESSOR`` (resolved via
    ``effective_document_processor``): 'kreuzberg' (default when a Kreuzberg URL
    is set) or the in-process 'markitdown' adapter (optional dep). markitdown is
    imported lazily so the dependency is only required when it is selected."""
    which = datastore_settings.effective_document_processor()
    if which == "markitdown":
        from app.modules.datastore.infrastructure.markitdown_processor import (
            MarkItDownDocumentProcessor,
        )

        return MarkItDownDocumentProcessor()
    if which == "docling":
        from app.modules.datastore.infrastructure.docling_processor import (
            DoclingDocumentProcessor,
        )

        return DoclingDocumentProcessor()
    return KreuzbergDocumentProcessor()
