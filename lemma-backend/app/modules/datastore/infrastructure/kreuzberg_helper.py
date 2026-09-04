import asyncio
from functools import partial
from io import BytesIO
import json
import mimetypes
import os
import tempfile
from typing import Any

import aiohttp

from app.core.concurrency.offload import run_blocking
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DocumentExtractionUnavailableError
from app.modules.datastore.infrastructure.extraction_result import (
    KreuzbergExtractionResult,
)
from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    get_kreuzberg_circuit,
)
from app.modules.datastore.infrastructure.pdf_renderer import get_pdf_text_sample
from app.modules.datastore.infrastructure.streaming import open_binary
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Fallbacks for invalid retry settings. Connection failures use bounded backoff;
# timeouts are re-driven later to avoid duplicating accepted extraction work.
# Request-schema 400/422 responses alone use the compatibility-config fallback.
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_BASE_DELAY_SECONDS = 1.0


class KreuzbergTransientError(DocumentExtractionUnavailableError):
    """The extractor was unreachable or timed out; retry as a later job.

    Subclasses the engine-neutral unavailability error so processing refunds the
    attempt instead of spending it — see DocumentExtractionUnavailableError.
    """


class KreuzbergCompatibilityError(RuntimeError):
    """The extractor rejected the request schema and a legacy config may work."""


