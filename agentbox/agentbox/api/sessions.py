from __future__ import annotations

from fastapi import APIRouter, Depends

from agentbox.auth import require_api_key
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers import SandboxProvider
from agentbox.sandbox_ids import validate_sandbox_id
from agentbox.schemas import (
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonRequest,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionHeartbeatResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    SandboxEnsureRequest,
    WriteStdinRequest,
)
from agentbox.state_store.protocol import AsyncStateStore

from .deps import lifecycle_manager, sandbox_provider, state_store
from .lifecycle import activity_lease

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.put(
    "/sandboxes/{sandbox_id}/sessions/{session_id}",
    response_model=RuntimeSessionResponse,
)
async def create_runtime_session(
    sandbox_id: str,
    session_id: str,
    request: RuntimeSessionRequest,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> RuntimeSessionResponse:
    validate_sandbox_id(sandbox_id)
    sandbox_record = await store.get_sandbox(sandbox_id)
    await manager.ensure(
        sandbox_id,
        sandbox_record.to_ensure_request()
        if sandbox_record is not None
        else SandboxEnsureRequest(),
    )
    async with activity_lease(
        store,
        manager,
        sandbox_id,
        session_id=None,
        operation="create-session",
    ):
        response = await provider.create_session(sandbox_id, session_id, request)
        await store.upsert_session(
            sandbox_id,
            session_id,
            cwd=response.cwd,
            env_keys=response.env_keys,
        )
    return response


@router.post(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/heartbeat",
    response_model=RuntimeSessionHeartbeatResponse,
)
async def heartbeat_runtime_session(
    sandbox_id: str,
    session_id: str,
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> RuntimeSessionHeartbeatResponse:
    validate_sandbox_id(sandbox_id)
    active = await manager.heartbeat_session(sandbox_id, session_id)
    if not active:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Runtime session not found")
    return RuntimeSessionHeartbeatResponse(
        sandbox_id=sandbox_id,
        session_id=session_id,
        active=True,
    )


@router.delete("/sandboxes/{sandbox_id}/sessions/{session_id}")
async def delete_runtime_session(
    sandbox_id: str,
    session_id: str,
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> dict[str, str | bool]:
    validate_sandbox_id(sandbox_id)
    deleted = await manager.delete_session(sandbox_id, session_id)
    return {"sandbox_id": sandbox_id, "session_id": session_id, "deleted": deleted}


@router.post(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/python",
    response_model=ExecutePythonResponse,
)
async def execute_python(
    sandbox_id: str,
    session_id: str,
    request: ExecutePythonRequest,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecutePythonResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="python"
    ):
        return await provider.execute_code(
            sandbox_id,
            session_id,
            request.code,
            request.timeout_seconds,
        )


@router.post(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
    response_model=ExecCommandResponse,
)
async def exec_runtime_process_command(
    sandbox_id: str,
    session_id: str,
    request: ExecCommandRequest,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecCommandResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="exec-command"
    ):
        return await provider.exec_session_process_command(
            sandbox_id,
            session_id,
            request,
        )


@router.post(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/stdin",
    response_model=ExecCommandResponse,
)
async def write_runtime_process_stdin(
    sandbox_id: str,
    session_id: str,
    request: WriteStdinRequest,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecCommandResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="stdin"
    ):
        return await provider.write_session_process_stdin(
            sandbox_id, session_id, request
        )


@router.get(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/processes",
    response_model=ListProcessesResponse,
)
async def list_runtime_processes(
    sandbox_id: str,
    session_id: str,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ListProcessesResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="list-processes"
    ):
        return await provider.list_session_processes(sandbox_id, session_id)


@router.delete(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/processes/{process_id}",
    response_model=ExecCommandResponse,
)
async def terminate_runtime_process(
    sandbox_id: str,
    session_id: str,
    process_id: str,
    provider: SandboxProvider = Depends(sandbox_provider),
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecCommandResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="terminate"
    ):
        return await provider.terminate_session_process(
            sandbox_id, session_id, process_id
        )
