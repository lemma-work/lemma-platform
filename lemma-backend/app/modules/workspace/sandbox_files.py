"""Reading and writing files inside a sandbox, as one mixin.

Split from ``sandbox_session`` for size — that module sits against the
architecture ratchet's file-size limit — and the file operations are the
coherent half to take: they share a path resolver, they touch no process state,
and they are what the workspace file tools are built on.

A mixin rather than a collaborator because these are the session's own methods
to every caller, and the alternative is a second object that needs the client,
the logical id and the cwd to do anything.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID

from sandbox_runtime.protocol import FileStat


class _SessionInternals(Protocol):
    """What the mixin needs from the session it is mixed into."""

    client: object
    logical_id: UUID

    async def _resolve_path(self, path: str) -> str: ...


class SandboxFileOperationsMixin:
    """File reads and writes against the sandbox runtime."""

    async def stat_file(self, path: str, *, timeout: int = 30) -> FileStat:
        return await self.client.stat_file(
            self.logical_id,
            await self._resolve_path(path),
            deadline_at=self._deadline(timeout),
        )

    async def list_files(self, path: str, *, timeout: int = 30) -> tuple[FileStat, ...]:
        return await self.client.list_files(
            self.logical_id,
            await self._resolve_path(path),
            deadline_at=self._deadline(timeout),
        )

    async def read_file(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
        timeout: int = 60,
    ) -> bytes:
        return await self.client.read_file(
            self.logical_id,
            await self._resolve_path(path),
            offset=offset,
            length=length,
            deadline_at=self._deadline(timeout),
        )

    @asynccontextmanager
    async def stream_file(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
        timeout: int = 60,
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        async with self.client.stream_file(
            self.logical_id,
            await self._resolve_path(path),
            offset=offset,
            length=length,
            deadline_at=self._deadline(timeout),
        ) as stream:
            yield stream

    async def write_file(
        self,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None = None,
        timeout: int = 60,
    ) -> FileStat:
        return await self.client.write_file(
            self.logical_id,
            await self._resolve_path(path),
            data,
            expected_sha256=expected_sha256,
            deadline_at=self._deadline(timeout),
        )

    async def write_file_stream(
        self,
        path: str,
        data: AsyncIterable[bytes],
        *,
        expected_sha256: str | None = None,
        timeout: int = 60,
    ) -> FileStat:
        return await self.client.write_file_stream(
            self.logical_id,
            await self._resolve_path(path),
            data,
            expected_sha256=expected_sha256,
            deadline_at=self._deadline(timeout),
        )

    async def move_file(
        self,
        source: str,
        destination: str,
        *,
        timeout: int = 30,
    ) -> None:
        await self.client.move_file(
            self.logical_id,
            await self._resolve_path(source),
            await self._resolve_path(destination),
            deadline_at=self._deadline(timeout),
        )

    async def delete_file(
        self,
        path: str,
        *,
        recursive: bool = False,
        timeout: int = 30,
    ) -> None:
        await self.client.delete_file(
            self.logical_id,
            await self._resolve_path(path),
            recursive=recursive,
            deadline_at=self._deadline(timeout),
        )

    # There is deliberately no set_cwd/get_cwd here. Every command runs as a
    # fresh process started at `self._cwd`, and this object is rebuilt on each
    # tool call from the conversation's resolved cwd, so there is no shell
    # whose directory could be moved or queried. `pwd` could only ever echo
    # `self._cwd` back. A `cd` inside one command likewise does not carry to
    # the next; the tool descriptions say so.
