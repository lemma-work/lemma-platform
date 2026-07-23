from __future__ import annotations

from datetime import datetime, timezone

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
from agentbox.ports import ProviderAllocationRef, ProviderFilesystemPort


class FilesystemService:
    def __init__(self, database: StateDatabase, provider: ProviderFilesystemPort) -> None:
        self._database = database
        self._provider = provider

    async def stat(
        self, key: SandboxKey, path: str, *, deadline_at: datetime
    ) -> FileStat:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider.stat_file(
            allocation, path=path, deadline_at=deadline_at
        )

    async def list(
        self, key: SandboxKey, path: str, *, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider.list_files(
            allocation, path=path, deadline_at=deadline_at
        )

    async def read(
        self,
        key: SandboxKey,
        path: str,
        byte_range: ByteRange,
        *,
        deadline_at: datetime,
    ) -> bytes:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider.read_file(
            allocation,
            path=path,
            byte_range=byte_range,
            deadline_at=deadline_at,
        )

    async def write(
        self,
        key: SandboxKey,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        allocation = await self._current_allocation(key, deadline_at)
        return await self._provider.write_file(
            allocation,
            path=path,
            data=data,
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
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
        await self._provider.move_file(
            allocation,
            source=source,
            destination=destination,
            deadline_at=deadline_at,
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
        return await self._provider.delete_file(
            allocation,
            path=path,
            recursive=recursive,
            deadline_at=deadline_at,
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
