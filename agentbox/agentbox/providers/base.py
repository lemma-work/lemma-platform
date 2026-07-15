from __future__ import annotations

from typing import Protocol

from agentbox.config import settings
from agentbox.providers.protocol import SandboxLifecycleProvider
from agentbox.providers.registry import build_provider
from agentbox.schemas import (
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    WriteStdinRequest,
)


class SandboxProvider(SandboxLifecycleProvider, Protocol):
    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        timeout_seconds: int,
    ) -> ExecutePythonResponse: ...

    async def create_session(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: RuntimeSessionRequest,
    ) -> RuntimeSessionResponse: ...

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool: ...

    async def exec_session_process_command(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse: ...

    async def write_session_process_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse: ...

    async def terminate_session_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse: ...

    async def list_session_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse: ...


def build_sandbox_provider() -> SandboxProvider:
    return build_provider(settings.agentbox_provider)  # type: ignore[return-value]
