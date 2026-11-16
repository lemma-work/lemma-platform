from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import stat as stat_module
from typing import BinaryIO
from uuid import uuid4

from sandbox_runtime.protocol import ByteRange, FileKind, FileStat


class FileConflictError(RuntimeError):
    pass


class FileTooLargeError(RuntimeError):
    pass


class ProtectedPathError(ValueError):
    """A change to a protected path, however the path was spelled."""


class FilesystemManager:
    """Files under the allowed roots.

    ``protected_paths`` (the browser profile, in the runtime) may not be
    written, moved, deleted or created -- nor anything that contains them --
    and may not be read through a path that only *resolves* there. Checked
    here on the resolved path, because the API checks the path as spelled:
    ``~/lemma/p -> ~/.lemma/browser/profile`` got a write past it.
    """

    def __init__(
        self,
        allowed_roots: tuple[str, ...],
        *,
        max_read_bytes: int = 256 * 1024 * 1024,
        max_write_bytes: int = 256 * 1024 * 1024,
        protected_paths: tuple[str, ...] = (),
    ) -> None:
        self._roots = tuple(Path(root).resolve() for root in allowed_roots)
        self._protected = tuple(protected_paths)
        self._max_read_bytes = max_read_bytes
        self._max_write_bytes = max_write_bytes

    async def stat(self, path: str) -> FileStat:
        return await asyncio.to_thread(self._stat_sync, path, False)

    async def create_directory(self, path: str) -> None:
        await asyncio.to_thread(self._create_directory_sync, path)

    async def list(self, path: str) -> tuple[FileStat, ...]:
        return await asyncio.to_thread(self._list_sync, path)

    async def open_read(self, path: str, byte_range: ByteRange) -> AsyncIterator[bytes]:
        handle, remaining = await asyncio.to_thread(
            self._prepare_read_sync, path, byte_range
        )

        async def chunks() -> AsyncIterator[bytes]:
            unread = remaining
            try:
                while unread:
                    chunk = await asyncio.to_thread(
                        handle.read, min(unread, 1024 * 1024)
                    )
                    if not chunk:
                        break
                    unread -= len(chunk)
                    yield chunk
            finally:
                await asyncio.to_thread(handle.close)

        return chunks()

    async def read(self, path: str, byte_range: ByteRange) -> bytes:
        stream = await self.open_read(path, byte_range)
        return b"".join([chunk async for chunk in stream])

    async def write(
        self,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None,
    ) -> FileStat:
        async def one_chunk() -> AsyncIterator[bytes]:
            yield data

        return await self.write_stream(
            path,
            one_chunk(),
            expected_sha256=expected_sha256,
        )

    async def write_stream(
        self,
        path: str,
        data: AsyncIterable[bytes],
        *,
        expected_sha256: str | None,
        mode: int | None = None,
    ) -> FileStat:
        candidate, temporary, handle = await asyncio.to_thread(
            self._prepare_write_sync, path, expected_sha256, mode
        )
        digest = hashlib.sha256()
        size = 0
        committed = False
        try:
            async for chunk in data:
                if not chunk:
                    continue
                size += len(chunk)
                if size > self._max_write_bytes:
                    raise FileTooLargeError("file write exceeds configured limit")
                payload = bytes(chunk)
                digest.update(payload)
                await asyncio.to_thread(handle.write, payload)
            await asyncio.to_thread(
                self._commit_write_sync,
                handle,
                temporary,
                candidate,
            )
            committed = True
        finally:
            if not committed:
                await asyncio.to_thread(
                    self._abort_write_sync,
                    handle,
                    temporary,
                )
        result = await asyncio.to_thread(self._stat_sync, str(candidate), False)
        return replace(result, sha256=f"sha256:{digest.hexdigest()}")

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

    def _create_directory_sync(self, path: str) -> None:
        candidate = self._new_path(path)
        self._refuse_protected(candidate)
        if candidate.exists() and not candidate.is_dir():
            raise ValueError("directory path conflicts with a non-directory")
        candidate.mkdir(parents=True, exist_ok=True)

    def _list_sync(self, path: str) -> tuple[FileStat, ...]:
        directory = self._existing_path(path, follow_symlinks=True)
        self._refuse_protected_through_link(path, directory)
        if not directory.is_dir():
            raise ValueError("file list path is not a directory")
        return tuple(
            self._stat_sync(str(child), False)
            for child in sorted(directory.iterdir(), key=lambda item: item.name)
        )

    def _prepare_read_sync(
        self, path: str, byte_range: ByteRange
    ) -> tuple[BinaryIO, int]:
        candidate = self._existing_path(path, follow_symlinks=True)
        self._refuse_protected_through_link(path, candidate)
        if not candidate.is_file():
            raise ValueError("file read path is not a regular file")
        size = candidate.stat().st_size
        available = max(0, size - byte_range.offset)
        requested = (
            available
            if byte_range.length is None
            else min(available, byte_range.length)
        )
        if requested > self._max_read_bytes:
            raise FileTooLargeError("file read exceeds configured limit")
        handle = candidate.open("rb")
        handle.seek(byte_range.offset)
        return handle, requested

    def _prepare_write_sync(
        self, path: str, expected_sha256: str | None, mode: int | None = None
    ) -> tuple[Path, Path, BinaryIO]:
        candidate = self._new_path(path)
        self._refuse_protected(candidate)
        if expected_sha256 is not None:
            if (
                not candidate.exists()
                or not candidate.is_file()
                or candidate.is_symlink()
                or self._existing_path(path, follow_symlinks=True) != candidate
                or self._digest(candidate) != expected_sha256
            ):
                raise FileConflictError("file content digest does not match")
        temporary = candidate.with_name(f".{candidate.name}.lemma-{uuid4().hex}")
        handle = temporary.open("xb")
        if mode is not None:
            # Applied to the temporary, before `os.replace` makes the path
            # visible: a file that is briefly world-readable is world-readable.
            # This is how a delivered secret gets 0600 -- without it the write
            # took the process umask, and on Desktop the browser relay token
            # landed 0644 while Docker and E2B both wrote it 0600.
            os.fchmod(handle.fileno(), mode)
        return candidate, temporary, handle

    @staticmethod
    def _commit_write_sync(
        handle: BinaryIO,
        temporary: Path,
        candidate: Path,
    ) -> None:
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, candidate)
        directory_fd = os.open(candidate.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _abort_write_sync(handle: BinaryIO, temporary: Path) -> None:
        if not handle.closed:
            handle.close()
        if temporary.exists():
            temporary.unlink()

    def _move_sync(self, source: str, destination: str) -> None:
        source_path = self._existing_path(source, follow_symlinks=False)
        self._refuse_protected(source_path)
        destination_path = self._new_path(destination)
        self._refuse_protected(destination_path)
        os.replace(source_path, destination_path)

    def _delete_sync(self, path: str, recursive: bool) -> bool:
        candidate = self._existing_path(path, follow_symlinks=False)
        self._refuse_protected(candidate)
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
        # Creating an allowed root is an idempotent operation. Requiring its
        # parent to be allowed would incorrectly reject a root like
        # "/home/user", because "/" is intentionally outside the runtime's
        # filesystem capability.
        if candidate in self._roots:
            return candidate
        parent = candidate.parent
        ancestor = parent
        while not ancestor.exists():
            if ancestor == ancestor.parent:
                raise ValueError("filesystem path has no allowed existing ancestor")
            ancestor = ancestor.parent
        self._require_allowed(ancestor.resolve(strict=True))
        parent.mkdir(parents=True, exist_ok=True)
        parent = parent.resolve(strict=True)
        self._require_allowed(parent)
        return parent / candidate.name

    def _protected_roots(self) -> tuple[Path, ...]:
        # Resolved on every call: the profile may be created, or replaced by a
        # link, after the runtime starts.
        return tuple(Path(path).resolve() for path in self._protected)

    def _refuse_protected(self, resolved: Path) -> None:
        """``resolved`` (its parent resolved, for a link not followed) is, is
        inside, or contains a protected path."""
        for protected in self._protected_roots():
            if (
                resolved == protected
                or resolved.is_relative_to(protected)
                or protected.is_relative_to(resolved)
            ):
                raise ProtectedPathError(
                    "that path is the browser's profile, which holds the "
                    "machine's logins"
                )

    def _refuse_protected_through_link(self, spelled: str, resolved: Path) -> None:
        """A read that reaches a protected path only by following a link.

        Reading the profile by its own name is the API's to refuse (and the
        runtime's own callers may); reaching it through ``~/lemma/p`` is a
        way around that refusal, and is refused here.
        """
        named = Path(os.path.normpath(spelled))
        for protected, given in zip(self._protected_roots(), self._protected):
            inside = resolved == protected or resolved.is_relative_to(protected)
            named_inside = named.is_relative_to(Path(given)) or named.is_relative_to(
                protected
            )
            if inside and not named_inside:
                raise ProtectedPathError(
                    "that path leads into the browser's profile, which holds "
                    "the machine's logins"
                )

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
