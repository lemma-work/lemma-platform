"""In-process markdown chunker.

Chunking is normally delegated to the Kreuzberg service (its ``/extract`` inline
chunking or ``/chunk`` endpoint). Adapters that run in-process (e.g. the
``xberg`` document processor) and the bring-your-own-markdown path have no
Kreuzberg to call, so they chunk here instead.

The splitter is boundary-aware (paragraph > line > sentence > word) with a
character budget and overlap that mirror the Kreuzberg defaults (1000 / 200), so
chunks land at comparable sizes regardless of which path produced the markdown.
Per-chunk page spans are derived from the ``<!-- PAGE n -->`` markers via
``parse_page_offsets`` — the same markers the reader uses for page-scoped reads.
"""

from __future__ import annotations

from app.modules.datastore.domain.document_processing import DocumentChunk
from app.modules.datastore.services.files.page_markers import (
    PAGE_MARKER_RE,
    parse_page_offsets,
)

_DEFAULT_MAX_CHARS = 1000
_DEFAULT_OVERLAP = 200


def _page_at(offsets: list[tuple[int, int]], pos: int) -> int | None:
    """Page number in effect at character ``pos``.

    ``offsets`` is ``parse_page_offsets`` output — ``(char_offset, page)`` for
    each marker, in document order. Content before the first marker is attributed
    to the first page (leading frontmatter convention). ``None`` when the
    markdown has no markers at all.
    """
    if not offsets:
        return None
    page = offsets[0][1]
    for marker_offset, page_number in offsets:
        if marker_offset <= pos:
            page = page_number
        else:
            break
    return page


def _snap_boundary(text: str, start: int, end: int) -> int:
    """Pull ``end`` back to a natural break within ``[start, end]`` so a chunk
    does not sever a paragraph/sentence mid-way. Prefers, latest-first: a blank
    line, a newline, a sentence end, then a space. Falls back to the hard ``end``
    when no boundary is found in the lookback window."""
    if end >= len(text):
        return len(text)
    # Only look back over the tail of the window so chunks stay near the budget.
    floor = start + max(1, (end - start) // 2)
    window = text[floor:end]
    for needle in ("\n\n", "\n", ". ", ".\n", " "):
        idx = window.rfind(needle)
        if idx != -1:
            return floor + idx + len(needle)
    return end


def _clean(text: str) -> str:
    """Strip page-marker comments and collapse the whitespace they leave behind.

    Markers are structural annotations, not prose; keeping them out of chunk text
    yields cleaner embeddings and search snippets while the page span is retained
    separately on the ``DocumentChunk``."""
    stripped = PAGE_MARKER_RE.sub("", text)
    lines = [line.rstrip() for line in stripped.splitlines()]
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line:
            blanks = 0
            out.append(line)
        else:
            blanks += 1
            if blanks <= 1:
                out.append(line)
    return "\n".join(out).strip()


def chunk_markdown(
    markdown: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
    overlap: int = _DEFAULT_OVERLAP,
) -> list[DocumentChunk]:
    """Split ``markdown`` into overlapping, boundary-aligned chunks.

    Returns domain ``DocumentChunk``s with ``page_start``/``page_end`` (1-based,
    inclusive) taken from ``<!-- PAGE n -->`` markers when present, so the result
    drops straight into ``DocumentExtraction.chunks``.
    """
    if not markdown or not markdown.strip():
        return []
    max_chars = max(1, max_chars)
    overlap = max(0, min(overlap, max_chars - 1))

    offsets = parse_page_offsets(markdown)
    chunks: list[DocumentChunk] = []
    pos = 0
    length = len(markdown)
    while pos < length:
        hard_end = min(pos + max_chars, length)
        end = _snap_boundary(markdown, pos, hard_end)
        if end <= pos:  # no forward progress possible → take the hard window
            end = hard_end
        text = _clean(markdown[pos:end])
        if text:
            page_start = _page_at(offsets, pos)
            page_end = _page_at(offsets, max(pos, end - 1))
            chunks.append(
                DocumentChunk(
                    text=text,
                    page_start=page_start,
                    page_end=page_end or page_start,
                )
            )
        if end >= length:
            break
        # Start the next window ``overlap`` chars back, snapped forward to a word
        # boundary so the overlap never begins mid-token (which would emit stray
        # word fragments). Always make progress past the current ``pos``.
        raw = end - overlap
        if raw <= pos:
            raw = pos + 1
        boundary = max(markdown.rfind(" ", pos, raw), markdown.rfind("\n", pos, raw))
        pos = boundary + 1 if boundary > pos else raw
    return chunks
