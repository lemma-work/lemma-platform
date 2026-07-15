from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agentbox.auth import require_api_key
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers import SandboxProvider
from agentbox.sandbox_ids import validate_sandbox_id
from agentbox.schemas import (
    DeleteResponse,
    SandboxEnsureRequest,
    SandboxHeartbeatResponse,
    SandboxResponse,
    SandboxSummary,
    SuspendResponse,
    sandbox_summary,
)

from .deps import lifecycle_manager, sandbox_provider

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.put("/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def ensure_sandbox(
    sandbox_id: str,
    request: SandboxEnsureRequest,
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> SandboxResponse:
    validate_sandbox_id(sandbox_id)
    status = await manager.ensure(sandbox_id, request)
    return SandboxResponse(sandbox=sandbox_summary(status))


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxSummary)
async def get_sandbox(
    sandbox_id: str,
    provider: SandboxProvider = Depends(sandbox_provider),
) -> SandboxSummary:
    validate_sandbox_id(sandbox_id)
    return sandbox_summary(await provider.get_status(sandbox_id))


@router.post(
    "/sandboxes/{sandbox_id}/heartbeat",
    response_model=SandboxHeartbeatResponse,
)
async def heartbeat_sandbox(
    sandbox_id: str,
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> SandboxHeartbeatResponse:
    """Keep a sandbox alive while a long-running workload (e.g. a JOB function)
    is using it but holds no runtime session.

    The idle reaper suspends sandbox compute once it has had no sessions for
    ``agentbox_sandbox_idle_timeout_seconds``. A workload that runs through the
    function_executor app (not a runtime session) would otherwise be reaped
    mid-run, so the caller heartbeats the sandbox to reset its idle clock. This
    only touches manager state -- it never re-provisions the pod.
    """
    validate_sandbox_id(sandbox_id)
    active = await manager.heartbeat_sandbox(sandbox_id)
    if not active:
        raise HTTPException(status_code=404, detail="Sandbox is not active")
    return SandboxHeartbeatResponse(sandbox_id=sandbox_id, active=active)


@router.post(
    "/sandboxes/{sandbox_id}/suspend",
    response_model=SuspendResponse,
)
async def suspend_sandbox(
    sandbox_id: str,
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> SuspendResponse:
    """Release idle compute while retaining the logical user sandbox."""

    validate_sandbox_id(sandbox_id)
    suspended = await manager.suspend(sandbox_id)
    return SuspendResponse(sandbox_id=sandbox_id, suspended=suspended)


@router.delete("/sandboxes/{sandbox_id}", response_model=DeleteResponse)
async def delete_sandbox(
    sandbox_id: str,
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> DeleteResponse:
    validate_sandbox_id(sandbox_id)
    deleted = await manager.delete(sandbox_id)
    return DeleteResponse(sandbox_id=sandbox_id, deleted=deleted)
