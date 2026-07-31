"""``DocumentProcessorPort`` adapter backed by a Docling Serve HTTP service.

Docling (MIT, LF AI & Data) produces high-fidelity structured markdown — reading
order, heading hierarchy, TableFormer tables — for born-digital research papers
and books. It is ML-heavy (torch + models), so like Kreuzberg it runs as its OWN
container (``quay.io/docling-project/docling-serve``) and the backend talks to it
over HTTP. This keeps lemma-backend lean: NO torch, NO markitdown-style in-process
model deps — only an aiohttp call.

Conversions use Docling Serve's ASYNC api — submit the file (``/v1/convert/file/
async``), poll the task (``/v1/status/poll/{id}``), then fetch the result
(``/v1/result/{id}``). Docling is CPU-slow on large PDFs (minutes); the async
flow avoids the sync endpoint's server-side wait cap and the client-timeout →
resubmit "queue storm" a single long request would cause.

Fast digital-first config (matches the project default): ``do_ocr=false`` (no
OCR spike), ``table_mode=fast``. Page markers are reconstructed from Docling's
page-break placeholder so page-scoped reads + per-chunk page spans keep working.
Chunking is done in-process by ``markdown_chunker``; page images by the shared
pypdfium2 mixin.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import os
import tempfile

import aiohttp
import anyio

from app.core.concurrency.offload import run_blocking
from app.modules.datastore.infrastructure.streaming import read_file_bytes
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
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
# Sentinel we ask Docling to insert between pages, then rewrite to numbered
# ``<!-- PAGE n -->`` markers (the same format the reader/chunker expect).
_PAGE_BREAK_SENTINEL = "<!-- DOCLING_PAGE_BREAK -->"
# Per-HTTP-request timeout (submit uploads the file; result downloads markdown;
# each poll is tiny). The WHOLE conversion is bounded separately by
# ``docling_request_timeout_seconds`` via the poll loop.
_HTTP_REQUEST_TIMEOUT_SECONDS = 180.0
# Server-side long-poll window: ``/status/poll?wait=N`` blocks up to N seconds
# before returning current status, so we poll cheaply without a tight client loop.
_POLL_WAIT_SECONDS = 5
_TERMINAL_STATUSES = frozenset({"success", "failure"})
_SUBMIT_RETRY_ATTEMPTS = 5
_SUBMIT_RETRY_BASE_DELAY_SECONDS = 0.5


class DoclingDocumentProcessor(PdfPageRenderingMixin):
    """Document processor: Docling Serve extraction (HTTP) + pypdfium rendering."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        conversion_timeout: float | None = None,
    ):
        url = base_url if base_url is not None else datastore_settings.docling_serve_url
        self.base_url = url.rstrip("/") if url else None
        # Whole-conversion budget (polled); per-request timeout is separate and
        # short since individual HTTP calls just upload/poll/download.
        self._conversion_timeout = (
            conversion_timeout or datastore_settings.docling_request_timeout_seconds
        )
        self._request_timeout = aiohttp.ClientTimeout(
            total=_HTTP_REQUEST_TIMEOUT_SECONDS
        )

    async def extract(
        self,
        content: bytes | None,
        filename: str,
        *,
        mime_type: str | None = None,
        content_path: str | None = None,
    ) -> DocumentExtraction:
        if not self.base_url:
            raise ValueError("Docling serve URL not configured")
        # Docling serve takes the bytes; when handed a streamed temp path, read
        # it off the loop.
        if content is None:
            content = await run_blocking(
                read_file_bytes, content_path, limiter="cpu_bound"
            )
        raw_markdown = await self._convert(content, filename, mime_type)
        markdown = self._number_page_markers(raw_markdown)
        chunks = (
            await run_blocking(chunk_markdown, markdown, limiter="cpu_bound")
            if markdown.strip()
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
            extraction_mode="docling",
        )

    # -- HTTP (async submit -> poll -> result) -----------------------------

    async def _convert(
        self, content: bytes, filename: str, mime_type: str | None
    ) -> str:
        async with aiohttp.ClientSession(timeout=self._request_timeout) as session:
            task = await self._submit_async(session, content, filename, mime_type)
            task_id = task.get("task_id")
            if not task_id:
                raise RuntimeError("Docling async submit returned no task_id")
            status = await self._await_completion(session, task_id, task, filename)
            if status == "failure":
                raise RuntimeError(f"Docling conversion failed for {filename}")
            result = await self._fetch_result(session, task_id)
            return self._markdown_from_response(result)

    async def _submit_async(
        self,
        session: aiohttp.ClientSession,
        content: bytes,
        filename: str,
        mime_type: str | None,
    ) -> dict:
        """Submit the file for asynchronous conversion. Only the (fast) submit is
        retried on a transient connection failure — the long conversion itself is
        never resubmitted (that caused a queue storm on slow docs)."""
        for attempt in range(_SUBMIT_RETRY_ATTEMPTS):
            form = self._build_form(content, filename, mime_type)
            try:
                async with session.post(
                    f"{self.base_url}/v1/convert/file/async", data=form
                ) as response:
                    await self._raise_for_status(response)
                    return await response.json()
            except (
                aiohttp.ClientConnectionError,
                asyncio.TimeoutError,
                TimeoutError,
            ) as exc:
                if attempt < _SUBMIT_RETRY_ATTEMPTS - 1:
                    delay = _SUBMIT_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                    logger.debug(
                        'datastore.docling_processor.docling_async_submit_connection_s.diagnostic'
                    )
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError("Docling async submit failed") from exc
            except aiohttp.ClientError as exc:
                raise RuntimeError("Docling async submit failed") from exc
        raise RuntimeError("Docling async submit failed")

    async def _await_completion(
        self,
        session: aiohttp.ClientSession,
        task_id: str,
        task: dict,
        filename: str,
    ) -> str:
        """Poll the task until it reaches a terminal status, bounded by the
        whole-conversion budget. Transient poll failures are tolerated (the job
        keeps running server-side); only the overall deadline aborts."""
        status = task.get("task_status")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._conversion_timeout
        while status not in _TERMINAL_STATUSES:
            if loop.time() > deadline:
                raise RuntimeError(
                    f"Docling conversion timed out for {filename} after "
                    f"{self._conversion_timeout:.0f}s"
                )
            try:
                task = await self._poll(session, task_id)
                status = task.get("task_status")
            except aiohttp.ClientError, asyncio.TimeoutError, TimeoutError:
                # Job still runs server-side; retry the poll after a short pause.
                logger.debug(
                    "datastore.docling_processor.docling_poll_hiccup_s_retrying.observed",
                    exc_info=True,
                )
                await asyncio.sleep(_POLL_WAIT_SECONDS)
        return status

    async def _poll(self, session: aiohttp.ClientSession, task_id: str) -> dict:
        async with session.get(
            f"{self.base_url}/v1/status/poll/{task_id}",
            params={"wait": _POLL_WAIT_SECONDS},
        ) as response:
            await self._raise_for_status(response)
            return await response.json()

    async def _fetch_result(self, session: aiohttp.ClientSession, task_id: str) -> dict:
        async with session.get(f"{self.base_url}/v1/result/{task_id}") as response:
            await self._raise_for_status(response)
            return await response.json()

    def _build_form(
        self, content: bytes, filename: str, mime_type: str | None
    ) -> aiohttp.FormData:
        form = aiohttp.FormData()
        form.add_field(
            "files",
            BytesIO(content),
            filename=filename,
            content_type=mime_type or "application/pdf",
        )
        form.add_field("to_formats", "md")
        # Fast digital-first path: no OCR spike, fast table structure.
        form.add_field("do_ocr", "false")
        form.add_field("force_ocr", "false")
        form.add_field("do_table_structure", "true")
        form.add_field("table_mode", "fast")
        form.add_field("image_export_mode", "placeholder")
        form.add_field("md_page_break_placeholder", _PAGE_BREAK_SENTINEL)
        return form

    @staticmethod
    def _markdown_from_response(data: object) -> str:
        # Single-file sync response: {"document": {"md_content": ...}, ...}.
        if isinstance(data, dict):
            document = data.get("document")
            if isinstance(document, dict):
                return document.get("md_content") or ""
            # Some deployments return a list of per-file results.
            if isinstance(data.get("documents"), list) and data["documents"]:
                first = data["documents"][0]
                if isinstance(first, dict):
                    inner = first.get("document") or first
                    if isinstance(inner, dict):
                        return inner.get("md_content") or ""
        raise RuntimeError("Unexpected response from Docling convert endpoint")

    async def _raise_for_status(self, response: aiohttp.ClientResponse) -> None:
        if response.status < 400:
            return
        body = await response.text()
        raise RuntimeError(
            f"Docling request failed with status {response.status}: {body}"
        )

    # -- normalization -----------------------------------------------------

    @staticmethod
    def _number_page_markers(markdown: str) -> str:
        """Rewrite Docling's between-page sentinels into numbered
        ``<!-- PAGE n -->`` markers (the reader/chunker page format), prefixing
        the first page so all content is paged."""
        if not markdown or not markdown.strip():
            return markdown or ""
        pages = markdown.split(_PAGE_BREAK_SENTINEL)
        pieces: list[str] = []
        for index, page in enumerate(pages, start=1):
            pieces.append(f"\n\n<!-- PAGE {index} -->\n\n")
            pieces.append(page.strip("\n"))
        return "".join(pieces).strip()

    def _is_pdf(self, mime_type: str | None, filename: str) -> bool:
        base = (mime_type or "").split(";")[0].strip().lower()
        return base == _PDF_MIME or filename.lower().endswith(".pdf")

    def _pdf_pages(
        self, content: bytes, mime_type: str | None, filename: str
    ) -> list[DocumentPage]:
        if not self._is_pdf(mime_type, filename):
            return []
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            count = get_pdf_page_count(tmp.name)
        except Exception:
            logger.debug(
                "datastore.docling_processor.docling_pdf_page_count_probe.observed",
                exc_info=True,
            )
            return []
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return [DocumentPage(page_number=number) for number in range(1, count + 1)]
