from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from datetime import datetime, timezone
from typing import TypeVar

from agentbox.domain import (
    AgentBoxError,
    AllocationState,
    ByteRange,
    ErrorCode,
    FileStat,
    RetryDisposition,
    SandboxKey,
)
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderFilesystemConflict,
    ProviderFilesystemNotFound,
    ProviderFilesystemPort,
    ProviderFilesystemRejected,
    ProviderFilesystemUnavailable,
)


ResultT = TypeVar("ResultT")
DEFAULT_MAX_FILE_TRANSFER_BYTES = 256 * 1024 * 1024
MAX_FILE_TRANSFER_BYTES = 2 * 1024 * 1024 * 1024
_PROVIDER_FILESYSTEM_ERRORS = (
    ProviderFilesystemNotFound,
    ProviderFilesystemConflict,
    ProviderFilesystemRejected,
    ProviderFilesystemUnavailable,
)


class FilesystemService:
    def __init__(
        self,
        database: StateDatabase,
        provider: ProviderFilesystemPort,
        *,
        max_transfer_bytes: int = DEFAULT_MAX_FILE_TRANSFER_BYTES,
    ) -> None:
        if max_transfer_bytes < 1:
            raise ValueError("filesystem transfer limit must be positive")
        self._database = database
        self._provider = provider
        self._max_transfer_bytes = max_transfer_bytes

    async def stat(
        self, key: SandboxKey, path: str, *, deadline_at: datetime
    ) -> FileStat:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider_call(
            self._provider.stat_file(allocation, path=path, deadline_at=deadline_at)
        )

    async def list(
        self, key: SandboxKey, path: str, *, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider_call(
            self._provider.list_files(allocation, path=path, deadline_at=deadline_at)
        )

    async def open_read(
        self,
        key: SandboxKey,
        path: str,
        byte_range: ByteRange,
        *,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        allocation = await self._current_allocation(key, deadline_at)
        stream = await self._provider_call(
            self._provider.open_file(
                allocation,
                path=path,
                byte_range=byte_range,
                deadline_at=deadline_at,
            )
        )
        return self._guard_download(stream, deadline_at=deadline_at)

    async def read(
        self,
        key: SandboxKey,
        path: str,
        byte_range: ByteRange,
        *,
        deadline_at: datetime,
    ) -> bytes:
        stream = await self.open_read(
            key,
            path,
            byte_range,
            deadline_at=deadline_at,
        )
        return b"".join([chunk async for chunk in stream])

    async def write(
        self,
        key: SandboxKey,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        async def one_chunk() -> AsyncIterator[bytes]:
            yield data

        return await self.write_stream(
            key,
            path,
            one_chunk(),
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )

    async def write_stream(
        self,
        key: SandboxKey,
        path: str,
        data: AsyncIterable[bytes],
        *,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider_call(
            self._provider.write_file(
                allocation,
                path=path,
                data=self._guard_upload(data, deadline_at=deadline_at),
                expected_sha256=expected_sha256,
                deadline_at=deadline_at,
            )
        )

    async def move(
        self,
        key: SandboxKey,
        source: str,
        destination: str,
        *,
        deadline_at: datetime,
    ) -> None:
        allocation = await self._current_allocation(key, deadline_at)
        await self._provider_call(
            self._provider.move_file(
                allocation,
                source=source,
                destination=destination,
                deadline_at=deadline_at,
            )
        )

    async def delete(
        self,
        key: SandboxKey,
        path: str,
        *,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider_call(
            self._provider.delete_file(
                allocation,
                path=path,
                recursive=recursive,
                deadline_at=deadline_at,
            )
        )

    @staticmethod
    async def _provider_call(operation: Awaitable[ResultT]) -> ResultT:
        try:
            return await operation
        except _PROVIDER_FILESYSTEM_ERRORS as exc:
            raise FilesystemService._public_error(exc) from exc

    async def _guard_upload(
        self, stream: AsyncIterable[bytes], *, deadline_at: datetime
    ) -> AsyncIterator[bytes]:
        transferred = 0
        async for chunk in stream:
            self._check_deadline(deadline_at)
            if not chunk:
                continue
            transferred += len(chunk)
            if transferred > self._max_transfer_bytes:
                raise AgentBoxError(
                    ErrorCode.INVALID_REQUEST,
                    "filesystem upload exceeds configured limit",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=413,
                )
            yield bytes(chunk)

    async def _guard_download(
        self, stream: AsyncIterator[bytes], *, deadline_at: datetime
    ) -> AsyncIterator[bytes]:
        transferred = 0
        try:
            async for chunk in stream:
                self._check_deadline(deadline_at)
                if not chunk:
                    continue
                transferred += len(chunk)
                if transferred > self._max_transfer_bytes:
                    raise AgentBoxError(
                        ErrorCode.INVALID_REQUEST,
                        "filesystem download exceeds configured limit",
                        retry=RetryDisposition.DO_NOT_RETRY,
                        status_code=413,
                    )
                yield bytes(chunk)
        except _PROVIDER_FILESYSTEM_ERRORS as exc:
            raise self._public_error(exc) from exc
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    @staticmethod
    def _public_error(
        error: ProviderFilesystemNotFound
        | ProviderFilesystemConflict
        | ProviderFilesystemRejected
        | ProviderFilesystemUnavailable,
    ) -> AgentBoxError:
        if isinstance(error, ProviderFilesystemNotFound):
            return AgentBoxError(
                ErrorCode.FILE_NOT_FOUND,
                "filesystem path does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if isinstance(error, ProviderFilesystemConflict):
            return AgentBoxError(
                ErrorCode.FILE_CONFLICT,
                "filesystem operation precondition was not satisfied",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        if isinstance(error, ProviderFilesystemRejected):
            return AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "filesystem operation was rejected",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=error.status_code,
            )
        return AgentBoxError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "filesystem provider is unavailable",
            retry=RetryDisposition.WAIT,
            status_code=503,
            retry_after_ms=error.retry_after_ms,
        )

    async def _current_allocation(
        self, key: SandboxKey, deadline_at: datetime
    ) -> ProviderAllocationRef:
        self._check_deadline(deadline_at)
        async with self._database.uow() as uow:
            allocation = await uow.repository.current_allocation(key)
            await uow.commit()
        if (
            allocation is None
            or allocation.provider_id is None
            or allocation.state != AllocationState.ACTIVE
        ):
            raise AgentBoxError(
                ErrorCode.PROVISIONING,
                "sandbox is not ready for filesystem access",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        return ProviderAllocationRef(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
            allocation_id=allocation.allocation_id,
            allocation_token=allocation.allocation_token,
            key=allocation.key,
        )

    @staticmethod
    def _check_deadline(deadline_at: datetime) -> None:
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "deadline_at must include a timezone",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        if deadline_at <= datetime.now(timezone.utc):
            raise AgentBoxError(
                ErrorCode.DEADLINE_EXCEEDED,
                "filesystem operation deadline has elapsed",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=408,
            )
