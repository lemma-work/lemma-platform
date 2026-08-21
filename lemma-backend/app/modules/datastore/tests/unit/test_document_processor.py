from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from app.modules.datastore.infrastructure.document_processor import (
    KreuzbergDocumentProcessor,
)
from app.modules.datastore.infrastructure.kreuzberg_helper import (
    KreuzbergExtractionResult,
)


def _processor(result: KreuzbergExtractionResult) -> KreuzbergDocumentProcessor:
    client = AsyncMock()
    client.process_file = AsyncMock(return_value=result)
    return KreuzbergDocumentProcessor(client=client)


def _png_b64() -> str:
    return base64.b64encode(b"png-bytes").decode("ascii")


@pytest.mark.asyncio
async def test_extract_keeps_native_page_markers_and_rewrites_image_refs():
    result = KreuzbergExtractionResult(
        {
            "content": "<!-- PAGE 1 -->\n\n# Title\n\n![](assets/image_0.png)",
            "images": [
                {
                    "image_index": 0,
                    "format": "png",
                    "data": _png_b64(),
                    "page_number": 1,
                }
            ],
            "pages": [{"page_number": 1, "content": "Title"}],
        }
    )
    extraction = await _processor(result).extract(b"pdf", "report.pdf")

    # Native markers are trusted; inline image src is rewritten to the sibling name.
    assert extraction.markdown == "<!-- PAGE 1 -->\n\n# Title\n\n![](image_0.png)"
    assert [image.name for image in extraction.images] == ["image_0.png"]
    assert extraction.images[0].content == b"png-bytes"
    assert extraction.page_count == 1


@pytest.mark.asyncio
async def test_extract_preserves_rich_content_structure_and_pages_at_byte_boundaries():
    # The rich markdown structure lives in the top-level `content` (headings,
    # tables); Kreuzberg does NOT insert <!-- PAGE --> markers and the per-page
    # `pages[].content` is plain. The adapter must keep `content` and insert
    # markers at the per-page byte boundaries — not rebuild from the plain pages.
    content = (
        "# Title\n\nPage one body.\n\n## Section\n\n| a | b |\n| - | - |\n\nPage two."
    )
    byte_start_p2 = content.encode("utf-8").index(b"## Section")
    result = KreuzbergExtractionResult(
        {
            "content": content,
            "pages": [
                {"page_number": 1, "content": "page one PLAIN no structure"},
                {"page_number": 2, "content": "page two PLAIN no structure"},
            ],
            "metadata": {
                "pages": {
                    "boundaries": [
                        {"byte_start": 0, "byte_end": byte_start_p2, "page_number": 1},
                        {
                            "byte_start": byte_start_p2,
                            "byte_end": len(content.encode("utf-8")),
                            "page_number": 2,
                        },
                    ]
                }
            },
        }
    )
    md = (await _processor(result).extract(b"pdf", "report.pdf")).markdown

    # Markdown structure preserved (NOT rebuilt from the plain per-page text).
    assert "# Title" in md
    assert "## Section" in md
    assert "| a | b |" in md
    assert "PLAIN" not in md
    # Page markers inserted at the byte boundaries.
    assert "<!-- PAGE 1 -->" in md and "<!-- PAGE 2 -->" in md
    assert md.index("# Title") < md.index("<!-- PAGE 2 -->") < md.index("## Section")


@pytest.mark.asyncio
async def test_extract_builds_page_markers_from_pages_when_content_empty():
    result = KreuzbergExtractionResult(
        {
            "content": "",
            "pages": [
                {"page_number": 1, "content": "Alpha"},
                {"page_number": 2, "content": "Beta"},
            ],
        }
    )
    extraction = await _processor(result).extract(b"pdf", "report.pdf")

    assert "<!-- PAGE 1 -->" in extraction.markdown
    assert "<!-- PAGE 2 -->" in extraction.markdown
    assert extraction.markdown.index("Alpha") < extraction.markdown.index("Beta")


@pytest.mark.asyncio
async def test_extract_surfaces_native_chunk_page_spans():
    result = KreuzbergExtractionResult(
        {
            "content": "<!-- PAGE 1 -->\n\nA\n\n<!-- PAGE 2 -->\n\nB",
            "chunks": [
                {"content": "A", "metadata": {"first_page": 1, "last_page": 1}},
                {"content": "B", "metadata": {"first_page": 2, "last_page": 3}},
            ],
            "pages": [
                {"page_number": 1, "content": "A"},
                {"page_number": 2, "content": "B"},
            ],
        }
    )
    extraction = await _processor(result).extract(b"pdf", "report.pdf")

    assert extraction.chunks[0].page_start == 1
    assert extraction.chunks[0].page_end == 1
    assert extraction.chunks[1].page_start == 2
    assert extraction.chunks[1].page_end == 3


@pytest.mark.asyncio
async def test_extract_derives_chunk_pages_from_byte_boundaries_when_native_absent():
    # Kreuzberg 4.9.x returns chunks with byte offsets but no native page span;
    # the adapter maps them onto metadata.pages.boundaries.
    result = KreuzbergExtractionResult(
        {
            "content": "x" * 200,
            "chunks": [
                {"content": "A", "metadata": {"byte_start": 0, "byte_end": 50}},
                {"content": "B", "metadata": {"byte_start": 130, "byte_end": 180}},
            ],
            "pages": [
                {"page_number": 1, "content": "A"},
                {"page_number": 2, "content": "B"},
            ],
            "metadata": {
                "pages": {
                    "boundaries": [
                        {"byte_start": 0, "byte_end": 100, "page_number": 1},
                        {"byte_start": 101, "byte_end": 200, "page_number": 2},
                    ]
                }
            },
        }
    )
    extraction = await _processor(result).extract(b"pdf", "report.pdf")

    assert extraction.chunks[0].page_start == 1
    assert extraction.chunks[1].page_start == 2


def test_supports_page_rendering_only_for_pdf():
    processor = KreuzbergDocumentProcessor(client=AsyncMock())
    assert processor.supports_page_rendering("application/pdf", "a.pdf") is True
    assert processor.supports_page_rendering(None, "a.pdf") is True
    assert processor.supports_page_rendering("text/plain", "a.txt") is False
