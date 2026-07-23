from __future__ import annotations

from uuid import UUID

from datetime import datetime
import struct

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from agentbox.auth import require_api_key
from agentbox.domain import (
    AgentBoxError,
    ByteRange,
    ErrorCode,
    RetryDisposition,
    SandboxKey,
    WorkloadKind,
)
from agentbox.filesystem import FilesystemService, MAX_FILE_TRANSFER_BYTES
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.processes import ProcessExecutionService
from agentbox.port_access import PortAccessService
from agentbox.python_sessions import PythonSessionService

from .contracts import (
    CreatePythonSessionModel,
    CreatePortAccessRequest,
    DeadlineRequest,
    EnsureSandboxRequest,
    ErrorResponse,
    FileListResponse,
    FileStatResponse,
    MoveFileRequest,
    ExecutePythonModel,
    PythonResultResponse,
    PythonSessionResponse,
    ProcessRefResponse,
    PortAccessResponse,
    ResizeProcessRequest,
    SandboxHandleResponse,
    StartProcessModel,
)
from .deps import (
    filesystem,
    process_execution,
    port_access,
    python_sessions,
    sandbox_lifecycle,
)


router = APIRouter(dependencies=[Depends(require_api_key)])
_OUTPUT_CHANNEL_IDS = {"stdout": 1, "stderr": 2, "pty": 3}


@router.put(
    "/sandboxes/{workload_kind}/{logical_id}",
    response_model=SandboxHandleResponse,
    responses={202: {"model": SandboxHandleResponse}},
)
async def ensure_sandbox(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    request: EnsureSandboxRequest,
    service: SandboxLifecycleService = Depends(sandbox_lifecycle),
) -> Response | SandboxHandleResponse:
    handle = await service.ensure(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        request.profile.to_domain(),
        admission_class=request.admission_class,
        deadline_at=request.deadline_at,
    )
    response = SandboxHandleResponse.from_domain(handle)
    if handle.ready:
        return response
    return JSONResponse(status_code=202, content=jsonable_encoder(response))


@router.get(
    "/sandboxes/{workload_kind}/{logical_id}",
    response_model=SandboxHandleResponse,
)
async def inspect_sandbox(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    service: SandboxLifecycleService = Depends(sandbox_lifecycle),
) -> SandboxHandleResponse:
    handle = await service.inspect(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id)
    )
    if handle is None:
        raise AgentBoxError(
            ErrorCode.SANDBOX_NOT_FOUND,
            "sandbox does not exist",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=404,
        )
    return SandboxHandleResponse.from_domain(handle)


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}:release",
    response_model=SandboxHandleResponse,
)
async def release_sandbox(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    request: DeadlineRequest,
    service: SandboxLifecycleService = Depends(sandbox_lifecycle),
) -> SandboxHandleResponse:
    handle = await service.release(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        deadline_at=request.deadline_at,
    )
    return SandboxHandleResponse.from_domain(handle)


@router.delete(
    "/sandboxes/{workload_kind}/{logical_id}",
    status_code=204,
)
async def destroy_sandbox(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    deadline_at: datetime = Query(),
    service: SandboxLifecycleService = Depends(sandbox_lifecycle),
) -> Response:
    await service.destroy(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        deadline_at=deadline_at,
    )
    return Response(status_code=204)


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}/ports/{port}:access",
    response_model=PortAccessResponse,
)
async def create_port_access(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    port: int,
    request: CreatePortAccessRequest,
    service: PortAccessService = Depends(port_access),
) -> PortAccessResponse:
    grant = await service.create(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        port=port,
        protocol=request.protocol,
        expires_at=request.expires_at,
    )
    return PortAccessResponse.from_domain(grant)


def agentbox_error_response(error: AgentBoxError) -> JSONResponse:
    body = ErrorResponse.from_error(error)
    headers = None
    if error.retry_after_ms is not None:
        seconds = max(1, (error.retry_after_ms + 999) // 1000)
        headers = {"Retry-After": str(seconds)}
    return JSONResponse(
        status_code=error.status_code,
        headers=headers,
        content=jsonable_encoder(body),
    )


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}/processes",
    response_model=ProcessRefResponse,
    status_code=201,
    responses={200: {"model": ProcessRefResponse}},
)
async def start_process(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    request: StartProcessModel,
    service: ProcessExecutionService = Depends(process_execution),
) -> Response | ProcessRefResponse:
    process, created = await service.start(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        request.to_domain(),
    )
    response = ProcessRefResponse.from_domain(process)
    if created:
        return response
    return JSONResponse(status_code=200, content=jsonable_encoder(response))


