import asyncio
import base64
import binascii
from functools import partial
from io import BytesIO
import json
import mimetypes
import os
from pathlib import PurePosixPath
import tempfile
from typing import Any

import aiohttp
import anyio

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pdf_renderer import get_pdf_text_sample
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Bounded retry for transient connection/timeout failures talking to Kreuzberg.
# A refused connection or timeout is almost always the service being briefly
# unavailable (restart, GC pause, burst load), not a bad request — so retry the
# same request with exponential backoff rather than failing the extraction
# outright (which would mark the file FAILED and wait on the recovery cron).
# HTTP 4xx/5xx responses are NOT retried here; those are handled by the
# config-fallback layer.
# 5 attempts with 0.5s base ⇒ backoff 0.5+1+2+4 = 7.5s of waiting, enough to
# ride out a Kreuzberg container restart (e.g. after an OOM kill under burst
# load) instead of failing the extraction outright.
_TRANSIENT_RETRY_ATTEMPTS = 5
_TRANSIENT_RETRY_BASE_DELAY_SECONDS = 0.5


class KreuzbergExtractionResult:
    def __init__(self, data: dict[str, Any]):
        self.content = data.get("content", "")
        self.metadata = data.get("metadata", {})
        self.chunks = data.get("chunks", [])
        self.mime_type = data.get("mime_type")
        self.detected_languages = data.get("detected_languages", [])
        self.images = data.get("images", [])
        self.pages = data.get("pages", [])
        self.quality_score = data.get("quality_score")
        self.extraction_mode = data.get("extraction_mode", "direct")

    def get_chunks(self) -> list[dict[str, Any]]:
        if self.chunks:
            formatted = []
            for chunk in self.chunks:
                if isinstance(chunk, str):
                    formatted.append({"text": chunk, "metadata": {}})
                elif isinstance(chunk, dict):
                    if "text" not in chunk:
                        chunk["text"] = chunk.get("content", str(chunk))
                    if "metadata" not in chunk:
                        chunk["metadata"] = {}
                    formatted.append(chunk)
                else:
                    formatted.append({"text": str(chunk), "metadata": {}})
            return formatted

        if self.content:
            return [{"text": self.content, "metadata": self.metadata}]
        return []

    def get_images(self) -> list[dict[str, Any]]:
        images = self._format_images(self.images or [])
        for page in self.get_pages():
            images.extend(page["images"])

        formatted: list[dict[str, Any]] = []
        used_names: dict[str, bytes] = {}
        for image in images:
            name = image["name"]
            content = image["content"]
            if used_names.get(name) == content:
                continue
            if name in used_names:
                stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
                counter = 2
                candidate = f"{stem}_{counter}{suffix}"
                while candidate in used_names:
                    counter += 1
                    candidate = f"{stem}_{counter}{suffix}"
                image = {**image, "name": candidate}
                name = candidate
            used_names[name] = content
            formatted.append(image)
        return formatted

    def get_pages(self) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for index, page in enumerate(self.pages or []):
            if not isinstance(page, dict):
                continue
            page_number = page.get("page_number", page.get("pageNumber", index + 1))
            try:
                page_number = int(page_number)
            except (TypeError, ValueError):
                page_number = index + 1
            formatted.append(
                {
                    "page_number": page_number,
                    "content": str(page.get("content") or page.get("text") or ""),
                    "tables": (
                        page.get("tables")
                        if isinstance(page.get("tables"), list)
                        else []
                    ),
                    "images": self._format_images(
                        page.get("images") or [],
                        default_page_number=page_number,
                    ),
                    "is_blank": page.get("is_blank", page.get("isBlank")),
                }
            )
        return formatted

    def _format_images(
        self,
        images: list[Any],
        *,
        default_page_number: int | None = None,
    ) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                continue

            image_index = image.get("image_index", image.get("imageIndex"))
            page_number = image.get(
                "page_number",
                image.get("pageNumber", default_page_number),
            )
            image_format = str(image.get("format") or "png").lower()
            if image_index is not None:
                generated_name = f"image_{image_index}.{image_format}"
            elif page_number is not None:
                try:
                    normalized_page_number = int(page_number)
                except (TypeError, ValueError):
                    normalized_page_number = default_page_number or index + 1
                generated_name = (
                    f"page_{normalized_page_number:04d}_image_{index}.{image_format}"
                )
            else:
                generated_name = f"image_{index}.{image_format}"
            name = (
                image.get("name")
                or image.get("filename")
                or image.get("path")
                or image.get("source_path")
                or image.get("sourcePath")
                or generated_name
            )
            raw_data = image.get("data") or image.get("base64") or image.get("content")
            if raw_data is None:
                continue

            if isinstance(raw_data, bytes):
                content = raw_data
            elif isinstance(raw_data, str):
                payload = (
                    raw_data.split(",", 1)[-1]
                    if raw_data.startswith("data:")
                    else raw_data
                )
                try:
                    content = base64.b64decode(payload, validate=True)
                except (binascii.Error, ValueError):
                    continue
            elif isinstance(raw_data, list) and all(
                isinstance(item, int) for item in raw_data
            ):
                content = bytes(raw_data)
            else:
                continue

            formatted.append(
                {
                    "name": str(name).replace("\\", "/").split("/")[-1],
                    "content": content,
                    "mime_type": image.get("mime_type") or f"image/{image_format}",
                    "page_number": page_number,
                }
            )
        return formatted


