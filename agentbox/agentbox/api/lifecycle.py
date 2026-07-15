from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import HTTPException

from agentbox.config import settings
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers import SandboxProvider
from agentbox.providers.errors import ProviderError
from agentbox.providers.protocol import SandboxReleaseProvider
from agentbox.state_store.protocol import AsyncStateStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def activity_lease(
    store: AsyncStateStore,
    manager: SandboxLifecycleManager,
    sandbox_id: str,
    *,
    session_id: str | None,
    operation: str,
):
    """Hold a renewable durable lease for the entire proxied operation."""

    async with manager.claim(sandbox_id, f"activity:{operation}"):
        record = await store.get_sandbox(sandbox_id)
        if record is None or record.desired_state == "deleted":
            raise HTTPException(status_code=404, detail="Sandbox not found")
        if record.desired_state == "suspended":
            await manager.resume_claimed(sandbox_id)
        else:
            await manager.bind_exact_generation_claimed(record)
        lease = await store.acquire_activity_lease(
            sandbox_id,
            session_id=session_id,
            operation=operation,
            owner=manager.owner,
            ttl_seconds=settings.agentbox_activity_lease_ttl_seconds,
        )
    if lease is None:
        detail = "Runtime session not found" if session_id else "Sandbox not found"
        raise HTTPException(status_code=404, detail=detail)

    await _renew_provider_lease(manager, sandbox_id, lease.lease_id)
    renewal = asyncio.create_task(
        _renew_activity_lease(
            store,
            manager,
            lease.lease_id,
            asyncio.current_task(),
        )
    )
    try:
        yield lease
    finally:
        renewal.cancel()
        try:
            await renewal
        except asyncio.CancelledError:
            pass
        await store.release_activity_lease(lease.lease_id, owner=manager.owner)


async def _renew_activity_lease(
    store: AsyncStateStore,
    manager: SandboxLifecycleManager,
    lease_id: str,
    owner_task: asyncio.Task | None,
) -> None:
    interval = max(settings.agentbox_activity_lease_ttl_seconds / 3, 1.0)
    while True:
        await asyncio.sleep(interval)
        renewed = await store.renew_activity_lease(
            lease_id,
            owner=manager.owner,
            ttl_seconds=settings.agentbox_activity_lease_ttl_seconds,
        )
        if renewed is None:
            logger.error("agentbox_activity_lease_lost lease_id=%s", lease_id)
            if owner_task is not None:
                owner_task.cancel()
            return
        await _renew_provider_lease(manager, renewed.sandbox_id, lease_id)


async def _renew_provider_lease(
    manager: SandboxLifecycleManager,
    sandbox_id: str,
    lease_id: str,
) -> None:
    """Best-effort provider renewal; the durable activity lease remains primary."""

    try:
        await manager.renew_provider_lease(sandbox_id)
    except (ProviderError, HTTPException) as exc:
        logger.warning(
            "agentbox_provider_lease_renewal_failed lease_id=%s sandbox_id=%s error=%s",
            lease_id,
            sandbox_id,
            exc,
        )


async def delete_runtime_session_if_present(
    provider: SandboxProvider,
    sandbox_id: str,
    session_id: str,
) -> bool:
    try:
        return await provider.delete_session(sandbox_id, session_id)
    except HTTPException as exc:
        if exc.status_code in {404, 409, 502}:
            return False
        raise


async def cleanup_loop(manager: SandboxLifecycleManager) -> None:
    while True:
        await asyncio.sleep(settings.agentbox_cleanup_interval_seconds)
        try:
            await cleanup_once(manager)
        except Exception:
            logger.exception("AgentBox cleanup pass failed")


async def cleanup_once(manager: SandboxLifecycleManager) -> None:
    store = manager.store
    await store.prune_expired_activity_leases()
    for session in await store.expired_sessions(
        settings.agentbox_session_idle_timeout_seconds
    ):
        await manager.delete_session_if_idle(
            session.sandbox_id,
            session.session_id,
        )

    for sandbox in await store.idle_sandboxes(
        settings.agentbox_sandbox_idle_timeout_seconds
    ):
        await manager.suspend_if_idle(sandbox.sandbox_id)


async def release_sandbox_compute(provider: SandboxProvider, sandbox_id: str) -> bool:
    """Use optional suspension, falling back to compute deletion for plugins."""

    if isinstance(provider, SandboxReleaseProvider):
        return await provider.release(sandbox_id)
    return await provider.delete(sandbox_id)
