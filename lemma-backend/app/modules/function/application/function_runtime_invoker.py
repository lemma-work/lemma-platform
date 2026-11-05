"""One invocation request to a pod's function runtime, and what its failure means.

Split out of ``function_dispatcher.py`` at the 600-line ceiling
``scripts/check_architecture.py`` enforces. The dispatcher owns a run's state
transitions; this owns the wire: building the envelope, and turning each way
the request can fail into either "never delivered, safe to replay" or "may have
run, never replay" -- and deciding, with the runtime's health probe, whether
the sandbox behind it is still fit to serve anyone else.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urljoin
from uuid import UUID

import httpx

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.function.application.dispatch_failures import (
    InvocationOutcomeUnconfirmed,
    RuntimeNeverReached,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
)
from app.modules.function.application.function_runtime_quarantine import (
    FunctionRuntimeQuarantine,
)
from app.modules.function.application.function_runtime_route_resolver import (
    FunctionRuntimeRouteResolver,
)
from app.modules.function.config import function_settings
from app.modules.function.contracts.runtime import (
    RuntimeAcceptedResponse,
    RuntimeIdentity,
    RuntimeInvocationRequest,
    RuntimeTerminalRequest,
    invocation_payload,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunRuntimeContext,
)


logger = get_logger(__name__)


def runtime_gateway_url() -> str:
    configured = function_settings.function_runtime_gateway_url or settings.api_url
    return configured.rstrip("/")


class FunctionRuntimeInvoker:
    def __init__(
        self,
        *,
        routes: FunctionRuntimeRouteResolver,
        quarantine: FunctionRuntimeQuarantine,
        runtime_http_client_factory: Callable[[], httpx.AsyncClient],
    ) -> None:
        self._routes = routes
        self._quarantine = quarantine
        self._runtime_http_client_factory = runtime_http_client_factory

    async def cancel(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        endpoint: FunctionRuntimeEndpoint | None = None,
    ) -> None:
        """Ask the runtime to stop the run. Best effort: the run's state is
        already settled in the database whether or not this lands."""
        try:
            control_endpoint = endpoint or await self._routes.control_endpoint(dispatch)
            await self._runtime_http_client_factory().post(
                urljoin(
                    control_endpoint.url,
                    f"functions/{dispatch.function_id}/runs/{dispatch.run_id}:cancel",
                ),
                headers=control_endpoint.headers(),
                timeout=httpx.Timeout(5),
            )
        except Exception:
            logger.warning(
                "function.dispatcher.runtime_cancellation.failed",
                run_id=str(dispatch.run_id),
                exc_info=True,
            )

    async def post(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        context: FunctionRunRuntimeContext,
        endpoint: FunctionRuntimeEndpoint,
        function_token: str,
        organization_id: str | None,
    ) -> RuntimeTerminalRequest | RuntimeAcceptedResponse:
        remaining = (context.deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise TimeoutError("function execution deadline elapsed")
        url = urljoin(
            endpoint.url,
            f"functions/{dispatch.function_id}/runs/{dispatch.run_id}",
        )
        headers = {
            **endpoint.headers(),
            "Authorization": f"Bearer {function_token}",
            "If-Match": f'"{dispatch.revision_hash}"',
            "X-Lemma-Gateway-Url": runtime_gateway_url(),
        }
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            headers["Prefer"] = "respond-async"
        body = RuntimeInvocationRequest(
            input=context.input_data,
            config=context.config,
            identity=RuntimeIdentity(
                user_id=context.user_id,
                user_email=context.user_email,
                contact_id=context.contact_id,
                pod_id=context.pod_id,
                function_id=context.function_id,
                function_name=context.function_name,
                organization_id=UUID(organization_id) if organization_id else None,
            ),
            lemma_base_url=runtime_gateway_url(),
            deadline_at=context.deadline_at,
        )
        try:
            runtime = self._runtime_http_client_factory()
            response = await runtime.post(
                url,
                headers=headers,
                json=invocation_payload(body),
                timeout=httpx.Timeout(
                    max(0.1, min(10.0, remaining)),
                    read=max(0.1, remaining),
                ),
            )
        except httpx.TimeoutException as exc:
            # Our own deadline expired. Still unconfirmed — the sandbox may be
            # mid-execution, so this must not be replayed — but the run's error
            # should name the deadline, which is the cause the caller can act on,
            # rather than the generic "response was lost".
            raise InvocationOutcomeUnconfirmed(
                "function invocation exceeded its deadline"
            ) from exc
        except httpx.ConnectError as exc:
            # The connection was never established, so the request was never
            # delivered and the run cannot have started: it can be re-resolved
            # and replayed rather than failed. If the runtime is dead it must
            # not be handed to the next run either; the probe decides that.
            await self._quarantine.after_failure(dispatch.pod_id, endpoint)
            raise RuntimeNeverReached(
                "function runtime refused the connection"
            ) from exc
        except httpx.TransportError as exc:
            # Bytes crossed the wire and then something broke -- a read error, a
            # half-written request, a server that hung up mid-response. The run
            # may be underway with only the response lost, so this must not be
            # replayed, whatever the probe then says about the sandbox.
            await self._quarantine.after_failure(dispatch.pod_id, endpoint)
            raise InvocationOutcomeUnconfirmed(
                "function invocation response was lost"
            ) from exc
        return await self._read(dispatch, endpoint, response)

    async def _read(
        self,
        dispatch: FunctionExecutionDispatch,
        endpoint: FunctionRuntimeEndpoint,
        response: httpx.Response,
    ) -> RuntimeTerminalRequest | RuntimeAcceptedResponse:
        if response.status_code == 503:
            # Unavailable, not broken: refused before any work began, so it is
            # replayed like a refused connection. Never a reason to destroy the
            # sandbox other runs are using; only this lease is dropped.
            await self._routes.invalidate(dispatch, endpoint)
            raise RuntimeNeverReached("function runtime was unavailable (503)")
        if response.status_code >= 500:
            # Reachable and failing. A dead runtime process answers 502 here,
            # and every later run used to be handed the same sandbox; the probe
            # tells that apart from one bad request on a live runtime.
            await self._quarantine.after_failure(dispatch.pod_id, endpoint)
            raise InvocationOutcomeUnconfirmed(
                f"function runtime returned {response.status_code}"
            )
        if response.status_code in {401, 403, 404, 410}:
            await self._routes.invalidate(dispatch, endpoint)
        response.raise_for_status()
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            if response.status_code != 202:
                raise InvocationOutcomeUnconfirmed(
                    "function runtime did not acknowledge asynchronous execution"
                )
            accepted = RuntimeAcceptedResponse.model_validate(response.json())
            if accepted.run_id != dispatch.run_id:
                raise InvocationOutcomeUnconfirmed(
                    "function runtime acknowledged a different run"
                )
            return accepted
        if response.status_code != 200:
            raise InvocationOutcomeUnconfirmed(
                "function runtime did not return a terminal API response"
            )
        return RuntimeTerminalRequest.model_validate(response.json())