class KreuzbergHelper:
    def __init__(self):
        self.base_url = (
            datastore_settings.kreuzberg_url.rstrip("/") if datastore_settings.kreuzberg_url else None
        )
        self.request_timeout = aiohttp.ClientTimeout(
            total=datastore_settings.kreuzberg_request_timeout_seconds
        )

    async def process_file(
        self,
        file_content: bytes,
        filename: str,
        chunk_content: bool = True,
        max_chars: int = 1000,
        max_overlap: int = 200,
        mime_type: str | None = None,
        **kwargs,
    ) -> KreuzbergExtractionResult:
        if not self.base_url:
            raise ValueError("Kreuzberg not configured")

        mime_type = mime_type or mimetypes.guess_type(filename)[0]
        if not mime_type:
            mime_type = "application/octet-stream"

        # Decide scanned-vs-native up front (PDF only) with pypdfium2 so we run a
        # SINGLE extraction with the right force_ocr + image DPI instead of the
        # old "extract direct, then reactively re-extract with OCR" double pass.
        # Native → force_ocr=False + 150-DPI images (the text layer yields headers
        # + image refs); scanned → force_ocr=True + 300 DPI. The structure/table
        # config is kept on both paths. Any probe failure falls back to the native
        # (direct) path — the prior default behavior.
        initial_force_ocr = False
        if mime_type == "application/pdf":
            initial_force_ocr = await self._pdf_needs_ocr(file_content)

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
            )
            extraction.extraction_mode = "ocr" if initial_force_ocr else "direct"

            # Safety net: something we classified as native that extracted no text
            # at all (misclassification / odd encoding) gets one forced-OCR retry.
            if not initial_force_ocr and self._should_retry_with_forced_ocr(
                extraction, mime_type
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
                )
                extraction.extraction_mode = "ocr"

            if chunk_content and extraction.content and not extraction.chunks:
                extraction.chunks = await self._chunk_content(
                    session,
                    text=extraction.content,
                    chunker_type="markdown",
                    max_chars=max_chars,
                    max_overlap=max_overlap,
                )

            if not extraction.chunks and extraction.content:
                extraction.chunks = [{"text": extraction.content, "metadata": {}}]

            return extraction

    async def _pdf_needs_ocr(self, content: bytes) -> bool:
        """Probe a PDF with pypdfium2 to decide scanned-vs-native up front.

        Native PDFs carry a text layer; scanned ones don't. Deciding here lets us
        pick the right (single) extraction config instead of always running the
        heavy layout path and reactively re-extracting. Runs off the event loop.
        Any failure (encrypted / corrupt / 0-page) falls back to the native path
        — the prior default — rather than failing the extraction.
        """
        sample_pages = max(1, datastore_settings.pdf_ocr_detection_sample_pages)
        min_chars = datastore_settings.pdf_ocr_detection_min_chars_per_page
        # Write to a temp file so PDFium mmaps it (peak ≈ one page, no second copy
        # of the input held in the backend); mirror render_pages' cleanup shape.
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            probe = partial(get_pdf_text_sample, max_pages=sample_pages)
            try:
                pages_sampled, total_chars = await anyio.to_thread.run_sync(
                    probe, tmp.name
                )
            except Exception:
                logger.debug(
                    "pdfium OCR probe failed; defaulting to native extraction path",
                    exc_info=True,
                )
                return False
            if pages_sampled <= 0:
                return False
            return (total_chars / pages_sampled) < min_chars
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
        if self._supports_image_extraction(mime_type):
            # Native PDFs render embedded images fine at 150 DPI (4× less memory
            # than 300); scanned/OCR docs keep 300 for OCR + figure fidelity.
            config["images"] = {
                "extract_images": True,
                "target_dpi": 300 if force_ocr else 150,
            }
        if mime_type == "application/pdf":
            # The RT-DETR layout + table-transformer (tatr) + hierarchy run on
            # EVERY PDF: they are what reconstruct the standardized rich markdown
            # — headers, *text tables*, and embedded images — and validation
            # against the live service showed dropping them loses tables on
            # born-digital PDFs (the cheap allow_single_column_tables heuristic
            # alone produced zero). The model cost turned out NOT to be the memory
            # driver (a layout-free run used the same RAM); peak is bounded
            # instead by extraction concurrency (2) + the kreuzberg container at
            # cpus=2, the 150-DPI native image render, and the single-pass OCR
            # decision (no reactive double extraction). Build fresh dicts each
            # call so the shallow copy in _build_compat_extract_config can't
            # mutate a shared sub-dict.
            config["pdf_options"] = {
                "extract_images": True,
                "extract_metadata": True,
                "allow_single_column_tables": True,
                "hierarchy": {
                    "enabled": True,
                    "k_clusters": 6,
                    "include_bbox": False,
                },
            }
            config["layout"] = {
                # "fast" = YOLO DocLayNet (11-class), lighter than the default
                # "accurate" RT-DETR v2 (17-class) layout model — lower peak RAM
                # with no measured loss of table quality (validated against the
                # live service: identical table count vs no preset). "tatr" is the
                # smallest table-structure model (~30MB) and is what reconstructs
                # the markdown tables (slanet_plus was lighter but lost tables).
                "preset": "fast",
                "confidence_threshold": 0.5,
                "apply_heuristics": True,
                "table_model": "tatr",
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
        file_content: bytes,
        filename: str,
        mime_type: str,
        config: dict[str, Any],
    ) -> KreuzbergExtractionResult:
        fallback_configs = [
            self._build_compat_extract_config(config),
            self._build_legacy_extract_config(
                mime_type,
                force_ocr=bool(config.get("force_ocr")),
            ),
        ]
        attempted_configs: list[dict[str, Any]] = []
        last_error: RuntimeError | None = None

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
                )
            except RuntimeError as exc:
                last_error = exc
                if candidate == config:
                    logger.warning(
                        "Kreuzberg enhanced extraction failed for %s; "
                        "retrying with compatible config",
                        filename,
                    )
                continue

        if last_error is not None:
            logger.warning(
                "Kreuzberg compatible extraction also failed for %s",
                filename,
            )
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
        file_content: bytes,
        filename: str,
        mime_type: str,
        config: dict[str, Any] | None = None,
    ) -> KreuzbergExtractionResult:
        form_data = aiohttp.FormData()
        form_data.add_field(
            "files",
            BytesIO(file_content),
            filename=filename,
            content_type=mime_type,
        )
        if config:
            form_data.add_field(
                "config",
                json.dumps(config),
                content_type="application/json",
            )

        for attempt in range(_TRANSIENT_RETRY_ATTEMPTS):
            try:
                async with session.post(
                    f"{self.base_url}/extract",
                    data=form_data,
                    params={"output_format": "markdown"},
                ) as response:
                    await self._raise_for_status(response)
                    data = await response.json()
                    if isinstance(data, list) and data:
                        return KreuzbergExtractionResult(data[0])
                    raise RuntimeError(
                        "Unexpected response from Kreuzberg extract endpoint"
                    )
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError, TimeoutError) as exc:
                # Transient: the service was briefly unreachable. Retry the same
                # request with backoff before giving up.
                if attempt < _TRANSIENT_RETRY_ATTEMPTS - 1:
                    delay = _TRANSIENT_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                    logger.warning(
                        "Kreuzberg extract connection failed for %s (attempt %d/%d); "
                        "retrying in %.1fs",
                        filename,
                        attempt + 1,
                        _TRANSIENT_RETRY_ATTEMPTS,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError("Kreuzberg extract request failed") from exc
            except aiohttp.ClientError as exc:
                # Non-connection client error — not worth a same-request retry.
                raise RuntimeError("Kreuzberg extract request failed") from exc
        # Unreachable: the loop either returns or raises on the final attempt.
        raise RuntimeError("Kreuzberg extract request failed")

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
                data = await response.json()
        except Exception:
            logger.warning(
                "Chunking request failed for text (chunker=%s, max_chars=%s); "
                "proceeding without chunks",
                chunker_type,
                max_chars,
                exc_info=True,
            )
            return []

        return self._normalize_chunk_response(data)

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
        raise RuntimeError(
            f"Kreuzberg request failed with status {response.status}: {body}"
        )
