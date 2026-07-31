"""Domain value objects for document processing.

These are the app-facing result types produced by a ``DocumentProcessorPort``
adapter (Kreuzberg today, something else tomorrow). They deliberately contain
no engine-specific shape — the adapter is responsible for normalizing whatever
the underlying tool returns into these.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DocumentChunk:
    """A retrievable text chunk with its source page span (1-based, inclusive).

    ``page_start``/``page_end`` come from the processor's native page tracking
    when available, so we no longer re-derive them from page markers.
    """

    text: str
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class DocumentImage:
    """A figure/diagram extracted from the document. ``name`` is the filename
    referenced inline in the markdown (and stored as a sibling child artifact)."""

    name: str
    content: bytes
    mime_type: str
    page_number: int | None = None


@dataclass(slots=True)
class DocumentPage:
    """Per-page summary used for the converted manifest and page parity."""

    page_number: int
    is_blank: bool | None = None
    image_count: int = 0
    table_count: int = 0


@dataclass(slots=True)
class DocumentExtraction:
    """The full result of processing a document.

    ``markdown`` is the canonical, page-marked markdown (``<!-- PAGE n -->``
    boundaries) with inline image references already rewritten to the sibling
    child filenames in ``images``. ``chunks`` carry native page spans.
    """

    markdown: str
    chunks: list[DocumentChunk] = field(default_factory=list)
    images: list[DocumentImage] = field(default_factory=list)
    pages: list[DocumentPage] = field(default_factory=list)
    detected_languages: list[str] = field(default_factory=list)
    extraction_mode: str = "direct"

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_markdown(self) -> bool:
        return bool(self.markdown and self.markdown.strip())


@dataclass(frozen=True, slots=True)
class IndexingMetrics:
    """Measured stages of one file's search-index update."""

    chunk_count: int
    schema_seconds: float
    embedding_seconds: float
    persistence_seconds: float

    def as_metadata(self) -> dict[str, float]:
        return {
            "index_schema_seconds": round(self.schema_seconds, 6),
            "embedding_seconds": round(self.embedding_seconds, 6),
            "index_persistence_seconds": round(self.persistence_seconds, 6),
        }


def chunks_for_index(extraction: DocumentExtraction) -> list[dict]:
    """Flatten processor chunks into the search adapter's input shape."""
    chunks: list[dict] = []
    for chunk in extraction.chunks:
        metadata = dict(chunk.metadata or {})
        if chunk.page_start is not None:
            metadata["page_number"] = chunk.page_start
        if chunk.page_end is not None:
            metadata["page_end"] = chunk.page_end
        chunks.append({"text": chunk.text, "metadata": metadata})
    return chunks
