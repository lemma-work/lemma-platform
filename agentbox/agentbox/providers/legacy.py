from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import HTTPException

from agentbox.apps import sandbox_app
from agentbox.endpoint_transport import (
    EndpointRoutingUnavailable,
    REQUEST_NOT_DELIVERED_HEADER,
)
from agentbox.providers.models import SandboxEndpoint
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


_T = TypeVar("_T")


class LegacyRuntimeProviderMixin:
    """Compatibility facade for the pre-transport provider API.

    Runtime operations are deliberately implemented once here. Providers only
    resolve an authenticated runtime endpoint; existing manager routes and
    third-party callers can continue calling the historical methods.
    """

    async def _runtime_proxy(
        self,
        sandbox_id: str,
        endpoint: SandboxEndpoint | None = None,
    ) -> RuntimeProxy:
        runtime = sandbox_app("runtime")
        endpoint = endpoint or await self.resolve_endpoint(  # type: ignore[attr-defined]
            sandbox_id, runtime
        )
        return RuntimeProxy(
            endpoint.base_url,
            sandbox_id,
            headers=dict(endpoint.headers),
            transient_gateway=endpoint.transient_gateway,
            instance_id=endpoint.instance_id,
            port=runtime.port,
        )

    def _invalidate_runtime_endpoint(self, sandbox_id: str) -> None:
        invalidate = getattr(self, "invalidate_sandbox_cache", None)
        if invalidate is not None:
            invalidate(sandbox_id)

    async def _with_runtime_endpoint_refresh(
        self,
        sandbox_id: str,
        operation: Callable[[RuntimeProxy], Awaitable[_T]],
    ) -> _T:
        proxy = await self._runtime_proxy(sandbox_id)
        try:
            return await operation(proxy)
        except EndpointRoutingUnavailable as routing_error:
            # This exception is proof of a provider pre-routing miss, not a
            # generic 502. Reconnect once to refresh the route/domain/token and
            # safely replay the operation. Any response that may have reached
            # the runtime is never retried here.
            runtime = sandbox_app("runtime")
            refresh = getattr(self, "refresh_endpoint", None)
            if refresh is not None:
                endpoint = await refresh(
                    sandbox_id,
                    runtime,
                    instance_id=routing_error.instance_id,
                    protocol="http",
                )
                proxy = await self._runtime_proxy(sandbox_id, endpoint)
            else:
                self._invalidate_runtime_endpoint(sandbox_id)
                proxy = await self._runtime_proxy(sandbox_id)
            try:
                return await operation(proxy)
            except EndpointRoutingUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    headers={
                        "Retry-After": "1",
                        REQUEST_NOT_DELIVERED_HEADER: "true",
                    },
                    detail={
                        "message": (
                            "Sandbox runtime routing is temporarily unavailable"
                        ),
                        "code": "endpoint_routing_unavailable",
                        "retryable": True,
                        "provider_id": exc.instance_id,
                    },
                ) from exc

    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        timeout_seconds: int,
    ) -> ExecutePythonResponse:
        (
            stdout,
            stderr,
            result,
            error_name,
            exit_code,
        ) = await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.execute_code(
                code,
                timeout_seconds,
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

    async def create_session(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: RuntimeSessionRequest,
    ) -> RuntimeSessionResponse:
        return await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.create_session(session_id, request_obj),
        )

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        return await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.delete_session(session_id),
        )

    async def exec_session_process_command(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse:
        return await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.exec_session_process_command(session_id, request_obj),
        )

    async def write_session_process_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse:
        return await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.write_session_process_stdin(session_id, request_obj),
        )

    async def terminate_session_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse:
        return await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.terminate_session_process(session_id, process_id),
        )

    async def list_session_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse:
        return await self._with_runtime_endpoint_refresh(
            sandbox_id,
            lambda proxy: proxy.list_session_processes(session_id),
        )

    async def close(self) -> None:
        return None
