from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import time
from uuid import UUID
from urllib.parse import quote

import httpx

from agentbox_client.errors import AgentBoxError

from agentbox_client.generated.function_executor.models import (
    FunctionExecuteRequest,
    FunctionInvokeResponse,
    FunctionJobAcceptedResponse,
    FunctionJobStatusResponse,
    FunctionLogEntry,
    FunctionLogsResponse,
    RuntimeErrorInfo,
)

__all__ = [
    "FunctionExecuteRequest",
    "FunctionExecutorClient",
    "FunctionInvokeResponse",
    "FunctionJobAcceptedResponse",
    "FunctionJobStatusResponse",
    "FunctionLogEntry",
    "FunctionLogsResponse",
    "RuntimeErrorInfo",
]

# Per-request override telling the manager proxy how long to wait on the
# in-sandbox app (see agentbox.api.apps.UPSTREAM_TIMEOUT_HEADER).
_UPSTREAM_TIMEOUT_HEADER = "X-Agentbox-Upstream-Timeout"
# A synchronous execute blocks until the function returns. The in-sandbox
# executor enforces request.timeout_seconds itself and always returns a response
# (status="timeout" on overrun), so the proxy must wait that long plus headroom
# for the executor's auth/load/serialize overhead -- otherwise the proxy times
# out first and a still-running, non-idempotent function looks like a failure.
_EXECUTE_PROXY_OVERHEAD_SECONDS = 30.0
# The client waits a little longer than the proxy so it receives the proxy's
# response (including a 504) rather than tripping its own read timeout first.
_EXECUTE_CLIENT_OVERHEAD_SECONDS = 15.0
_CONTEXT_HEADER_NAMES = frozenset(
    {
        "x-request-id",
        "x-lemma-correlation-id",
        "x-lemma-event-id",
        "x-lemma-job-id",
    }
)


class FunctionExecutorClient:
    def __init__(
        self,
        *,
        manager_base_url: str,
        manager_api_key: str,
        lemma_token: str,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
        context_headers_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.manager_base_url = manager_base_url.rstrip("/")
        self.manager_api_key = manager_api_key
        self.lemma_token = lemma_token
        self._owns_client = client is None
        self._context_headers_provider = context_headers_provider
        self.client = client or httpx.AsyncClient(
            base_url=self.manager_base_url,
            timeout=timeout_seconds,
            headers={
                "X-API-Key": manager_api_key,
                "Authorization": f"Bearer {lemma_token}",
                "Accept": "application/json",
            },
        )

    def _context_headers(self, **extra: str) -> dict[str, str] | None:
        try:
            provided = (
                self._context_headers_provider()
                if self._context_headers_provider is not None
                else {}
            )
        except Exception:
            provided = {}
        headers = {
            key: value
            for key, value in provided.items()
            if key.lower() in _CONTEXT_HEADER_NAMES
        }
        headers.update(extra)
        return headers or None

    async def execute(
        self,
        *,
        sandbox_id: str,
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
    ) -> FunctionInvokeResponse | FunctionJobAcceptedResponse:
        # Drive the proxy + client timeouts from the function's own budget so a
        # legitimately long synchronous execute is not cut off at the proxy's
        # default. (An async_job execute returns immediately, so the larger
        # window is simply unused.)
        upstream_timeout = request.timeout_seconds + _EXECUTE_PROXY_OVERHEAD_SECONDS
        client_timeout = upstream_timeout + _EXECUTE_CLIENT_OVERHEAD_SECONDS
        response = await self.client.post(
            f"/sandboxes/{sandbox_id}/apps/function_executor/"
            f"pods/{pod_id}/functions/{quote(function_name, safe='')}/execute",
            json=request.model_dump(mode="json"),
            headers=self._context_headers(
                **{_UPSTREAM_TIMEOUT_HEADER: str(int(upstream_timeout))}
            ),
            timeout=client_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "accepted":
            return FunctionJobAcceptedResponse.model_validate(payload)
        return FunctionInvokeResponse.model_validate(payload)

    async def get_status(
        self,
        *,
        sandbox_id: str,
        run_id: UUID,
    ) -> FunctionJobStatusResponse:
        response = await self.client.get(
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{run_id}",
            headers=self._context_headers(),
        )
        response.raise_for_status()
        return FunctionJobStatusResponse.model_validate(response.json())

    async def get_logs(
        self,
        *,
        sandbox_id: str,
        run_id: UUID,
    ) -> FunctionLogsResponse:
        response = await self.client.get(
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{run_id}/logs",
            headers=self._context_headers(),
        )
        response.raise_for_status()
        return FunctionLogsResponse.model_validate(response.json())

    async def cancel(
        self, *, sandbox_id: str, run_id: UUID
    ) -> FunctionJobStatusResponse:
        """Idempotently cancel an invocation and return its resulting status."""

        response = await self.client.post(
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{run_id}/cancel",
            headers=self._context_headers(),
        )
        response.raise_for_status()
        return FunctionJobStatusResponse.model_validate(response.json())

    async def health(self, *, sandbox_id: str) -> bool:
        """Return True if the function_executor app is serving requests.

        Probes the readiness endpoint, falling back to ``/health`` for older
        server builds. A single non-2xx/transport result returns False.
        """
        for path in (
            f"/sandboxes/{sandbox_id}/apps/function_executor/readiness",
            f"/sandboxes/{sandbox_id}/apps/function_executor/health",
        ):
            try:
                response = await self.client.get(path, headers=self._context_headers())
            except httpx.HTTPError:
                return False
            if 200 <= response.status_code < 300:
                return True
            if response.status_code == 404:
                continue  # endpoint missing on this build; try the fallback path
            return False
        return False

    async def wait_until_ready(
        self,
        *,
        sandbox_id: str,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        """Block until the in-sandbox function_executor app is serving.

        Polls the app's ``/readiness`` endpoint (falling back to ``/health`` for
        older server builds) until it returns 2xx or the timeout elapses.
        Transient proxy errors (connection refused / 5xx -- the app is still
        binding its port) are retried. A server build exposing neither endpoint
        (both 404) is treated as ready-unknown and returns without error so the
        caller's execute path (with its own retry backstop) proceeds. Raises
        ``AgentBoxError`` on timeout.
        """
        readiness_path = f"/sandboxes/{sandbox_id}/apps/function_executor/readiness"
        health_path = f"/sandboxes/{sandbox_id}/apps/function_executor/health"
        deadline = time.monotonic() + timeout_seconds
        last_detail = "no response"
        while True:
            both_endpoints_missing = True
            for path in (readiness_path, health_path):
                try:
                    response = await self.client.get(path, headers=self._context_headers())
                except httpx.HTTPError as exc:
                    last_detail = f"{type(exc).__name__}: {exc}"
                    both_endpoints_missing = False
                    continue
                if 200 <= response.status_code < 300:
                    return
                if response.status_code == 404:
                    last_detail = f"{path} -> 404"
                    continue  # endpoint absent on this build; try the fallback
                last_detail = f"{path} -> HTTP {response.status_code}"
                both_endpoints_missing = False
            if both_endpoints_missing:
                # Neither endpoint exists on this server build; nothing to wait
                # on. Proceed and let the execute retry backstop cover cold start.
                return
            if time.monotonic() >= deadline:
                raise AgentBoxError(
                    f"function_executor for sandbox {sandbox_id} not ready "
                    f"after {timeout_seconds:.0f}s ({last_detail})",
                    retryable=True,
                )
            await asyncio.sleep(poll_interval_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
