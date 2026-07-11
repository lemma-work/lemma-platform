"""Streaming multipart parsing with per-field and process-wide limits."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request
from python_multipart.multipart import MultipartParser, parse_options_header

from app.core.api.uploads import (
    SNIFF_PREFIX_BYTES,
    UPLOAD_MEMORY_SPOOL_BYTES,
    StagedUpload,
)
from app.core.domain.errors import (
    BadRequestError,
    PayloadTooLargeError,
    UploadCapacityExceededError,
    ValidationError,
)


@dataclass(frozen=True, slots=True)
class MultipartFileLimit:
    max_bytes: int
    required: bool = False
    multiple: bool = False
    label: str | None = None


class UploadStagingCoordinator:
    """Fail-fast process budget for concurrent streaming request bodies."""

    def __init__(
        self,
        *,
        max_requests: int = 8,
        max_active_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.max_requests = max_requests
        self.max_active_bytes = max_active_bytes
        self._active_requests = 0
        self._active_bytes = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> None:
        async with self._lock:
            if self._active_requests >= self.max_requests:
                raise UploadCapacityExceededError()
            self._active_requests += 1

    async def reserve(self, size: int) -> None:
        async with self._lock:
            if self._active_bytes + size > self.max_active_bytes:
                raise UploadCapacityExceededError()
            self._active_bytes += size

    async def leave(self, reserved_bytes: int) -> None:
        async with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._active_bytes = max(0, self._active_bytes - reserved_bytes)

    @property
    def active_requests(self) -> int:
        return self._active_requests

    @property
    def active_bytes(self) -> int:
        return self._active_bytes


upload_staging_coordinator = UploadStagingCoordinator()


@dataclass(slots=True)
class StagedMultipartFile:
    field_name: str
    filename: str | None
    content_type: str | None
    staged: StagedUpload

    @property
    def path(self) -> Path:
        return self.staged.path

    @property
    def size(self) -> int:
        return self.staged.size

    async def read_bytes(self) -> bytes:
        return await self.staged.read_bytes()


@dataclass(slots=True)
class StagedMultipartForm:
    _files: dict[str, list[StagedMultipartFile]] = field(default_factory=dict)
    _text: dict[str, list[str]] = field(default_factory=dict)

    def file(self, name: str) -> StagedMultipartFile | None:
        values = self._files.get(name, [])
        return values[0] if values else None

    def require_file(self, name: str) -> StagedMultipartFile:
        value = self.file(name)
        if value is None:
            raise BadRequestError(
                f"Multipart file field {name!r} is required",
                code="MULTIPART_FIELD_REQUIRED",
            )
        return value

    def files(self, name: str) -> list[StagedMultipartFile]:
        return list(self._files.get(name, []))

    def text(self, name: str, default: str | None = None) -> str | None:
        values = self._text.get(name, [])
        return values[0] if values else default

    def has(self, name: str) -> bool:
        return name in self._files or name in self._text

    def require_text(self, name: str) -> str:
        value = self.text(name)
        if value is None:
            raise ValidationError(details=[{"field": name, "type": "missing"}])
        return value

    def boolean(self, name: str, default: bool | None = None) -> bool | None:
        value = self.text(name)
        if value is None:
            return default
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
        raise ValidationError(
            details=[{"field": name, "type": "bool_parsing"}],
        )


@dataclass(slots=True)
class _Part:
    header_field: bytearray = field(default_factory=bytearray)
    header_value: bytearray = field(default_factory=bytearray)
    headers: dict[bytes, bytes] = field(default_factory=dict)
    name: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size: int = 0
    digest: Any = field(default_factory=hashlib.sha256)
    prefix: bytearray = field(default_factory=bytearray)
    memory: bytearray = field(default_factory=bytearray)
    path: Path | None = None
    text_data: bytearray = field(default_factory=bytearray)


class _StreamingMultipartCollector:
    def __init__(
        self,
        *,
        file_limits: dict[str, MultipartFileLimit],
        text_fields: set[str],
        combined_max_bytes: int,
    ) -> None:
        self.file_limits = file_limits
        self.text_fields = text_fields
        self.combined_max_bytes = combined_max_bytes
        self.combined_size = 0
        self.form = StagedMultipartForm()
        self.part: _Part | None = None
        self.paths: list[Path] = []

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
        }

    def on_part_begin(self) -> None:
        self.part = _Part()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._part().header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._part().header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        part = self._part()
        part.headers[bytes(part.header_field).lower()] = bytes(part.header_value)
        part.header_field.clear()
        part.header_value.clear()

    def on_headers_finished(self) -> None:
        part = self._part()
        disposition = part.headers.get(b"content-disposition")
        if disposition is None:
            raise BadRequestError(
                "Multipart part is missing Content-Disposition",
                code="INVALID_MULTIPART",
            )
        _, options = parse_options_header(disposition)
        raw_name = options.get(b"name")
        if not raw_name:
            raise BadRequestError(
                "Multipart part is missing a field name",
                code="INVALID_MULTIPART",
            )
        part.name = raw_name.decode("utf-8", errors="strict")
        raw_filename = options.get(b"filename")
        part.filename = (
            raw_filename.decode("utf-8", errors="replace")
            if raw_filename is not None
            else None
        )
        raw_type = part.headers.get(b"content-type")
        part.content_type = (
            raw_type.decode("latin-1") if raw_type is not None else None
        )
        if part.filename is not None:
            limit = self.file_limits.get(part.name)
            if limit is None:
                raise BadRequestError(
                    f"Unexpected multipart file field {part.name!r}",
                    code="INVALID_MULTIPART_FIELD",
                )
            if not limit.multiple and self.form.files(part.name):
                raise BadRequestError(
                    f"Multipart file field {part.name!r} may appear only once",
                    code="INVALID_MULTIPART_FIELD",
                )
        elif part.name not in self.text_fields:
            raise BadRequestError(
                f"Unexpected multipart text field {part.name!r}",
                code="INVALID_MULTIPART_FIELD",
            )

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        part = self._part()
        chunk = data[start:end]
        part.size += len(chunk)
        self.combined_size += len(chunk)
        if self.combined_size > self.combined_max_bytes:
            raise PayloadTooLargeError(
                max_bytes=self.combined_max_bytes,
                field="multipart request",
            )
        if part.filename is None:
            if part.size > 64 * 1024:
                raise PayloadTooLargeError(max_bytes=64 * 1024, field=part.name or "form")
            part.text_data.extend(chunk)
            return

        limit = self.file_limits[part.name or ""]
        if part.size > limit.max_bytes:
            raise PayloadTooLargeError(
                max_bytes=limit.max_bytes,
                field=limit.label or part.name or "file",
            )
        part.digest.update(chunk)
        if len(part.prefix) < SNIFF_PREFIX_BYTES:
            part.prefix.extend(chunk[: SNIFF_PREFIX_BYTES - len(part.prefix)])
        if part.path is None and len(part.memory) + len(chunk) <= UPLOAD_MEMORY_SPOOL_BYTES:
            part.memory.extend(chunk)
            return
        if part.path is None:
            part.path = self._new_path()
            with part.path.open("wb") as destination:
                destination.write(part.memory)
                destination.write(chunk)
            part.memory.clear()
        else:
            with part.path.open("ab") as destination:
                destination.write(chunk)

    def on_part_end(self) -> None:
        part = self._part()
        if part.name is None:
            raise BadRequestError("Invalid multipart part", code="INVALID_MULTIPART")
        if part.filename is None:
            try:
                value = part.text_data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BadRequestError(
                    f"Multipart field {part.name!r} is not valid UTF-8",
                    code="INVALID_MULTIPART_FIELD",
                ) from exc
            self.form._text.setdefault(part.name, []).append(value)
        else:
            if part.path is None:
                part.path = self._new_path()
                part.path.write_bytes(part.memory)
                part.memory.clear()
            staged = StagedUpload(
                path=part.path,
                size=part.size,
                sha256=part.digest.hexdigest(),
                prefix=bytes(part.prefix),
            )
            self.form._files.setdefault(part.name, []).append(
                StagedMultipartFile(
                    field_name=part.name,
                    filename=part.filename,
                    content_type=part.content_type,
                    staged=staged,
                )
            )
        self.part = None

    def validate_required(self) -> None:
        for name, limit in self.file_limits.items():
            if limit.required and self.form.file(name) is None:
                raise BadRequestError(
                    f"Multipart file field {name!r} is required",
                    code="MULTIPART_FIELD_REQUIRED",
                )

    def cleanup(self) -> None:
        for path in self.paths:
            path.unlink(missing_ok=True)

    def _new_path(self) -> Path:
        descriptor, raw_path = tempfile.mkstemp(
            prefix="lemma-upload-",
            suffix=".staged",
        )
        os.close(descriptor)
        path = Path(raw_path)
        self.paths.append(path)
        return path

    def _part(self) -> _Part:
        if self.part is None:
            raise BadRequestError("Malformed multipart body", code="INVALID_MULTIPART")
        return self.part


@asynccontextmanager
async def stream_multipart_form(
    request: Request,
    *,
    file_limits: dict[str, MultipartFileLimit],
    text_fields: set[str] | None = None,
    combined_max_bytes: int,
    coordinator: UploadStagingCoordinator = upload_staging_coordinator,
) -> AsyncIterator[StagedMultipartForm]:
    """Parse multipart bytes directly from ASGI receive with early limits."""
    media_type, options = parse_options_header(
        request.headers.get("content-type", "").encode("latin-1")
    )
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise BadRequestError(
            "Expected multipart/form-data with a boundary",
            code="INVALID_MULTIPART",
        )
    collector = _StreamingMultipartCollector(
        file_limits=file_limits,
        text_fields=text_fields or set(),
        combined_max_bytes=combined_max_bytes,
    )
    parser = MultipartParser(boundary, collector.callbacks())
    reserved = 0
    entered = False
    try:
        await coordinator.enter()
        entered = True
        async for chunk in request.stream():
            await coordinator.reserve(len(chunk))
            reserved += len(chunk)
            await asyncio.to_thread(parser.write, chunk)
        await asyncio.to_thread(parser.finalize)
        collector.validate_required()
        yield collector.form
    finally:
        collector.cleanup()
        if entered:
            await coordinator.leave(reserved)


def streaming_multipart_openapi(
    component_name: str,
    *,
    properties: dict[str, object],
    required: list[str] | None = None,
) -> dict[str, object]:
    """Describe a manually streamed multipart body without using UploadFile."""
    component: dict[str, object] = {
        "type": "object",
        "title": component_name,
        "properties": properties,
    }
    if required:
        component["required"] = required
    return {
        "requestBody": {
            "required": bool(required),
            "content": {
                "multipart/form-data": {
                    "schema": {"$ref": f"#/components/schemas/{component_name}"}
                }
            },
        },
        "x-lemma-streaming-multipart-schema": component,
    }


def install_streaming_multipart_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Promote private multipart declarations to OpenAPI components."""
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            component = operation.pop("x-lemma-streaming-multipart-schema", None)
            if not isinstance(component, dict):
                continue
            name = component.get("title")
            if isinstance(name, str):
                components[name] = component
    return schema
