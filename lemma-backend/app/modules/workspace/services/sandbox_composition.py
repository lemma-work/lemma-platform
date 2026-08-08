"""Builds the sandbox service and the adapters that sit on it."""

from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from app.modules.workspace.config import workspace_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.domain.sandbox import (
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.services.interfaces import ISandbox
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.provider_factory import build_provider
from app.modules.workspace.services.sandbox_service import SandboxService

_service: SandboxService | None = None
_service_key: tuple[int, str] | None = None


def get_sandbox_service() -> SandboxService:
    """The process-shared sandbox service.

    Keyed by event loop as well as provider: the Docker client underneath owns
    an httpx.AsyncClient, which is bound to the loop that created it.
    """
    global _service, _service_key
    key = (id(asyncio.get_running_loop()), workspace_settings.provider)
    if _service is not None and _service_key == key:
        return _service
    _service = SandboxService(
        provider=build_provider(),
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
    )
    _service_key = key
    return _service


async def reset_sandbox_service() -> None:
    global _service, _service_key
    service, _service = _service, None
    _service_key = None
    if service is not None:
        await service.close()


def build_local_client() -> LocalSandboxClient:
    return LocalSandboxClient(get_sandbox_service())


class LocalSandbox(ISandbox):
    """``ISandbox`` over the local sandbox service.

    Resolves the row before ensuring, because a user whose default workspace
    predates the backfill -- or was created after it -- still has to get one.
    """

    name = "local"

    def __init__(self, service: SandboxService | None = None) -> None:
        self._service = service or get_sandbox_service()

    async def _sandbox_id(self, user_id: UUID) -> UUID:
        sandbox = await self._service.resolve(
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
        )
        return sandbox.id

    async def ensure_sandbox(self, user_id: UUID) -> SandboxInfo:
        handle = await self._service.ensure(await self._sandbox_id(user_id))
        return _to_info(handle)

    async def get_sandbox(self, user_id: UUID) -> Optional[SandboxInfo]:
        """Report what exists. Never provisions.

        The caller uses this to decide whether an ensure is needed at all, so
        provisioning here would make every status read start a container.
        """
        return await self._service.describe(await self._sandbox_id(user_id))

    async def delete_sandbox(self, user_id: UUID) -> None:
        await self._service.destroy(await self._sandbox_id(user_id))

    async def suspend_sandbox(self, user_id: UUID) -> None:
        await self._service.release(await self._sandbox_id(user_id))

    async def is_sandbox_running(self, user_id: UUID) -> bool:
        info = await self.get_sandbox(user_id)
        return info is not None and info.status == "RUNNING"

    async def close(self) -> None:
        # The service is process-shared; it is disposed at lifespan shutdown.
        return None


def _to_info(handle) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(handle.sandbox_id),
        name=handle.provider_id,
        status="RUNNING",
        image="",
        created_at=None,
        endpoint=f"sandbox://{handle.sandbox_id}",
        # The epoch is what the old allocation pair meant: which incarnation of
        # this sandbox a caller is talking to.
        allocation_id=str(handle.sandbox_id),
        allocation_epoch=handle.epoch,
        storage_generation=handle.storage_generation,
    )
