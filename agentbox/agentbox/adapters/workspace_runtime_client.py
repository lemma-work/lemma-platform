from __future__ import annotations

from datetime import datetime, timezone
import struct

import httpx

from agentbox.api.contracts import (
    EnvironmentVariableModel,
    StartProcessModel,
    TerminalSizeModel,
)
from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
    PythonResult,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)
from agentbox.workspace_runtime.models import (
    RuntimeCreatePythonSessionRequest,
    RuntimeExecutePythonRequest,
    RuntimeFileListResponse,
    RuntimeFileStatResponse,
    RuntimeHealthResponse,
    RuntimeMoveFileRequest,
    RuntimePythonResultResponse,
    RuntimePythonSessionResponse,
    RuntimeQuiesceResponse,
    RuntimeProcessResponse,
    RuntimeResizeRequest,
    RuntimeTerminateRequest,
)


class WorkspaceRuntimeError(RuntimeError):
    pass


class WorkspaceRuntimeStartAmbiguous(WorkspaceRuntimeError):
    pass


class WorkspaceRuntimePythonAmbiguous(WorkspaceRuntimeError):
    pass


class WorkspaceRuntimeClient:
    def __init__(
        self, base_url: str, token: str, *, request_timeout_seconds: float = 35
    ) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-AgentBox-Runtime-Token": token},
            timeout=None,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self, *, deadline_at: datetime) -> RuntimeHealthResponse:
        response = await self._request("GET", "/health", deadline_at=deadline_at)
        return RuntimeHealthResponse.model_validate(response.json())

    async def start_process(
        self, request: StartProcessRequest
    ) -> RuntimeProcessResponse:
        body = StartProcessModel(
            operation_id=request.operation_id,
            shell_command=request.shell_command,
            argv=request.argv,
            cwd=request.cwd,
            environment=tuple(
                EnvironmentVariableModel(name=item.name, value=item.value)
                for item in request.environment
            ),
            tty=(
                TerminalSizeModel(cols=request.tty.cols, rows=request.tty.rows)
                if request.tty is not None
                else None
            ),
            output_limit_bytes=request.output_limit_bytes,
            deadline_at=request.deadline_at,
        )
        response = await self._request(
            "POST",
            "/processes",
            deadline_at=request.deadline_at,
            json_body=body,
            ambiguous_error=WorkspaceRuntimeStartAmbiguous,
        )
        return RuntimeProcessResponse.model_validate(response.json())

    async def send_input(
        self,
        operation_id: str,
        data: bytes,
        *,
        deadline_at: datetime,
    ) -> None:
        await self._request(
            "POST",
            f"/processes/{operation_id}:input",
            deadline_at=deadline_at,
            content=data,
            content_type="application/octet-stream",
        )

    async def read_output(
        self,
        operation_id: str,
        *,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        response = await self._request(
            "GET",
            f"/processes/{operation_id}/output",
            deadline_at=deadline_at,
            params={
                "after_seq": str(after_sequence),
                "wait_seconds": str(wait_seconds),
            },
        )
        channels = {
            1: ProcessOutputChannel.STDOUT,
            2: ProcessOutputChannel.STDERR,
            3: ProcessOutputChannel.PTY,
        }
        chunks: list[ProcessOutputChunk] = []
        offset = 0
        while offset < len(response.content):
            if len(response.content) - offset < 13:
                raise WorkspaceRuntimeError("runtime output frame header is truncated")
            sequence, channel_id, size = struct.unpack_from(
                "!QBI", response.content, offset
            )
            offset += 13
            data = response.content[offset : offset + size]
            if len(data) != size or channel_id not in channels:
                raise WorkspaceRuntimeError("runtime output frame is invalid")
            offset += size
            chunks.append(
                ProcessOutputChunk(
                    sequence=sequence,
                    channel=channels[channel_id],
                    data=data,
                )
            )
        truncated = response.headers.get("X-AgentBox-Truncated-Before", "")
        exit_code = response.headers.get("X-AgentBox-Exit-Code", "")
        return ProcessOutputSnapshot(
            chunks=tuple(chunks),
            next_sequence=int(response.headers["X-AgentBox-Next-Sequence"]),
            truncated_before_sequence=int(truncated) if truncated else None,
            state=ProcessState(response.headers["X-AgentBox-Process-State"]),
            exit_code=int(exit_code) if exit_code else None,
        )

    async def resize(
        self,
        operation_id: str,
        size: TerminalSize,
        *,
        deadline_at: datetime,
    ) -> None:
        await self._request(
            "POST",
            f"/processes/{operation_id}:resize",
            deadline_at=deadline_at,
            json_body=RuntimeResizeRequest(cols=size.cols, rows=size.rows),
        )

    async def terminate(
        self,
        operation_id: str,
        *,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> RuntimeProcessResponse:
        response = await self._request(
            "DELETE",
            f"/processes/{operation_id}",
            deadline_at=deadline_at,
            json_body=RuntimeTerminateRequest(grace_seconds=grace_seconds),
        )
        return RuntimeProcessResponse.model_validate(response.json())

    async def stat_file(self, path: str, *, deadline_at: datetime) -> FileStat:
        response = await self._request(
            "GET", "/files:stat", deadline_at=deadline_at, params={"path": path}
        )
        return RuntimeFileStatResponse.model_validate(response.json()).to_domain()

    async def list_files(
        self, path: str, *, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        response = await self._request(
            "GET", "/files", deadline_at=deadline_at, params={"path": path}
        )
        body = RuntimeFileListResponse.model_validate(response.json())
        return tuple(item.to_domain() for item in body.entries)

    async def read_file(
        self,
        path: str,
        byte_range: ByteRange,
        *,
        deadline_at: datetime,
    ) -> bytes:
        params = {"path": path, "offset": str(byte_range.offset)}
        if byte_range.length is not None:
            params["length"] = str(byte_range.length)
        response = await self._request(
            "GET", "/files:content", deadline_at=deadline_at, params=params
        )
        return response.content

    async def write_file(
        self,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        params = {"path": path}
        if expected_sha256 is not None:
            params["expected_sha256"] = expected_sha256
        response = await self._request(
            "PUT",
            "/files:content",
            deadline_at=deadline_at,
            params=params,
            content=data,
            content_type="application/octet-stream",
        )
        return RuntimeFileStatResponse.model_validate(response.json()).to_domain()

    async def move_file(
        self, source: str, destination: str, *, deadline_at: datetime
    ) -> None:
        await self._request(
            "POST",
            "/files:move",
            deadline_at=deadline_at,
            json_body=RuntimeMoveFileRequest(
                source=source, destination=destination
            ),
        )

    async def delete_file(
        self,
        path: str,
        *,
        recursive: bool,
        deadline_at: datetime,
    ) -> None:
        await self._request(
            "DELETE",
            "/files",
            deadline_at=deadline_at,
            params={"path": path, "recursive": str(recursive).lower()},
        )

    async def create_python_session(
        self, request: CreatePythonSessionRequest
    ) -> RuntimePythonSessionResponse:
        body = RuntimeCreatePythonSessionRequest(
            cwd=request.cwd,
            environment_keys=request.environment_keys,
            deadline_at=request.deadline_at,
        )
        response = await self._request(
            "PUT",
            f"/python-sessions/{request.session_id}",
            deadline_at=request.deadline_at,
            json_body=body,
            ambiguous_error=WorkspaceRuntimePythonAmbiguous,
        )
        return RuntimePythonSessionResponse.model_validate(response.json())

    async def execute_python(
        self,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        body = RuntimeExecutePythonRequest(
            operation_id=request.operation_id,
            code=request.code,
            environment=tuple(
                EnvironmentVariableModel(name=item.name, value=item.value)
                for item in request.environment
            ),
            output_limit_bytes=request.output_limit_bytes,
            deadline_at=request.deadline_at,
        )
        response = await self._request(
            "POST",
            f"/python-sessions/{session.session_id}:execute",
            deadline_at=request.deadline_at,
            json_body=body,
            ambiguous_error=WorkspaceRuntimePythonAmbiguous,
        )
        return RuntimePythonResultResponse.model_validate(response.json()).to_domain()

    async def restart_python_session(
        self, session_id: str, *, deadline_at: datetime
    ) -> RuntimePythonSessionResponse:
        response = await self._request(
            "POST",
            f"/python-sessions/{session_id}:restart",
            deadline_at=deadline_at,
            ambiguous_error=WorkspaceRuntimePythonAmbiguous,
        )
        return RuntimePythonSessionResponse.model_validate(response.json())

    async def delete_python_session(
        self, session_id: str, *, deadline_at: datetime
    ) -> None:
        await self._request(
            "DELETE",
            f"/python-sessions/{session_id}",
            deadline_at=deadline_at,
            ambiguous_error=WorkspaceRuntimePythonAmbiguous,
        )

    async def quiesce(self, *, deadline_at: datetime) -> RuntimeQuiesceResponse:
        response = await self._request(
            "POST", "/quiesce", deadline_at=deadline_at
        )
        return RuntimeQuiesceResponse.model_validate(response.json())

    async def _request(
        self,
        method: str,
        path: str,
        *,
        deadline_at: datetime,
        json_body: StartProcessModel
        | RuntimeResizeRequest
        | RuntimeTerminateRequest
        | RuntimeMoveFileRequest
        | RuntimeCreatePythonSessionRequest
        | RuntimeExecutePythonRequest
        | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        params: dict[str, str] | None = None,
        ambiguous_error: type[WorkspaceRuntimeError] | None = None,
    ) -> httpx.Response:
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise WorkspaceRuntimeError("runtime operation deadline has elapsed")
        headers = {"Content-Type": content_type} if content_type is not None else None
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=(
                    json_body.model_dump(mode="json", exclude_none=True)
                    if json_body is not None
                    else None
                ),
                content=content,
                headers=headers,
                timeout=min(remaining, self._request_timeout_seconds),
            )
        except httpx.TransportError as exc:
            error_type = ambiguous_error or WorkspaceRuntimeError
            raise error_type(
                f"workspace runtime transport failed: {type(exc).__name__}"
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise WorkspaceRuntimeError(
                f"workspace runtime returned HTTP {response.status_code}"
            )
        return response
