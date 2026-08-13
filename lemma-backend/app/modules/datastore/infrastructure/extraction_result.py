"""Normalization of an extractor response into a stable internal shape.

Split out of ``kreuzberg_helper.py`` to keep that module under the architecture
ratchet's size limit, and because this is a genuinely separate concern: the
helper owns HTTP, retries and the circuit breaker, while this owns the messy job
of turning whatever the engine returned into predictable chunks, images and
pages.

Deliberately tolerant of shape differences so one class covers both the
Kreuzberg v4 and Xberg 1.x responses — keys are optional, image payloads arrive
as base64, raw bytes or an int array, and page numbers may be camelCase.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import PurePosixPath
from typing import Any


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
            except TypeError, ValueError:
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
                except TypeError, ValueError:
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
            # data_base64 first: when the extractor supplies both, its `data`
            # field is a JSON array of integers for the same bytes, which is
            # several times larger to ship and to parse.
            raw_data = (
                image.get("data_base64")
                or image.get("data")
                or image.get("base64")
                or image.get("content")
            )
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
                except binascii.Error, ValueError:
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
