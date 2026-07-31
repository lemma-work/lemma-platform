"""Bounded multipart upload helpers."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncio

from fastapi import UploadFile

from app.core.domain.errors import PayloadTooLargeError


UPLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_MEMORY_SPOOL_BYTES = 8 * 1024 * 1024
SNIFF_PREFIX_BYTES = 8192
UploadSource = bytes | Path


def upload_source_size(source: UploadSource) -> int:
    return source.stat().st_size if isinstance(source, Path) else len(source)


def upload_source_sha256(source: UploadSource) -> str:
    digest = hashlib.sha256()
    if isinstance(source, Path):
        with source.open("rb") as staged:
            while chunk := staged.read(UPLOAD_CHUNK_BYTES):
                digest.update(chunk)
    else:
        digest.update(source)
    return digest.hexdigest()


def upload_source_has_content(source: UploadSource) -> bool:
    if isinstance(source, Path):
        with source.open("rb") as staged:
            while chunk := staged.read(UPLOAD_CHUNK_BYTES):
                if chunk.strip():
                    return True
        return False
    return bool(source.strip())


@dataclass(frozen=True, slots=True)
class BoundedUpload:
    data: bytes
    size: int
    sha256: str
    prefix: bytes


@dataclass(frozen=True, slots=True)
class StagedUpload:
    """A bounded upload staged on disk for streaming storage/inspection."""

    path: Path
    size: int
    sha256: str
    prefix: bytes

    async def read_bytes(self) -> bytes:
        return await asyncio.to_thread(self.path.read_bytes)


@dataclass(slots=True)
class UploadBudget:
    max_bytes: int
    field: str
    consumed: int = 0

    def consume(self, size: int) -> None:
        self.consumed += size
        if self.consumed > self.max_bytes:
            raise PayloadTooLargeError(max_bytes=self.max_bytes, field=self.field)


async def read_upload_limited(
    upload: UploadFile,
    *,
    max_bytes: int,
    field: str,
    budget: UploadBudget | None = None,
) -> BoundedUpload:
    """Read a small upload through the shared bounded staging implementation."""
    async with stage_upload_limited(
        upload,
        max_bytes=max_bytes,
        field=field,
        budget=budget,
    ) as staged:
        data = await staged.read_bytes()
        return BoundedUpload(
            data=data,
            size=staged.size,
            sha256=staged.sha256,
            prefix=staged.prefix,
        )


def _new_staging_path() -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="lemma-upload-", suffix=".staged")
    os.close(descriptor)
    return Path(raw_path)


def _append_bytes(path: Path, data: bytes) -> None:
    with path.open("ab") as destination:
        destination.write(data)


@asynccontextmanager
async def stage_upload_limited(
    upload: UploadFile,
    *,
    max_bytes: int,
    field: str,
    budget: UploadBudget | None = None,
) -> AsyncIterator[StagedUpload]:
    """Stage an upload with 1 MiB reads and at most 8 MiB buffered in memory.

    The temporary file is removed for success, limit failure, cancellation,
    disconnect, validation failure, or a downstream storage exception.
    """
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    prefix = bytearray()
    path: Path | None = None
    try:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                raise PayloadTooLargeError(max_bytes=max_bytes, field=field)
            if budget is not None:
                budget.consume(len(chunk))
            digest.update(chunk)
            if len(prefix) < SNIFF_PREFIX_BYTES:
                prefix.extend(chunk[: SNIFF_PREFIX_BYTES - len(prefix)])
            if path is None and sum(map(len, chunks)) + len(chunk) <= UPLOAD_MEMORY_SPOOL_BYTES:
                chunks.append(chunk)
                continue
            if path is None:
                path = _new_staging_path()
                buffered = b"".join(chunks)
                chunks.clear()
                await asyncio.to_thread(path.write_bytes, buffered + chunk)
            else:
                await asyncio.to_thread(_append_bytes, path, chunk)

        if path is None:
            path = _new_staging_path()
            await asyncio.to_thread(path.write_bytes, b"".join(chunks))
        yield StagedUpload(
            path=path,
            size=size,
            sha256=digest.hexdigest(),
            prefix=bytes(prefix),
        )
    finally:
        await upload.close()
        if path is not None:
            await asyncio.to_thread(path.unlink, missing_ok=True)
