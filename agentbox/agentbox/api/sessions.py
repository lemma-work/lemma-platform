from __future__ import annotations

from fastapi import APIRouter, Depends

from agentbox.auth import require_api_key
from agentbox.lifecycle_manager import SandboxLifecycleManager
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

from .deps import lifecycle_manager, state_store
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
    ) as lease:
        response = await manager.runtime_operation(
            sandbox_id,
            lease.sandbox_generation,
            lambda proxy: proxy.create_session(session_id, request),
        )
        stored = await store.upsert_session(
            sandbox_id,
            session_id,
            cwd=response.cwd,
            env_keys=response.env_keys,
            expected_generation=lease.sandbox_generation,
        )
        if stored is None:
            from agentbox.providers.errors import ProviderError

            raise ProviderError(
                "Sandbox changed while creating the runtime session",
                code="lifecycle_changed",
                retryable=True,
                status_code=409,
                headers={"Retry-After": "1"},
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
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecutePythonResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="python"
    ) as lease:
        stdout, stderr, result, error_name, exit_code = await manager.runtime_operation(
            sandbox_id,
            lease.sandbox_generation,
            lambda proxy: proxy.execute_code(
                request.code,
                request.timeout_seconds,
                session_id=session_id,
            ),
        )
        return ExecutePythonResponse(
            sandbox_id=sandbox_id,
            session_id=session_id,
            stdout=stdout,
            stderr=stderr,
            result=result,
            error_name=error_name,
            exit_code=exit_code,
            status="completed" if exit_code == 0 else "error",
        )


@router.post(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
    response_model=ExecCommandResponse,
)
async def exec_runtime_process_command(
    sandbox_id: str,
    session_id: str,
    request: ExecCommandRequest,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecCommandResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="exec-command"
    ) as lease:
        return await manager.runtime_operation(
            sandbox_id,
            lease.sandbox_generation,
            lambda proxy: proxy.exec_session_process_command(session_id, request),
        )


@router.post(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/stdin",
    response_model=ExecCommandResponse,
)
async def write_runtime_process_stdin(
    sandbox_id: str,
    session_id: str,
    request: WriteStdinRequest,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecCommandResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="stdin"
    ) as lease:
        return await manager.runtime_operation(
            sandbox_id,
            lease.sandbox_generation,
            lambda proxy: proxy.write_session_process_stdin(session_id, request),
        )


@router.get(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/processes",
    response_model=ListProcessesResponse,
)
async def list_runtime_processes(
    sandbox_id: str,
    session_id: str,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ListProcessesResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="list-processes"
    ) as lease:
        return await manager.runtime_operation(
            sandbox_id,
            lease.sandbox_generation,
            lambda proxy: proxy.list_session_processes(session_id),
        )


@router.delete(
    "/sandboxes/{sandbox_id}/sessions/{session_id}/processes/{process_id}",
    response_model=ExecCommandResponse,
)
async def terminate_runtime_process(
    sandbox_id: str,
    session_id: str,
    process_id: str,
    store: AsyncStateStore = Depends(state_store),
    manager: SandboxLifecycleManager = Depends(lifecycle_manager),
) -> ExecCommandResponse:
    validate_sandbox_id(sandbox_id)
    async with activity_lease(
        store, manager, sandbox_id, session_id=session_id, operation="terminate"
    ) as lease:
        return await manager.runtime_operation(
            sandbox_id,
            lease.sandbox_generation,
            lambda proxy: proxy.terminate_session_process(session_id, process_id),
        )
