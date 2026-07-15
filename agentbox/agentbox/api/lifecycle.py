from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from agentbox.config import settings
from agentbox.providers import SandboxProvider
from agentbox.providers.protocol import SandboxReleaseProvider
from agentbox.state import AgentBoxStateStore

logger = logging.getLogger(__name__)


async def begin_tracked_operation(
    store: AgentBoxStateStore,
    sandbox_id: str,
    session_id: str,
) -> None:
    if not store.begin_operation(sandbox_id, session_id):
        raise HTTPException(status_code=404, detail="Runtime session not found")


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


async def cleanup_loop(provider: SandboxProvider, store: AgentBoxStateStore) -> None:
    while True:
        await asyncio.sleep(settings.agentbox_cleanup_interval_seconds)
        try:
            await cleanup_once(provider, store)
        except Exception:
            logger.exception("AgentBox cleanup pass failed")


async def cleanup_once(provider: SandboxProvider, store: AgentBoxStateStore) -> None:
    for session in store.expired_sessions(settings.agentbox_session_idle_timeout_seconds):
        await delete_runtime_session_if_present(
            provider,
            session.sandbox_id,
            session.session_id,
        )
        store.delete_session(session.sandbox_id, session.session_id)

    for sandbox in store.idle_sandboxes(settings.agentbox_sandbox_idle_timeout_seconds):
        await release_sandbox_compute(provider, sandbox.sandbox_id)
        store.mark_pod_stopped(sandbox.sandbox_id)


async def release_sandbox_compute(
    provider: SandboxProvider, sandbox_id: str
) -> bool:
    """Use optional suspension, falling back to compute deletion for plugins."""

    if isinstance(provider, SandboxReleaseProvider):
        return await provider.release(sandbox_id)
    return await provider.delete(sandbox_id)
