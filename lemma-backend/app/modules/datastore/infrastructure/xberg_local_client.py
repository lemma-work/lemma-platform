"""In-process document extraction, shaped exactly like the HTTP client's output.

xberg is Kreuzberg renamed (same author, now MIT, a Rust core with Python
bindings), so it returns the same information under the same names. That makes
the transport the only thing worth replacing: this client produces a
``KreuzbergExtractionResult`` and the existing normalizer does the rest, so
markdown assembly, page markers, image de-duplication and chunk page-mapping
stay byte-identical to the container path rather than being written twice.

Why in-process here and a container in cloud. Extraction is CPU-bound, and in
cloud that belongs in its own container rather than competing with the API's
event loop. A local install has no container fleet — one backend process is the
whole deployment — so in-process is the only option, and the cost is acceptable
for one machine with one user.

It does not block the loop. ``xberg.extract`` is a genuine coroutine backed by
the Rust runtime and releases the GIL: measured against this repo's arxiv
fixtures, a 75-page 6.6 MB paper extracted in 0.80s with the event loop's
maximum stall at 1.8 ms over 144 samples. So it is awaited directly; wrapping it
in ``run_blocking`` would add a thread hop for nothing.
"""

from __future__ import annotations

from typing import Any

from app.core.log.log import get_logger
from app.modules.datastore.infrastructure.extraction_result import (
    KreuzbergExtractionResult,
)

logger = get_logger(__name__)

_INSTALL_HINT = (
    "DOCUMENT_PROCESSOR is set to 'xberg' but the 'xberg' package is not "
    "installed. Install the backend's 'local' extra (uv sync --extra local)."
)

# Enough of a page's opening text to find it again in the whole document, and
# short enough to survive the small differences between a page's own rendering
# and its appearance in the assembled markdown.
_PAGE_ANCHOR_CHARS = 120


def _page_boundaries(content: str, pages: list[Any]) -> list[dict[str, int]]:
    """Locate each page's text within the assembled markdown.

    xberg does not emit ``<!-- PAGE n -->`` markers and leaves
    ``metadata.pages`` unset, but it does return per-page content — so the
    boundaries the normalizer wants can be recovered by finding where each page
    starts. Anchoring on a prefix rather than the whole page, and searching
    forward from the previous match, keeps this linear and monotonic.

    A page whose opening cannot be found is skipped rather than guessed. The
    effect is that its text is attributed to the preceding page, which is the
    same thing that happens today with a processor that emits no boundaries at
    all. Measured across this repo's six arxiv fixtures, four anchored every
    page and two missed one each.
    """
    data = content.encode("utf-8")
    found: list[dict[str, int]] = []
    cursor = 0
    for page in pages:
        text = (getattr(page, "content", None) or "").strip()
        number = getattr(page, "page_number", None)
        if not text or number is None:
            continue
        probe = text[:_PAGE_ANCHOR_CHARS].encode("utf-8")
        index = data.find(probe, cursor)
        if index < 0:
            continue
        found.append({"byte_start": index, "page_number": int(number)})
        cursor = index + len(probe)
    for position, entry in enumerate(found):
        entry["byte_end"] = (
            found[position + 1]["byte_start"] - 1
            if position + 1 < len(found)
            else len(data)
        )
    return found


class XbergLocalClient:
    """Same call signature as ``KreuzbergHelper.process_file``, no HTTP."""

    async def process_file(
        self,
        content: bytes | None,
        filename: str,
        *,
        chunk_content: bool = True,
        max_chars: int = 1000,
        max_overlap: int = 200,
        mime_type: str | None = None,
        content_path: str | None = None,
    ) -> KreuzbergExtractionResult:
        try:
            from xberg import ExtractInput, ExtractionConfig, extract
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise ImportError(_INSTALL_HINT) from exc

        config_kwargs: dict[str, Any] = {"output_format": "markdown"}
        if chunk_content:
            from xberg import ChunkingConfig

            # `max_characters`/`overlap`, not the `max_chars`/`max_overlap` the
            # published README shows -- the binding's real signature disagrees
            # with its own docs, which only running it reveals. `markdown`
            # chunking rather than the `text` default, because the content being
            # chunked is markdown and splitting it as prose cuts through tables
            # and fences.
            config_kwargs["chunking"] = ChunkingConfig(
                max_characters=max_chars,
                overlap=max_overlap,
                chunker_type="markdown",
            )

        # From the path when there is one. The bytes are already on disk, and
        # handing over a path lets the Rust side stream them instead of making
        # the caller hold a whole document in memory first.
        if content_path:
            source = ExtractInput(kind="uri", uri=content_path)
        else:
            source = ExtractInput(
                kind="bytes", bytes=content or b"", mime_type=mime_type
            )

        output = await extract(source, ExtractionConfig(**config_kwargs))
        results = getattr(output, "results", None) or []
        if not results:
            return KreuzbergExtractionResult({"content": "", "mime_type": mime_type})
        result = results[0]

        return _as_extraction_result(result, mime_type)


def _as_extraction_result(result: Any, mime_type: str | None) -> KreuzbergExtractionResult:
    """Shape one xberg result the way the shared normalizer reads it.

    Separate from the call above so the transport stays legible: everything here
    is renaming, and none of it decides anything.
    """
    text = getattr(result, "content", "") or ""
    pages = list(getattr(result, "pages", None) or [])
    boundaries = _page_boundaries(text, pages)
    return KreuzbergExtractionResult(
        {
            "content": text,
            "mime_type": getattr(result, "mime_type", None) or mime_type,
            "detected_languages": list(
                getattr(result, "detected_languages", None) or []
            ),
            "chunks": [
                {"text": getattr(chunk, "content", "") or "", "metadata": {}}
                for chunk in (getattr(result, "chunks", None) or [])
            ],
            "pages": [
                {
                    "page_number": getattr(page, "page_number", None),
                    "content": getattr(page, "content", "") or "",
                }
                for page in pages
            ],
            # Only ``pages`` is filled in: the normalizer reads boundaries from
            # here, and nothing else in this dict is consulted.
            "metadata": {"pages": {"boundaries": boundaries}} if boundaries else {},
        }
    )