class KreuzbergHelper:
    def __init__(self):
        self.base_url = (
            datastore_settings.kreuzberg_url.rstrip("/")
            if datastore_settings.kreuzberg_url
            else None
        )
        # A long `total` covers a connected-but-slow OCR of a large PDF, but
        # `connect`/`sock_connect` make a DOWN endpoint fail within seconds
        # instead of hanging to the full total (which was the 180s-per-attempt
        # stall that pinned worker slots during a Kreuzberg outage).
        self.request_timeout = aiohttp.ClientTimeout(
            total=datastore_settings.kreuzberg_request_timeout_seconds,
            connect=datastore_settings.kreuzberg_connect_timeout_seconds,
            sock_connect=datastore_settings.kreuzberg_connect_timeout_seconds,
        )

    async def process_file(
        self,
        file_content: bytes | None,
        filename: str,
        chunk_content: bool = True,
        max_chars: int = 1000,
        max_overlap: int = 200,
        mime_type: str | None = None,
        content_path: str | None = None,
        **kwargs,
    ) -> KreuzbergExtractionResult:
        if not self.base_url:
            raise ValueError("Kreuzberg not configured")

        mime_type = mime_type or mimetypes.guess_type(filename)[0]
        if not mime_type:
            mime_type = "application/octet-stream"

        # Opt-in OCR probes PDFs once up front: native uses 150 DPI; scanned uses
        # forced OCR at 300 DPI. Probe failures and the default OCR-off path stay
        # digital-first, avoiding the old reactive double extraction.
        ocr_enabled = datastore_settings.document_processing_ocr_enabled
        initial_force_ocr = False
        if ocr_enabled and mime_type == "application/pdf":
            initial_force_ocr = await self._pdf_needs_ocr(
                file_content, content_path=content_path
            )

        async with aiohttp.ClientSession(timeout=self.request_timeout) as session:
            config = self._build_extract_config(
                mime_type,
                force_ocr=initial_force_ocr,
                max_chars=max_chars,
                max_overlap=max_overlap,
            )
            extraction = await self._extract_with_config_fallback(
                session,
                file_content=file_content,
                filename=filename,
                mime_type=mime_type,
                config=config,
                content_path=content_path,
            )
            extraction.extraction_mode = "ocr" if initial_force_ocr else "direct"

            # Safety net: something we classified as native that extracted no text
            # at all (misclassification / odd encoding) gets one forced-OCR retry.
            # Also gated on OCR being enabled — with it off we never escalate.
            if (
                ocr_enabled
                and not initial_force_ocr
                and self._should_retry_with_forced_ocr(extraction, mime_type)
            ):
                config = self._build_extract_config(
                    mime_type,
                    force_ocr=True,
                    max_chars=max_chars,
                    max_overlap=max_overlap,
                )
                extraction = await self._extract_with_config_fallback(
                    session,
                    file_content=file_content,
                    filename=filename,
                    mime_type=mime_type,
                    config=config,
                    content_path=content_path,
                )
                extraction.extraction_mode = "ocr"

            if chunk_content and extraction.content and not extraction.chunks:
                # Inline chunking (the `chunking` config key) normally makes this
                # unnecessary AND gives chunks native page spans. This is the
                # fallback for when it produced nothing.
                extraction.chunks = await self._chunk_content(
                    session,
                    text=extraction.content,
                    chunker_type="markdown",
                    max_chars=max_chars,
                    max_overlap=max_overlap,
                )

            if not extraction.chunks and extraction.content:
                # Last resort: chunk in-process. Xberg 1.x removed POST /chunk
                # entirely, so on that engine the remote fallback above always
                # fails — without this the whole document would be indexed as one
                # giant chunk, which destroys retrieval quality.
                extraction.chunks = await self._chunk_locally(
                    extraction.content,
                    max_chars=max_chars,
                    max_overlap=max_overlap,
                )

            return extraction

    @staticmethod
    async def _chunk_locally(
        content: str,
        *,
        max_chars: int,
        max_overlap: int,
    ) -> list[dict[str, Any]]:
        """Chunk markdown in this process, off the event loop.

        Mirrors what the xberg/docling adapters already do, so a document is
        never indexed as a single unsplittable blob just because the extractor
        has no chunking endpoint.
        """
        from app.modules.datastore.infrastructure.markdown_chunker import (
            chunk_markdown,
        )

        chunks = await run_blocking(
            partial(chunk_markdown, max_chars=max_chars, overlap=max_overlap),
            content,
            limiter="cpu_bound",
        )
        return [
            {
                "text": chunk.text,
                "metadata": {
                    key: value
                    for key, value in (
                        ("first_page", chunk.page_start),
                        ("last_page", chunk.page_end),
                    )
                    if value is not None
                },
            }
            for chunk in chunks
        ]

    async def _pdf_needs_ocr(
        self, content: bytes | None, content_path: str | None = None
    ) -> bool:
        """Probe a PDF with pypdfium2 to decide scanned-vs-native up front.

        Native PDFs carry a text layer; scanned ones don't. Deciding here lets us
        pick the right (single) extraction config instead of always running the
        heavy layout path and reactively re-extracting. Runs off the event loop.
        Any failure (encrypted / corrupt / 0-page) falls back to the native path
        — the prior default — rather than failing the extraction.

        When ``content_path`` is given (the streamed source on disk) it is probed
        directly — no extra copy is written.
        """
        sample_pages = max(1, datastore_settings.pdf_ocr_detection_sample_pages)
        min_chars = datastore_settings.pdf_ocr_detection_min_chars_per_page
        probe = partial(get_pdf_text_sample, max_pages=sample_pages)

        async def _probe(path: str) -> bool:
            try:
                pages_sampled, total_chars = await run_blocking(probe, path)
            except Exception:
                logger.debug(
                    "datastore.kreuzberg_helper.pdfium_ocr_probe_defaulting_native.observed",
                    exc_info=True,
                )
                return False
            if pages_sampled <= 0:
                return False
            return (total_chars / pages_sampled) < min_chars

        if content_path is not None:
            return await _probe(content_path)

        # Write to a temp file so PDFium mmaps it (peak ≈ one page, no second copy
        # of the input held in the backend); mirror render_pages' cleanup shape.
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(content or b"")
            tmp.flush()
            tmp.close()
            return await _probe(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _build_extract_config(
        self,
        mime_type: str,
        *,
        force_ocr: bool,
        max_chars: int = 1000,
        max_overlap: int = 200,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "enable_quality_processing": True,
            "include_document_structure": True,
            "output_format": "markdown",
            "result_format": "unified",
            "pages": {
                "extract_pages": True,
                "insert_page_markers": True,
                "marker_format": "\n\n<!-- PAGE {page_num} -->\n\n",
            },
            # Chunk inline during extraction so chunks carry native page spans
            # (chunk.metadata.first_page/last_page); avoids a second /chunk call
            # and our own page-marker → chunk mapping.
            "chunking": {
                "max_chars": max_chars,
                "overlap": max_overlap,
            },
            "ocr": {
                "backend": "tesseract",
                "language": "eng",
            },
        }
        # Cap the extractor's internal thread pool. Left unset it sizes itself
        # from the host's CPU count, ignoring our container limit, so several
        # concurrent extractions oversubscribe the box.
        max_threads = datastore_settings.document_processing_extractor_max_threads
        if max_threads > 0:
            config["concurrency"] = {"max_threads": max_threads}
        if self._supports_image_extraction(mime_type):
            # Native PDFs render embedded images fine at 150 DPI (4× less memory
            # than 300); scanned/OCR docs keep 300 for OCR + figure fidelity.
            config["images"] = {
                "extract_images": True,
                "target_dpi": 300 if force_ocr else 150,
                # Without this, image bytes arrive as a JSON array of integers —
                # several times the payload and parse cost of base64 for the
                # same bytes.
                "include_data_base64": True,
            }
        if mime_type == "application/pdf":
            # Layout + TATR reconstruct rich markdown and text tables for digital
            # PDFs. Fresh nested dicts keep compatibility fallback mutation-safe.
            config["pdf_options"] = {
                "extract_images": True,
                "extract_metadata": True,
                "allow_single_column_tables": True,
            }
            if datastore_settings.document_processing_layout_enabled:
                config["pdf_options"]["hierarchy"] = {
                    "enabled": True,
                    "k_clusters": 6,
                    "include_bbox": False,
                }
                config["layout"] = {
                    # "auto" pre-screens each page with cheap geometry signals and
                    # runs the layout model only where it can help. The default is
                    # "always", which renders and infers EVERY page and dominates
                    # extraction cost.
                    #
                    # NOTE: this replaced a "preset": "fast" key that never
                    # existed on LayoutDetectionConfig. That struct does not set
                    # deny_unknown_fields, so the key was accepted and silently
                    # discarded and we paid full always-on layout while believing
                    # otherwise. Only Xberg 1.x honours "strategy"; on Kreuzberg
                    # v4 it is likewise ignored (v4 has no page-selection knob),
                    # which is what keeps this config valid against both.
                    "strategy": datastore_settings.document_processing_layout_strategy,
                    "confidence_threshold": 0.5,
                    "apply_heuristics": True,
                    "table_model": datastore_settings.document_processing_table_model,
                }
        if force_ocr:
            config["force_ocr"] = True
        return config

    def _build_compat_extract_config(self, config: dict[str, Any]) -> dict[str, Any]:
        compat = dict(config)
        compat.pop("layout", None)

        pdf_options = dict(compat.get("pdf_options") or {})
        pdf_options.pop("hierarchy", None)
        pdf_options.pop("allow_single_column_tables", None)
        if pdf_options:
            compat["pdf_options"] = pdf_options
        else:
            compat.pop("pdf_options", None)

        compat.pop("result_format", None)
        return compat

    def _build_legacy_extract_config(
        self,
        mime_type: str,
        *,
        force_ocr: bool,
    ) -> dict[str, Any]:
        legacy: dict[str, Any] = {
            "enable_quality_processing": True,
            "include_document_structure": True,
            "output_format": "markdown",
            "ocr": {
                "backend": "tesseract",
                "language": "eng",
            },
        }
        if self._supports_image_extraction(mime_type):
            legacy["images"] = {
                "extract_images": True,
            }
        if mime_type == "application/pdf":
            legacy["pdf_options"] = {
                "extract_images": True,
                "extract_metadata": True,
            }
        if force_ocr:
            legacy["force_ocr"] = True
        return legacy

    async def _extract_with_config_fallback(
        self,
        session: aiohttp.ClientSession,
        *,
        file_content: bytes | None,
        filename: str,
        mime_type: str,
        config: dict[str, Any],
        content_path: str | None = None,
    ) -> KreuzbergExtractionResult:
        fallback_configs = [
            self._build_compat_extract_config(config),
            self._build_legacy_extract_config(
                mime_type,
                force_ocr=bool(config.get("force_ocr")),
            ),
        ]
        attempted_configs: list[dict[str, Any]] = []
        last_error: KreuzbergCompatibilityError | None = None

        for candidate in [config, *fallback_configs]:
            if candidate in attempted_configs:
                continue
            attempted_configs.append(candidate)
            try:
                return await self._extract(
                    session,
                    file_content=file_content,
                    filename=filename,
                    mime_type=mime_type,
                    config=candidate,
                    content_path=content_path,
                )
            except KreuzbergCompatibilityError as exc:
                last_error = exc
                if candidate == config:
                    logger.debug(
                        "datastore.kreuzberg_helper.kreuzberg_enhanced_extraction_s_retrying.diagnostic"
                    )
                continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("Kreuzberg extraction failed before sending a request")

    def _supports_image_extraction(self, mime_type: str) -> bool:
        return mime_type == "application/pdf" or mime_type.startswith("image/")

    def _should_retry_with_forced_ocr(
        self,
        extraction: KreuzbergExtractionResult,
        mime_type: str,
    ) -> bool:
        # Scanned PDFs are now detected up front (pypdfium2 probe) and OCR'd on
        # the first pass, so this is just a safety net: a supported doc we ran
        # without forced OCR that came back with *no text at all* (e.g. a
        # misclassified scan, or odd encoding) earns one forced-OCR retry. The
        # old quality_score<0.2 trigger is dropped — it caused an expensive
        # second full extraction on borderline-but-usable native PDFs.
        if not self._supports_image_extraction(mime_type):
            return False
        return not extraction.content.strip()

    async def _extract(
        self,
        session: aiohttp.ClientSession,
        *,
        file_content: bytes | None,
        filename: str,
        mime_type: str,
        config: dict[str, Any] | None = None,
        content_path: str | None = None,
    ) -> KreuzbergExtractionResult:
        max_attempts = (
            datastore_settings.kreuzberg_transient_retry_attempts
            or _TRANSIENT_RETRY_ATTEMPTS
        )
        base_delay = (
            datastore_settings.kreuzberg_transient_retry_base_delay_seconds
            or _TRANSIENT_RETRY_BASE_DELAY_SECONDS
        )

        # Fail fast when the extractor is already known-down (see kreuzberg_circuit).
        circuit = get_kreuzberg_circuit()
        circuit.raise_if_open()

        for attempt in range(max_attempts):
            # Build the multipart body fresh each attempt: a streamed file handle
            # is consumed once, so it can't be reused across retries. With a
            # content_path we stream the file from disk (peak memory ≈ one chunk)
            # instead of holding a full BytesIO copy.
            file_obj = None
            if content_path is not None:
                file_obj = await run_blocking(
                    open_binary, content_path, limiter="cpu_bound"
                )
                source: Any = file_obj
            else:
                source = BytesIO(file_content or b"")
            form_data = aiohttp.FormData()
            form_data.add_field(
                "files", source, filename=filename, content_type=mime_type
            )
            if config:
                form_data.add_field(
                    "config",
                    json.dumps(config),
                    content_type="application/json",
                )
            try:
                async with session.post(
                    f"{self.base_url}/extract",
                    data=form_data,
                ) as response:
                    await self._raise_for_status(response)
                    # A completed HTTP round-trip means the extractor is reachable.
                    circuit.record_success()
                    # Read bytes and parse OFF the loop. An extraction
                    # response carries the whole document's text and, when
                    # figures are requested, base64 images inline — tens of
                    # megabytes of JSON. aiohttp's .json() parses that on the
                    # event loop, and _parse_extract_response walks it again.
                    raw = await response.read()
                    return await run_blocking(
                        self._parse_extract_bytes, raw, limiter="cpu_bound"
                    )
            except (asyncio.TimeoutError, TimeoutError) as exc:
                # A timeout may happen after Kreuzberg accepted the upload and
                # started CPU-heavy work. Retrying immediately can duplicate that
                # work because disconnecting the HTTP client does not guarantee
                # server-side cancellation. Re-drive later through datastore
                # recovery instead of multiplying full-document extractions.
                circuit.record_failure()
                raise KreuzbergTransientError(
                    "Kreuzberg extract request timed out"
                ) from exc
            except aiohttp.ClientConnectionError as exc:
                # Connection establishment/transport failure: a bounded retry is
                # useful here because no successful response was received.
                circuit.record_failure()
                if attempt < max_attempts - 1:
                    delay = base_delay * (2**attempt)
                    logger.debug(
                        "datastore.kreuzberg_helper.kreuzberg_extract_connection_s_attempt.diagnostic",
                        max_attempts=max_attempts,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise KreuzbergTransientError(
                    "Kreuzberg extract connection failed"
                ) from exc
            except KreuzbergTransientError:
                circuit.record_failure()
                raise
            except aiohttp.ClientError as exc:
                # Non-connection client error — not worth a same-request retry.
                raise RuntimeError("Kreuzberg extract request failed") from exc
            finally:
                if file_obj is not None:
                    file_obj.close()
        # Unreachable: the loop either returns or raises on the final attempt.
        raise KreuzbergTransientError("Kreuzberg extract request failed")

    @staticmethod
    def _parse_extract_response(data: Any) -> KreuzbergExtractionResult:
        """Read one document out of an /extract response, on either wire format.

        Kreuzberg v4 returns a bare JSON array of results. Xberg 1.x returns an
        envelope, ``{results: [...], errors: [...], summary: {...}}``, and
        reports per-input failures inside a **200** — so a naive parse would
        silently treat a failed extraction as an empty document. Supporting both
        shapes here is what lets one client run against either engine, so the
        image tag is the only thing that changes when we migrate.
        """
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, list) and results:
                return KreuzbergExtractionResult(results[0])
            # 200 with no result means the errors array is the real answer.
            errors = data.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                detail = (
                    first.get("message") or first.get("error") or str(first)
                    if isinstance(first, dict)
                    else str(first)
                )
                # A document-level rejection, not an outage: this must spend an
                # attempt and eventually go terminal rather than be retried
                # forever, so it is deliberately NOT a transient error.
                raise RuntimeError(f"Extractor reported no usable result: {detail}")
            raise RuntimeError("Extractor returned an empty result set")
        if isinstance(data, list) and data:
            return KreuzbergExtractionResult(data[0])
        raise RuntimeError("Unexpected response from the extract endpoint")

    async def _chunk_content(
        self,
        session: aiohttp.ClientSession,
        *,
        text: str,
        chunker_type: str,
        max_chars: int,
        max_overlap: int,
    ) -> list[dict[str, Any]]:
        payload = {
            "text": text,
            "chunker_type": chunker_type,
            "config": {
                "max_characters": max_chars,
                "overlap": max_overlap,
            },
        }

        try:
            async with session.post(f"{self.base_url}/chunk", json=payload) as response:
                await self._raise_for_status(response)
                raw = await response.read()
            # Parsing stays inside the try. Returning [] is what makes the
            # caller fall back to in-process chunking, and a 200 carrying
            # something that is not JSON -- a proxy error page, an engine that
            # dropped this endpoint -- has to reach that fallback rather than
            # raise out of extract().
            return await run_blocking(
                self._normalize_chunk_bytes, raw, limiter="cpu_bound"
            )
        except Exception:
            logger.debug(
                "datastore.kreuzberg_helper.chunking_request_text_chunker_s.diagnostic",
                chunker_type=chunker_type,
                exc_info=True,
            )
            return []

    def _parse_extract_bytes(self, raw: bytes) -> KreuzbergExtractionResult:
        """Parse and normalize an extract response, off the event loop."""
        return self._parse_extract_response(json.loads(raw))

    def _normalize_chunk_bytes(self, raw: bytes) -> list[dict[str, Any]]:
        """Parse and normalize a chunk response, off the event loop."""
        return self._normalize_chunk_response(json.loads(raw))

    def _normalize_chunk_response(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            raw_chunks = data.get("chunks")
            if isinstance(raw_chunks, list):
                return self._format_chunks(raw_chunks)

        if isinstance(data, list):
            return self._format_chunks(data)

        return []

    def _format_chunks(self, raw_chunks: list[Any]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for chunk in raw_chunks:
            if isinstance(chunk, str):
                if chunk.strip():
                    formatted.append({"text": chunk, "metadata": {}})
                continue
            if isinstance(chunk, dict):
                text = chunk.get("text") or chunk.get("content") or ""
                if text:
                    formatted.append(
                        {
                            "text": text,
                            "metadata": chunk.get("metadata", {}),
                        }
                    )
        return formatted

    async def _raise_for_status(self, response: aiohttp.ClientResponse) -> None:
        if response.status < 400:
            return
        body = await response.text()
        message = f"Extractor request failed with status {response.status}: {body}"
        if response.status in {400, 422}:
            # 400 = bad request/config (the compat-config ladder may recover it);
            # 422 = the document itself could not be parsed or OCR'd.
            raise KreuzbergCompatibilityError(message)
        if response.status in {408, 429}:
            raise KreuzbergTransientError(message)
        if response.status == 502:
            # Model download from HuggingFace failed — exactly what a cold
            # container produces. Infrastructure, not a bad document.
            raise KreuzbergTransientError(message)
        if response.status >= 500:
            # A per-file extraction timeout surfaces as a 500 carrying
            # error_type=TimeoutError rather than a 408, so the body is the only
            # way to tell "the extractor is struggling" (retry later) from a
            # genuine internal error. Both are treated as transient; the
            # distinction is kept for the log.
            raise KreuzbergTransientError(message)
        raise RuntimeError(message)
