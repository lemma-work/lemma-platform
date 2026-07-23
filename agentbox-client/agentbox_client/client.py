from __future__ import annotations

import base64
from collections.abc import AsyncIterable, AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
import struct
from typing import Any, TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel

from .models import (
    AdmissionClass,
    AgentBoxErrorResponse,
    EnvironmentVariable,
    FileList,
    FileStat,
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessRef,
    ProcessState,
    PortAccessGrant,
    PortProtocol,
    ProfileRef,
    PythonResult,
    PythonSession,
    SandboxHandle,
    TerminalSize,
    WorkloadKind,
)


ModelT = TypeVar("ModelT", bound=BaseModel)
_CONTEXT_HEADER_NAMES = frozenset(
    {
        "x-request-id",
        "x-lemma-correlation-id",
        "x-lemma-event-id",
        "x-lemma-job-id",
    }
)


class AgentBoxApiError(RuntimeError):
    def __init__(self, response: httpx.Response, error: AgentBoxErrorResponse) -> None:
        super().__init__(error.error.message)
        self.status_code = response.status_code
        self.code = error.error.code
        self.retry = error.error.retry
        self.retry_after_ms = error.error.retry_after_ms
        self.context = error.error.context


class AgentBoxClient:
    """Typed async client for the canonical AgentBox API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 120,
        client: httpx.AsyncClient | None = None,
        context_headers_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._owns_client = client is None
        self._context_headers_provider = context_headers_provider
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )
        self._client.headers.setdefault("X-API-Key", api_key)
        self._client.headers.setdefault("Accept", "application/json")
        self.client = self._client

    async def __aenter__(self) -> AgentBoxClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def ensure_sandbox(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        *,
        profile: ProfileRef,
        admission_class: AdmissionClass,
        deadline_at: datetime,
    ) -> SandboxHandle:
        return await self._model_request(
            "PUT",
            self._sandbox_path(workload_kind, logical_id),
            SandboxHandle,
            json_body={
                "profile": profile.model_dump(mode="json"),
                "admission_class": admission_class.value,
                "deadline_at": deadline_at.isoformat(),
            },
        )

    async def inspect_sandbox(
        self, workload_kind: WorkloadKind, logical_id: UUID
    ) -> SandboxHandle | None:
        response = await self._request(
            "GET", self._sandbox_path(workload_kind, logical_id)
        )
        if response.status_code == 404:
            return None
        self._raise_for_error(response)
        return SandboxHandle.model_validate(response.json())

    async def release_sandbox(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        *,
        deadline_at: datetime,
    ) -> SandboxHandle:
        return await self._model_request(
            "POST",
            f"{self._sandbox_path(workload_kind, logical_id)}:release",
            SandboxHandle,
            json_body={"deadline_at": deadline_at.isoformat()},
        )

    async def destroy_sandbox(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        *,
        deadline_at: datetime,
    ) -> None:
        response = await self._request(
            "DELETE",
            self._sandbox_path(workload_kind, logical_id),
            params={"deadline_at": deadline_at.isoformat()},
        )
        self._raise_for_error(response)

    async def create_port_access(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        port: int,
        *,
        protocol: PortProtocol = PortProtocol.HTTP,
        expires_at: datetime,
    ) -> PortAccessGrant:
        return await self._model_request(
            "POST",
            f"{self._sandbox_path(workload_kind, logical_id)}/ports/{port}:access",
            PortAccessGrant,
            json_body={
                "protocol": protocol.value,
                "expires_at": expires_at.isoformat(),
            },
        )

    async def start_process(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        *,
        operation_id: UUID,
        deadline_at: datetime,
        cwd: str,
        shell_command: str | None = None,
        argv: tuple[str, ...] | None = None,
        environment: tuple[EnvironmentVariable, ...] = (),
        tty: TerminalSize | None = None,
        output_limit_bytes: int = 1024 * 1024,
        initial_input: bytes | None = None,
    ) -> ProcessRef:
        return await self._model_request(
            "POST",
            f"{self._sandbox_path(workload_kind, logical_id)}/processes",
            ProcessRef,
            json_body={
                "operation_id": str(operation_id),
                "shell_command": shell_command,
                "argv": argv,
                "cwd": cwd,
                "environment": [item.model_dump() for item in environment],
                "tty": tty.model_dump() if tty is not None else None,
                "output_limit_bytes": output_limit_bytes,
                "deadline_at": deadline_at.isoformat(),
                "initial_input_base64": (
                    base64.b64encode(initial_input).decode()
                    if initial_input is not None
                    else None
                ),
            },
        )

    async def inspect_process(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        operation_id: UUID,
    ) -> ProcessRef:
        return await self._model_request(
            "GET",
            f"{self._sandbox_path(workload_kind, logical_id)}/processes/{operation_id}",
            ProcessRef,
        )

    async def list_processes(
        self, workload_kind: WorkloadKind, logical_id: UUID
    ) -> tuple[ProcessRef, ...]:
        response = await self._request(
            "GET", f"{self._sandbox_path(workload_kind, logical_id)}/processes"
        )
        self._raise_for_error(response)
        return tuple(ProcessRef.model_validate(item) for item in response.json())

    async def read_process_output(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        after_sequence: int = 0,
        wait_seconds: float = 0,
    ) -> ProcessOutputSnapshot:
        response = await self._request(
            "GET",
            f"{self._sandbox_path(workload_kind, logical_id)}/processes/{operation_id}/output",
            params={
                "deadline_at": deadline_at.isoformat(),
                "after_seq": str(after_sequence),
                "wait_seconds": str(wait_seconds),
            },
        )
        self._raise_for_error(response)
        channels = {
            1: ProcessOutputChannel.STDOUT,
            2: ProcessOutputChannel.STDERR,
            3: ProcessOutputChannel.PTY,
        }
        chunks: list[ProcessOutputChunk] = []
        offset = 0
        while offset < len(response.content):
            if len(response.content) - offset < 13:
                raise RuntimeError("AgentBox process output frame is truncated")
            sequence, channel_id, size = struct.unpack_from(
                "!QBI", response.content, offset
            )
            offset += 13
            data = response.content[offset : offset + size]
            if len(data) != size or channel_id not in channels:
                raise RuntimeError("AgentBox process output frame is invalid")
            offset += size
            chunks.append(
                ProcessOutputChunk(
                    sequence=sequence, channel=channels[channel_id], data=data
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

    async def send_process_input(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        operation_id: UUID,
        data: bytes,
        *,
        deadline_at: datetime,
    ) -> None:
        response = await self._request(
            "POST",
            f"{self._sandbox_path(workload_kind, logical_id)}/processes/{operation_id}:input",
            params={"deadline_at": deadline_at.isoformat()},
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        self._raise_for_error(response)

    async def resize_process(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        operation_id: UUID,
        size: TerminalSize,
        *,
        deadline_at: datetime,
    ) -> None:
        response = await self._request(
            "POST",
            f"{self._sandbox_path(workload_kind, logical_id)}/processes/{operation_id}:resize",
            json_body={
                "size": size.model_dump(),
                "deadline_at": deadline_at.isoformat(),
            },
        )
        self._raise_for_error(response)

    async def terminate_process(
        self,
        workload_kind: WorkloadKind,
        logical_id: UUID,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        grace_seconds: float = 5,
    ) -> ProcessRef:
        return await self._model_request(
            "DELETE",
            f"{self._sandbox_path(workload_kind, logical_id)}/processes/{operation_id}",
            ProcessRef,
            params={
                "deadline_at": deadline_at.isoformat(),
                "grace_seconds": str(grace_seconds),
            },
        )

    async def create_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        cwd: str,
        environment_keys: tuple[str, ...],
        deadline_at: datetime,
    ) -> PythonSession:
        return await self._model_request(
            "PUT",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/python-sessions/{session_id}",
            PythonSession,
            json_body={
                "cwd": cwd,
                "environment_keys": environment_keys,
                "deadline_at": deadline_at.isoformat(),
            },
        )

    async def execute_python(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        operation_id: UUID,
        code: str,
        environment: tuple[EnvironmentVariable, ...] = (),
        output_limit_bytes: int,
        deadline_at: datetime,
    ) -> PythonResult:
        return await self._model_request(
            "POST",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/python-sessions/{session_id}:execute",
            PythonResult,
            json_body={
                "operation_id": str(operation_id),
                "code": code,
                "environment": [item.model_dump() for item in environment],
                "output_limit_bytes": output_limit_bytes,
                "deadline_at": deadline_at.isoformat(),
            },
        )

    async def restart_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        deadline_at: datetime,
    ) -> PythonSession:
        return await self._model_request(
            "POST",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/python-sessions/{session_id}:restart",
            PythonSession,
            json_body={"deadline_at": deadline_at.isoformat()},
        )

    async def delete_python_session(
        self,
        logical_id: UUID,
        session_id: UUID,
        *,
        deadline_at: datetime,
    ) -> None:
        response = await self._request(
            "DELETE",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/python-sessions/{session_id}",
            params={"deadline_at": deadline_at.isoformat()},
        )
        self._raise_for_error(response)

    async def stat_file(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> FileStat:
        return await self._model_request(
            "GET",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/files:stat",
            FileStat,
            params={"path": path, "deadline_at": deadline_at.isoformat()},
        )

    async def create_directory(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> None:
        response = await self._request(
            "PUT",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/directories",
            params={"path": path, "deadline_at": deadline_at.isoformat()},
        )
        self._raise_for_error(response)

    async def list_files(
        self, logical_id: UUID, path: str, *, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        result = await self._model_request(
            "GET",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/files",
            FileList,
            params={"path": path, "deadline_at": deadline_at.isoformat()},
        )
        return result.entries

    async def read_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        async with self.stream_file(
            logical_id,
            path,
            deadline_at=deadline_at,
            offset=offset,
            length=length,
        ) as stream:
            return b"".join([chunk async for chunk in stream])

    @asynccontextmanager
    async def stream_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        params = {
            "path": path,
            "offset": str(offset),
            "deadline_at": deadline_at.isoformat(),
        }
        if length is not None:
            params["length"] = str(length)
        async with self._client.stream(
            "GET",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/files:content",
            params=params,
            headers=self._context_headers() or None,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                await response.aread()
                self._raise_for_error(response)
            yield response.aiter_bytes(chunk_size=1024 * 1024)

    async def write_file(
        self,
        logical_id: UUID,
        path: str,
        data: bytes,
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> FileStat:
        async def one_chunk() -> AsyncIterator[bytes]:
            yield data

        return await self.write_file_stream(
            logical_id,
            path,
            one_chunk(),
            deadline_at=deadline_at,
            expected_sha256=expected_sha256,
        )

    async def write_file_stream(
        self,
        logical_id: UUID,
        path: str,
        data: AsyncIterable[bytes],
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> FileStat:
        params = {"path": path, "deadline_at": deadline_at.isoformat()}
        if expected_sha256 is not None:
            params["expected_sha256"] = expected_sha256
        return await self._model_request(
            "PUT",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/files:content",
            FileStat,
            params=params,
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )

    async def move_file(
        self,
        logical_id: UUID,
        source: str,
        destination: str,
        *,
        deadline_at: datetime,
    ) -> None:
        response = await self._request(
            "POST",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/files:move",
            json_body={
                "source": source,
                "destination": destination,
                "deadline_at": deadline_at.isoformat(),
            },
        )
        self._raise_for_error(response)

    async def delete_file(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at: datetime,
        recursive: bool = False,
    ) -> None:
        response = await self._request(
            "DELETE",
            f"{self._sandbox_path(WorkloadKind.WORKSPACE, logical_id)}/files",
            params={
                "path": path,
                "recursive": str(recursive).lower(),
                "deadline_at": deadline_at.isoformat(),
            },
        )
        self._raise_for_error(response)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _model_request(
        self,
        method: str,
        path: str,
        model: type[ModelT],
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        content: bytes | AsyncIterable[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ModelT:
        response = await self._request(
            method,
            path,
            json_body=json_body,
            params=params,
            content=content,
            headers=headers,
        )
        self._raise_for_error(response)
        return model.model_validate(response.json())

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        content: bytes | AsyncIterable[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        merged_headers = self._context_headers()
        if headers:
            merged_headers.update(headers)
        return await self._client.request(
            method,
            path,
            json=json_body,
            params=params,
            content=content,
            headers=merged_headers or None,
        )

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        # A process whose exact dispatch is still being reconciled can return an
        # accepted (202) typed error. Inspect the envelope before treating every
        # 2xx response as a success resource.
        payload: Any | None = None
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict) and "error" in payload:
            error = AgentBoxErrorResponse.model_validate(payload)
            raise AgentBoxApiError(response, error)
        if 200 <= response.status_code < 300:
            return
        try:
            error = AgentBoxErrorResponse.model_validate(payload)
        except Exception:
            response.raise_for_status()
            raise AssertionError("unreachable")
        raise AgentBoxApiError(response, error)

    def _context_headers(self) -> dict[str, str]:
        if self._context_headers_provider is None:
            return {}
        try:
            provided = self._context_headers_provider()
        except Exception:
            return {}
        return {
            key: value
            for key, value in provided.items()
            if key.lower() in _CONTEXT_HEADER_NAMES
        }

    @staticmethod
    def _sandbox_path(workload_kind: WorkloadKind, logical_id: UUID) -> str:
        return f"/sandboxes/{workload_kind.value}/{logical_id}"
