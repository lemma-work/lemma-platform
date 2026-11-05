"""Reporting a sandbox's state without provisioning it.

A free function rather than part of `SandboxService` so the state machine stays
under the architecture ratchet's file-size limit; it needs only the database
and one provider inspect, none of the service's own state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.providers.base import SandboxProvider


async def describe_sandbox(
    sandbox_id: UUID,
    *,
    uow_factory: UnitOfWorkFactory,
    provider: SandboxProvider,
) -> SandboxInfo | None:
    """Current state, in the legacy ``SandboxInfo`` shape.

    Callers that only want to know whether an ensure is needed must not start a
    container by asking.
    """
    async with uow_factory() as uow:
        repository = SandboxRepository(uow)
        sandbox = await repository.get(sandbox_id)
        if sandbox is None:
            return None
        instance = await repository.current_instance(sandbox_id)

    running = False
    if instance is not None and instance.provider_id:
        found = await provider.inspect(
            instance.provider_id,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15),
        )
        running = found is not None and found.running

    return SandboxInfo(
        sandbox_id=str(sandbox.id),
        name=instance.provider_id if instance else str(sandbox.id),
        status="RUNNING" if running else "STOPPED",
        image="",
        created_at=None,
        endpoint=f"sandbox://{sandbox.id}",
        allocation_id=str(sandbox.id),
        allocation_epoch=sandbox.epoch,
        storage_generation=sandbox.storage_generation,
    )
