from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import HTTPException

from agentbox.config import settings
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers import SandboxProvider
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

    record = await store.get_sandbox(sandbox_id)
    if record is None or record.desired_state == "deleted":
        raise HTTPException(status_code=404, detail="Sandbox not found")
    if record.desired_state == "suspended":
        await manager.ensure(sandbox_id, record.to_ensure_request())
    elif (
        record.desired_state != "present"
        or record.observed_state not in {"running", "degraded"}
        or record.observed_generation != record.desired_generation
    ):
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "1"},
            detail={
                "message": "Sandbox lifecycle is transitioning",
                "code": "sandbox_starting",
                "retryable": True,
                "state": record.observed_state,
            },
        )
    lease = None
    claim = None
    deadline = asyncio.get_running_loop().time() + 1.0
    while lease is None and asyncio.get_running_loop().time() < deadline:
        lease = await store.acquire_activity_lease(
            sandbox_id,
            session_id=session_id,
            operation=operation,
            owner=manager.owner,
            ttl_seconds=settings.agentbox_activity_lease_ttl_seconds,
        )
        if lease is not None:
            break
        claim = await store.get_lifecycle_claim(sandbox_id)
        # A claim can commit and disappear between the failed acquisition and
        # this diagnostic read. Retry the DB-only acquisition until the short
        # transition budget expires instead of surfacing a false 503.
        await asyncio.sleep(0.01)
    if lease is None:
        current = await store.get_sandbox(sandbox_id)
        if current is None or current.desired_state == "deleted":
            raise HTTPException(status_code=404, detail="Sandbox not found")
        if (
            session_id is not None
            and await store.get_session(sandbox_id, session_id) is None
        ):
            raise HTTPException(status_code=404, detail="Runtime session not found")
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "1"},
            detail={
                "message": "Sandbox lifecycle is transitioning",
                "code": "sandbox_starting",
                "retryable": True,
                "state": current.observed_state,
                "lifecycle_operation": claim.operation if claim else None,
            },
        )

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


async def provider_lease_renewal_loop(manager: SandboxLifecycleManager) -> None:
    """Renew cloud leases independently from idle/session cleanup."""

    interval = min(max(settings.agentbox_cleanup_interval_seconds, 5), 15)
    while True:
        await asyncio.sleep(interval)
        try:
            await manager.renew_active_provider_leases()
        except Exception:
            logger.exception("AgentBox provider lease renewal pass failed")


async def release_sandbox_compute(provider: SandboxProvider, sandbox_id: str) -> bool:
    """Use optional suspension, falling back to compute deletion for plugins."""

    if isinstance(provider, SandboxReleaseProvider):
        return await provider.release(sandbox_id)
    return await provider.delete(sandbox_id)
