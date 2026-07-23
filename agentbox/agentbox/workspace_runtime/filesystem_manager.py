from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import stat as stat_module
from uuid import uuid4

from agentbox.domain import ByteRange, FileKind, FileStat


class FileConflictError(RuntimeError):
    pass


class FilesystemManager:
    def __init__(
        self,
        allowed_roots: tuple[str, ...],
        *,
        max_read_bytes: int = 64 * 1024 * 1024,
        max_write_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self._roots = tuple(Path(root).resolve() for root in allowed_roots)
        self._max_read_bytes = max_read_bytes
        self._max_write_bytes = max_write_bytes

    async def stat(self, path: str) -> FileStat:
        return await asyncio.to_thread(self._stat_sync, path, False)

    async def list(self, path: str) -> tuple[FileStat, ...]:
        return await asyncio.to_thread(self._list_sync, path)

    async def read(self, path: str, byte_range: ByteRange) -> bytes:
        return await asyncio.to_thread(self._read_sync, path, byte_range)

    async def write(
        self,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None,
    ) -> FileStat:
        if len(data) > self._max_write_bytes:
            raise ValueError("file write exceeds configured limit")
        return await asyncio.to_thread(
            self._write_sync, path, data, expected_sha256
        )

    async def move(self, source: str, destination: str) -> None:
        await asyncio.to_thread(self._move_sync, source, destination)

    async def delete(self, path: str, *, recursive: bool) -> bool:
        return await asyncio.to_thread(self._delete_sync, path, recursive)

    def _stat_sync(self, path: str, include_digest: bool) -> FileStat:
        candidate = self._existing_path(path, follow_symlinks=False)
        metadata = candidate.lstat()
        if stat_module.S_ISLNK(metadata.st_mode):
            kind = FileKind.SYMLINK
            size = metadata.st_size
            digest = None
        elif stat_module.S_ISDIR(metadata.st_mode):
            kind = FileKind.DIRECTORY
            size = metadata.st_size
            digest = None
        elif stat_module.S_ISREG(metadata.st_mode):
            kind = FileKind.FILE
            size = metadata.st_size
            digest = self._digest(candidate) if include_digest else None
        else:
            raise ValueError("unsupported filesystem object")
        return FileStat(
            path=str(candidate),
            kind=kind,
            size_bytes=size,
            modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
            mode=stat_module.S_IMODE(metadata.st_mode),
            sha256=digest,
        )

    def _list_sync(self, path: str) -> tuple[FileStat, ...]:
        directory = self._existing_path(path, follow_symlinks=True)
        if not directory.is_dir():
            raise ValueError("file list path is not a directory")
        return tuple(
            self._stat_sync(str(child), False)
            for child in sorted(directory.iterdir(), key=lambda item: item.name)
        )

    def _read_sync(self, path: str, byte_range: ByteRange) -> bytes:
        candidate = self._existing_path(path, follow_symlinks=True)
        if not candidate.is_file():
            raise ValueError("file read path is not a regular file")
        size = candidate.stat().st_size
        requested = (
            max(0, size - byte_range.offset)
            if byte_range.length is None
            else byte_range.length
        )
        if requested > self._max_read_bytes:
            raise ValueError("file read exceeds configured limit")
        with candidate.open("rb") as handle:
            handle.seek(byte_range.offset)
            return handle.read(requested)

    def _write_sync(
        self, path: str, data: bytes, expected_sha256: str | None
    ) -> FileStat:
        candidate = self._new_path(path)
        if expected_sha256 is not None:
            if (
                not candidate.exists()
                or not candidate.is_file()
                or candidate.is_symlink()
                or self._existing_path(path, follow_symlinks=True) != candidate
                or self._digest(candidate) != expected_sha256
            ):
                raise FileConflictError("file content digest does not match")
        temporary = candidate.with_name(f".{candidate.name}.agentbox-{uuid4().hex}")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, candidate)
            directory_fd = os.open(candidate.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()
        return self._stat_sync(str(candidate), True)

    def _move_sync(self, source: str, destination: str) -> None:
        source_path = self._existing_path(source, follow_symlinks=False)
        destination_path = self._new_path(destination)
        os.replace(source_path, destination_path)

    def _delete_sync(self, path: str, recursive: bool) -> bool:
        candidate = self._existing_path(path, follow_symlinks=False)
        if candidate in self._roots:
            raise ValueError("cannot delete an allowed filesystem root")
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
            return True
        if candidate.is_dir():
            if recursive:
                shutil.rmtree(candidate)
            else:
                candidate.rmdir()
            return True
        return False

    def _existing_path(self, path: str, *, follow_symlinks: bool) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError("filesystem path must be absolute")
        if follow_symlinks:
            resolved = candidate.resolve(strict=True)
            self._require_allowed(resolved)
            return resolved
        parent = candidate.parent.resolve(strict=True)
        resolved = parent / candidate.name
        if resolved not in self._roots:
            self._require_allowed(parent)
        return resolved

    def _new_path(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError("filesystem path must be absolute")
        parent = candidate.parent.resolve(strict=True)
        self._require_allowed(parent)
        return parent / candidate.name

    def _require_allowed(self, candidate: Path) -> None:
        if not any(
            candidate == root or candidate.is_relative_to(root) for root in self._roots
        ):
            raise ValueError("filesystem path is outside allowed roots")

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