@router.get(
    "/sandboxes/{workload_kind}/{logical_id}/processes",
    response_model=tuple[ProcessRefResponse, ...],
)
async def list_processes(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    service: ProcessExecutionService = Depends(process_execution),
) -> tuple[ProcessRefResponse, ...]:
    processes = await service.list(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id)
    )
    return tuple(ProcessRefResponse.from_domain(process) for process in processes)


@router.get(
    "/sandboxes/{workload_kind}/{logical_id}/processes/{operation_id}",
    response_model=ProcessRefResponse,
)
async def inspect_process(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    operation_id: UUID,
    service: ProcessExecutionService = Depends(process_execution),
) -> ProcessRefResponse:
    process = await service.inspect(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id), operation_id
    )
    return ProcessRefResponse.from_domain(process)


@router.get("/sandboxes/{workload_kind}/{logical_id}/processes/{operation_id}/output")
async def read_process_output(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    operation_id: UUID,
    deadline_at: datetime = Query(),
    after_seq: int = Query(default=0, ge=0),
    wait_seconds: float = Query(default=0, ge=0, le=30),
    service: ProcessExecutionService = Depends(process_execution),
) -> Response:
    snapshot = await service.read_output(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        operation_id,
        after_sequence=after_seq,
        wait_seconds=wait_seconds,
        deadline_at=deadline_at,
    )
    content = b"".join(
        struct.pack(
            "!QBI",
            chunk.sequence,
            _OUTPUT_CHANNEL_IDS[chunk.channel.value],
            len(chunk.data),
        )
        + chunk.data
        for chunk in snapshot.chunks
    )
    return Response(
        content=content,
        media_type="application/vnd.agentbox.process-output",
        headers={
            "X-AgentBox-Next-Sequence": str(snapshot.next_sequence),
            "X-AgentBox-Truncated-Before": str(
                snapshot.truncated_before_sequence or ""
            ),
            "X-AgentBox-Process-State": snapshot.state.value,
            "X-AgentBox-Exit-Code": (
                str(snapshot.exit_code) if snapshot.exit_code is not None else ""
            ),
        },
    )


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}/processes/{operation_id}:input",
    status_code=204,
)
async def send_process_input(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    operation_id: UUID,
    deadline_at: datetime = Query(),
    data: bytes = Body(media_type="application/octet-stream"),
    service: ProcessExecutionService = Depends(process_execution),
) -> Response:
    await service.send_input(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        operation_id,
        data,
        deadline_at=deadline_at,
    )
    return Response(status_code=204)


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}/processes/{operation_id}:resize",
    status_code=204,
)
async def resize_process(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    operation_id: UUID,
    request: ResizeProcessRequest,
    service: ProcessExecutionService = Depends(process_execution),
) -> Response:
    await service.resize(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        operation_id,
        request.size.to_domain(),
        deadline_at=request.deadline_at,
    )
    return Response(status_code=204)


@router.delete(
    "/sandboxes/{workload_kind}/{logical_id}/processes/{operation_id}",
    response_model=ProcessRefResponse,
)
async def terminate_process(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    operation_id: UUID,
    deadline_at: datetime = Query(),
    grace_seconds: float = Query(default=5, ge=0, le=30),
    service: ProcessExecutionService = Depends(process_execution),
) -> ProcessRefResponse:
    process = await service.terminate(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        operation_id,
        grace_seconds=grace_seconds,
        deadline_at=deadline_at,
    )
    return ProcessRefResponse.from_domain(process)


@router.get(
    "/sandboxes/{workload_kind}/{logical_id}/files:stat",
    response_model=FileStatResponse,
)
async def stat_file(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
    deadline_at: datetime = Query(),
    service: FilesystemService = Depends(filesystem),
) -> FileStatResponse:
    stat = await service.stat(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        path,
        deadline_at=deadline_at,
    )
    return FileStatResponse.from_domain(stat)


@router.put(
    "/sandboxes/{workload_kind}/{logical_id}/directories",
    status_code=204,
)
async def create_directory(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
    deadline_at: datetime = Query(),
    service: FilesystemService = Depends(filesystem),
) -> Response:
    await service.create_directory(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        path,
        deadline_at=deadline_at,
    )
    return Response(status_code=204)


