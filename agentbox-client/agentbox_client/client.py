from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

import httpx
from pydantic import BaseModel

from agentbox_client.generated.manager.models import (
    AppAccessRequest,
    AppAccessResponse,
    DeleteResponse,
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonRequest,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionHeartbeatResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    SandboxEnsureRequest,
    SandboxResponse,
    SandboxSummary,
    SuspendResponse,
    WriteStdinRequest,
)
from agentbox_client.timeouts import (
    exec_command_http_timeout,
    write_stdin_http_timeout,
)

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)
_CONTEXT_HEADER_NAMES = frozenset(
    {
        "x-request-id",
        "x-lemma-correlation-id",
        "x-lemma-event-id",
        "x-lemma-job-id",
    }
)


class AgentBoxClient:
    """Async typed client for the AgentBox manager API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
        context_headers_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._context_headers_provider = context_headers_provider
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers={
                # Sandbox-manager auth is exclusively via X-API-Key. The
                # Authorization header is reserved for the function/lemma bearer
                # token (the manager forwards it to the runtime), so the manager
                # key must never travel on Authorization.
                "X-API-Key": api_key,
                "Accept": "application/json",
            },
        )

    def _context_headers(self) -> dict[str, str] | None:
        if self._context_headers_provider is None:
            return None
        try:
            provided = self._context_headers_provider()
        except Exception:
            return None
        headers = {
            key: value
            for key, value in provided.items()
            if key.lower() in _CONTEXT_HEADER_NAMES
        }
        return headers or None

    async def __aenter__(self) -> "AgentBoxClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def ensure_sandbox(
        self,
        sandbox_id: str,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxSummary:
        response = await self._request_model(
            "PUT",
            f"/sandboxes/{sandbox_id}",
            SandboxResponse,
            SandboxEnsureRequest(env=env or {}),
        )
        return response.sandbox

    async def get_sandbox(self, sandbox_id: str) -> SandboxSummary | None:
        response = await self.client.get(
            f"/sandboxes/{sandbox_id}", headers=self._context_headers()
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return SandboxSummary.model_validate(response.json())

    async def delete_sandbox(self, sandbox_id: str) -> DeleteResponse:
        return await self._request_model(
            "DELETE",
            f"/sandboxes/{sandbox_id}",
            DeleteResponse,
        )

    async def suspend_sandbox(self, sandbox_id: str) -> SuspendResponse:
        return await self._request_model(
            "POST",
            f"/sandboxes/{sandbox_id}/suspend",
            SuspendResponse,
        )

    async def create_session(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        env: dict[str, str] | None = None,
        cwd: str = "/workspace",
    ) -> RuntimeSessionResponse:
        return await self._request_model(
            "PUT",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}",
            RuntimeSessionResponse,
            RuntimeSessionRequest(env=env or {}, cwd=cwd),
        )

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        response = await self.client.delete(
            f"/sandboxes/{sandbox_id}/sessions/{session_id}",
            headers=self._context_headers(),
        )
        response.raise_for_status()
        return bool(response.json().get("deleted"))

    async def heartbeat_session(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> RuntimeSessionHeartbeatResponse:
        return await self._request_model(
            "POST",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/heartbeat",
            RuntimeSessionHeartbeatResponse,
        )

    async def heartbeat_sandbox(self, sandbox_id: str) -> bool:
        """Reset a sandbox's idle clock so the manager's reaper keeps it alive.

        Used by long-running workloads that occupy a sandbox without holding a
        runtime session (e.g. JOB functions running through the function_executor
        app). Best-effort: returns the manager's reported active state.
        """
        response = await self.client.post(
            f"/sandboxes/{sandbox_id}/heartbeat", headers=self._context_headers()
        )
        response.raise_for_status()
        return bool(response.json().get("active", False))

    async def get_app_access_url(
        self,
        sandbox_id: str,
        app_name: str,
        *,
        ttl_seconds: int = 1800,
    ) -> AppAccessResponse:
        return await self._request_model(
            "POST",
            f"/sandboxes/{sandbox_id}/apps/{app_name}/access",
            AppAccessResponse,
            AppAccessRequest(ttl_seconds=ttl_seconds),
        )

    async def execute_python(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        *,
        timeout_seconds: int = 60,
    ) -> ExecutePythonResponse:
        return await self._request_model(
            "POST",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/python",
            ExecutePythonResponse,
            ExecutePythonRequest(code=code, timeout_seconds=timeout_seconds),
        )

    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        *,
        timeout_seconds: int = 60,
    ) -> ExecutePythonResponse:
        return await self.execute_python(
            sandbox_id,
            session_id,
            code,
            timeout_seconds=timeout_seconds,
        )

    async def exec_command(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        cmd: str,
        max_output_tokens: int | None = None,
        tty: bool = False,
        workdir: str | None = None,
        yield_time_ms: int | None = None,
        timeout: int | None = 300,
    ) -> ExecCommandResponse:
        request = ExecCommandRequest(
            cmd=cmd,
            max_output_tokens=max_output_tokens,
            tty=tty,
            workdir=workdir,
            yield_time_ms=yield_time_ms,
            timeout=timeout,
        )
        return await self._request_model(
            "POST",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
            ExecCommandResponse,
            request,
            timeout=exec_command_http_timeout(
                client_timeout_seconds=self.timeout_seconds,
                command_timeout_seconds=timeout,
                yield_time_ms=yield_time_ms,
            ),
        )

    async def write_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        process_id: str,
        chars: str | None = None,
        max_output_tokens: int | None = None,
        yield_time_ms: int | None = None,
    ) -> ExecCommandResponse:
        request = WriteStdinRequest(
            process_id=process_id,
            chars=chars,
            max_output_tokens=max_output_tokens,
            yield_time_ms=yield_time_ms,
        )
        return await self._request_model(
            "POST",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/stdin",
            ExecCommandResponse,
            request,
            timeout=write_stdin_http_timeout(yield_time_ms=yield_time_ms),
        )

    async def terminate_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse:
        return await self._request_model(
            "DELETE",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/processes/{process_id}",
            ExecCommandResponse,
            timeout=35,
        )

    async def list_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse:
        return await self._request_model(
            "GET",
            f"/sandboxes/{sandbox_id}/sessions/{session_id}/processes",
            ListProcessesResponse,
            timeout=35,
        )

    def session(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        env: dict[str, str] | None = None,
        cwd: str = "/workspace",
        heartbeat_interval_seconds: float = 30.0,
        delete_on_exit: bool = True,
    ):
        from agentbox_client.sessions import AgentBoxSession

        return AgentBoxSession(
            client=self,
            sandbox_id=sandbox_id,
            session_id=session_id,
            env=env or {},
            cwd=cwd,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            delete_on_exit=delete_on_exit,
        )

    async def _request_model(
        self,
        method: str,
        path: str,
        model_type: type[ResponseModelT],
        body: BaseModel | dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> ResponseModelT:
        json_body = body.model_dump(exclude_none=True) if isinstance(body, BaseModel) else body
        response = await self.client.request(
            method,
            path,
            json=json_body,
            timeout=timeout,
            headers=self._context_headers(),
        )
        response.raise_for_status()
        return model_type.model_validate(response.json())

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
