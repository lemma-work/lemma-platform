"""The filesystem and Python-session half of the in-process sandbox client.

Split from the client itself only by subject: the whole class exists to present
the `SandboxClient` surface without a network hop, and these are the calls that
never touch process or port lifecycle.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from collections.abc import AsyncIterable, AsyncIterator
from uuid import UUID

from sandbox_runtime.errors import SandboxUnavailable
from app.modules.workspace.providers.base import ProviderGone

from sandbox_runtime.protocol import (
    ByteRange,
    EnvironmentVariable,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    PythonResult,
)


@dataclass(frozen=True, slots=True)
class LocalPythonSessionRef:
    """Only the session id travels; the runtime keys sessions by it."""

    session_id: UUID


class LocalSandboxFilesMixin:
    """File and Python-session operations against a local sandbox."""

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def stat_file(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> FileStat:
        _, instance = await self._instance(logical_id)
        return await self._provider.stat_file(
            instance, path=path, deadline_at=deadline_at
        )

    async def list_files(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        _, instance = await self._instance(logical_id)
        return await self._provider.list_files(
            instance, path=path, deadline_at=deadline_at
        )

    async def create_directory(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> None:
        _, instance = await self._instance(logical_id)
        await self._provider.create_directory(
            instance, path=path, deadline_at=deadline_at
        )

    async def read_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        chunks: list[bytes] = []
        async with self.stream_file(
            logical_id, path, offset=offset, length=length, deadline_at=deadline_at
        ) as stream:
            async for chunk in stream:
                chunks.append(chunk)
        return b"".join(chunks)

    @asynccontextmanager
    async def stream_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        _, instance = await self._instance(logical_id)
        yield self._provider.open_file(
            instance,
            path=path,
            byte_range=ByteRange(offset=offset, length=length),
            deadline_at=deadline_at,
        )

    async def write_file(
        self,
        logical_id: UUID,
        path: str,
        data: bytes,
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> FileStat:
        async def one_chunk() -> AsyncIterator[bytes]:
            yield data

        return await self.write_file_stream(
            logical_id,
            path,
            one_chunk(),
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )

    async def write_file_stream(
        self,
        logical_id: UUID,
        path: str,
        data: AsyncIterable[bytes],
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> FileStat:
        _, instance = await self._instance(logical_id)
        return await self._provider.write_file(
            instance,
            path=path,
            data=data,
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )

    async def move_file(
        self,
        logical_id: UUID,
        source: str,
        destination: str,
        *,
        deadline_at: datetime,
    ) -> None:
        _, instance = await self._instance(logical_id)
        await self._provider.move_file(
            instance, source=source, destination=destination, deadline_at=deadline_at
        )

    async def delete_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        recursive: bool = False,
    ) -> None:
        _, instance = await self._instance(logical_id)
        await self._provider.delete_file(
            instance, path=path, recursive=recursive, deadline_at=deadline_at
        )

    # ------------------------------------------------------------------
    # Python sessions
    # ------------------------------------------------------------------

    async def create_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        cwd: str,
        environment_keys: tuple[str, ...] = (),
        deadline_at: datetime,
    ) -> LocalPythonSessionRef:
        _, instance = await self._instance(logical_id)
        await self._provider.ensure_python_session(
            instance,
            CreatePythonSessionRequest(
                session_id=session_id,
                cwd=cwd,
                environment_keys=environment_keys,
                deadline_at=deadline_at,
            ),
        )
        return LocalPythonSessionRef(session_id=session_id)

    async def execute_python(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        operation_id: UUID,
        code: str,
        environment: tuple[EnvironmentVariable, ...] = (),
        output_limit_bytes: int = 1024 * 1024,
        deadline_at: datetime,
    ) -> PythonResult:
        _, instance = await self._instance(logical_id)
        return await self._provider.execute_python(
            instance,
            LocalPythonSessionRef(session_id=session_id),
            ExecutePythonRequest(
                operation_id=operation_id,
                code=code,
                environment=environment,
                output_limit_bytes=output_limit_bytes,
                deadline_at=deadline_at,
            ),
        )

    async def delete_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            _, instance = await self._instance(logical_id)
            await self._provider.delete_python_session(
                instance, session_id=str(session_id), deadline_at=deadline_at
            )
        except ProviderGone, SandboxUnavailable:
            # Closing a session against a sandbox that is already gone has
            # achieved what it was asking for.
            return

    async def restart_python_session(
        self, logical_id: UUID, session_id: UUID, *, deadline_at: datetime
    ) -> LocalPythonSessionRef:
        # Deleting is enough: the next execute recreates the session lazily
        # from its deterministic id, so there is no separate restart path to
        # keep correct.
        await self.delete_python_session(
            logical_id, session_id, deadline_at=deadline_at
        )
        return LocalPythonSessionRef(session_id=session_id)