@router.get(
    "/sandboxes/{workload_kind}/{logical_id}/files",
    response_model=FileListResponse,
)
async def list_files(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
    deadline_at: datetime = Query(),
    service: FilesystemService = Depends(filesystem),
) -> FileListResponse:
    entries = await service.list(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        path,
        deadline_at=deadline_at,
    )
    return FileListResponse(
        entries=tuple(FileStatResponse.from_domain(entry) for entry in entries)
    )


@router.get("/sandboxes/{workload_kind}/{logical_id}/files:content")
async def read_file(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
    offset: int = Query(default=0, ge=0),
    length: int | None = Query(default=None, ge=0, le=MAX_FILE_TRANSFER_BYTES),
    deadline_at: datetime = Query(),
    service: FilesystemService = Depends(filesystem),
) -> StreamingResponse:
    stream = await service.open_read(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        path,
        ByteRange(offset=offset, length=length),
        deadline_at=deadline_at,
    )
    return StreamingResponse(stream, media_type="application/octet-stream")


@router.put(
    "/sandboxes/{workload_kind}/{logical_id}/files:content",
    response_model=FileStatResponse,
)
async def write_file(
    request: Request,
    workload_kind: WorkloadKind,
    logical_id: UUID,
    path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
    deadline_at: datetime = Query(),
    expected_sha256: str | None = Query(default=None, pattern=r"^sha256:[0-9a-f]{64}$"),
    service: FilesystemService = Depends(filesystem),
) -> FileStatResponse:
    stat = await service.write_stream(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        path,
        request.stream(),
        expected_sha256=expected_sha256,
        deadline_at=deadline_at,
    )
    return FileStatResponse.from_domain(stat)


@router.post("/sandboxes/{workload_kind}/{logical_id}/files:move", status_code=204)
async def move_file(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    request: MoveFileRequest,
    service: FilesystemService = Depends(filesystem),
) -> Response:
    await service.move(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        request.source,
        request.destination,
        deadline_at=request.deadline_at,
    )
    return Response(status_code=204)


@router.delete("/sandboxes/{workload_kind}/{logical_id}/files", status_code=204)
async def delete_file(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
    recursive: bool = Query(default=False),
    deadline_at: datetime = Query(),
    service: FilesystemService = Depends(filesystem),
) -> Response:
    await service.delete(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        path,
        recursive=recursive,
        deadline_at=deadline_at,
    )
    return Response(status_code=204)


@router.put(
    "/sandboxes/{workload_kind}/{logical_id}/python-sessions/{session_id}",
    response_model=PythonSessionResponse,
    status_code=201,
    responses={200: {"model": PythonSessionResponse}},
)
async def create_python_session(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    session_id: UUID,
    request: CreatePythonSessionModel,
    service: PythonSessionService = Depends(python_sessions),
) -> Response | PythonSessionResponse:
    session, created = await service.create(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        request.to_domain(session_id),
    )
    response = PythonSessionResponse.from_domain(session)
    if created:
        return response
    return JSONResponse(status_code=200, content=jsonable_encoder(response))


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}/python-sessions/{session_id}:execute",
    response_model=PythonResultResponse,
    status_code=201,
    responses={200: {"model": PythonResultResponse}},
)
async def execute_python(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    session_id: UUID,
    request: ExecutePythonModel,
    service: PythonSessionService = Depends(python_sessions),
) -> Response | PythonResultResponse:
    result, created = await service.execute(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        session_id,
        request.to_domain(),
    )
    response = PythonResultResponse.from_domain(result)
    if created:
        return response
    return JSONResponse(status_code=200, content=jsonable_encoder(response))


@router.post(
    "/sandboxes/{workload_kind}/{logical_id}/python-sessions/{session_id}:restart",
    response_model=PythonSessionResponse,
)
async def restart_python_session(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    session_id: UUID,
    request: DeadlineRequest,
    service: PythonSessionService = Depends(python_sessions),
) -> PythonSessionResponse:
    session = await service.restart(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        session_id,
        deadline_at=request.deadline_at,
    )
    return PythonSessionResponse.from_domain(session)


@router.delete(
    "/sandboxes/{workload_kind}/{logical_id}/python-sessions/{session_id}",
    status_code=204,
)
async def delete_python_session(
    workload_kind: WorkloadKind,
    logical_id: UUID,
    session_id: UUID,
    deadline_at: datetime = Query(),
    service: PythonSessionService = Depends(python_sessions),
) -> Response:
    await service.delete(
        SandboxKey(workload_kind=workload_kind, logical_id=logical_id),
        session_id,
        deadline_at=deadline_at,
    )
    return Response(status_code=204)
