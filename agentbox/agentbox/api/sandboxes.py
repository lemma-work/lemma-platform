from __future__ import annotations

from fastapi import APIRouter, Depends

from agentbox.auth import require_api_key
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
from agentbox.state import AgentBoxStateStore

from .deps import sandbox_provider, state_store
from .lifecycle import release_sandbox_compute

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.put("/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def ensure_sandbox(
    sandbox_id: str,
    request: SandboxEnsureRequest,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AgentBoxStateStore = Depends(state_store),
) -> SandboxResponse:
    validate_sandbox_id(sandbox_id)
    store.upsert_sandbox(sandbox_id, request)
    status = await provider.create(sandbox_id, request)
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
    store: AgentBoxStateStore = Depends(state_store),
) -> SandboxHeartbeatResponse:
    """Keep a sandbox alive while a long-running workload (e.g. a JOB function)
    is using it but holds no runtime session.

    The idle reaper deletes a sandbox once it has had no sessions for
    ``agentbox_sandbox_idle_timeout_seconds``. A workload that runs through the
    function_executor app (not a runtime session) would otherwise be reaped
    mid-run, so the caller heartbeats the sandbox to reset its idle clock. This
    only touches manager state -- it never re-provisions the pod.
    """
    validate_sandbox_id(sandbox_id)
    store.mark_sandbox_active(sandbox_id)
    return SandboxHeartbeatResponse(sandbox_id=sandbox_id, active=True)


@router.post(
    "/sandboxes/{sandbox_id}/suspend",
    response_model=SuspendResponse,
)
async def suspend_sandbox(
    sandbox_id: str,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AgentBoxStateStore = Depends(state_store),
) -> SuspendResponse:
    """Release idle compute while retaining the logical user sandbox."""

    validate_sandbox_id(sandbox_id)
    suspended = await release_sandbox_compute(provider, sandbox_id)
    store.mark_pod_stopped(sandbox_id)
    return SuspendResponse(sandbox_id=sandbox_id, suspended=suspended)


@router.delete("/sandboxes/{sandbox_id}", response_model=DeleteResponse)
async def delete_sandbox(
    sandbox_id: str,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AgentBoxStateStore = Depends(state_store),
) -> DeleteResponse:
    validate_sandbox_id(sandbox_id)
    deleted = await provider.delete(sandbox_id)
    store.delete_sandbox(sandbox_id)
    return DeleteResponse(sandbox_id=sandbox_id, deleted=deleted)
