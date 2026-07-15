from __future__ import annotations

from fastapi import HTTPException

from agentbox.apps import sandbox_app
from agentbox.runtime_proxy import RuntimeProxy
from agentbox.schemas import (
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    WriteStdinRequest,
)


class LegacyRuntimeProviderMixin:
    """Compatibility facade for the pre-transport provider API.

    Runtime operations are deliberately implemented once here. Providers only
    resolve an authenticated runtime endpoint; existing manager routes and
    third-party callers can continue calling the historical methods.
    """

    async def _runtime_proxy(self, sandbox_id: str) -> RuntimeProxy:
        status = await self.get_status(sandbox_id)  # type: ignore[attr-defined]
        if not status.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        endpoint = await self.resolve_endpoint(  # type: ignore[attr-defined]
            sandbox_id,
            sandbox_app("runtime"),
        )
        return RuntimeProxy(
            endpoint.base_url,
            sandbox_id,
            headers=dict(endpoint.headers),
        )

    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        timeout_seconds: int,
    ) -> ExecutePythonResponse:
        proxy = await self._runtime_proxy(sandbox_id)
        stdout, stderr, result, error_name, exit_code = await proxy.execute_code(
            code,
            timeout_seconds,
            session_id=session_id,
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

    async def create_session(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: RuntimeSessionRequest,
    ) -> RuntimeSessionResponse:
        return await (await self._runtime_proxy(sandbox_id)).create_session(
            session_id, request_obj
        )

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        return await (await self._runtime_proxy(sandbox_id)).delete_session(session_id)

    async def exec_session_process_command(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse:
        return await (await self._runtime_proxy(sandbox_id)).exec_session_process_command(
            session_id, request_obj
        )

    async def write_session_process_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse:
        return await (await self._runtime_proxy(sandbox_id)).write_session_process_stdin(
            session_id, request_obj
        )

    async def terminate_session_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse:
        return await (await self._runtime_proxy(sandbox_id)).terminate_session_process(
            session_id, process_id
        )

    async def list_session_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse:
        return await (await self._runtime_proxy(sandbox_id)).list_session_processes(
            session_id
        )

    async def close(self) -> None:
        return None
