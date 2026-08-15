"""Durable function dispatcher over the sandbox runtime's resident API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from uuid import UUID

import httpx
from opentelemetry import trace


from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.concurrency.offload import run_blocking
from app.core.redaction import redact_text
from sandbox_runtime.errors import (
    SandboxError,
    SandboxUnavailable,
)
from app.core.request_context import create_inherited_task
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
    FunctionSessionTokenKey,
)
from app.modules.function.application.runtime_policy import (
    FUNCTION_JOB_CALLBACK_GRACE_SECONDS,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_route_resolver import (
    SandboxClientFactory,
    FunctionRuntimeRouteResolver,
)
from app.modules.function.contracts.runtime import (
    RuntimeAcceptedResponse,
    RuntimeIdentity,
    RuntimeInvocationRequest,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunRuntimeContext,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository


logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# What a run keeps of its own output, and how much extra is redacted before
# trimming so a credential cannot survive by straddling the cut. The margin is
# far larger than any single credential (a PEM private key block is a few KB).
_LOG_LIMIT_BYTES = 4 * 1024 * 1024
_REDACTION_MARGIN_BYTES = 64 * 1024


RuntimeHttpClientFactory = Callable[[], httpx.AsyncClient]
TokenMinter = Callable[..., Awaitable[FunctionSessionToken]]
OrganizationResolver = Callable[[UUID | None], Awaitable[str | None]]


class FunctionDispatcher:
    """Start one persisted run, invoke the sandbox, and persist its result.

    No method keeps a Unit of Work alive across sandbox, identity, sleep, or
    sandbox I/O. The backend owns the PENDING -> RUNNING transition; the
    runtime receives the complete immutable envelope and never claims it back.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        sandbox_client_factory: SandboxClientFactory,
        token_minter: TokenMinter,
        token_cache: FunctionSessionTokenCache,
        endpoint_cache: FunctionRuntimeEndpointCache,
        runtime_http_client_factory: RuntimeHttpClientFactory,
        organization_resolver: OrganizationResolver,
        delegated_tokens_enabled: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._token_minter = token_minter
        self._token_cache = token_cache
        self._routes = FunctionRuntimeRouteResolver(
            sandbox_client_factory=sandbox_client_factory,
            endpoint_cache=endpoint_cache,
        )
        self._runtime_http_client_factory = runtime_http_client_factory
        self._organization_resolver = organization_resolver
        self._delegated_tokens_enabled = delegated_tokens_enabled

    async def execute(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionRunEntity:
        # Spanned per phase because the gap between a run being created and
        # reaching RUNNING was measurable (p50 ~0.7s warm and uncontended) but
        # not attributable: this path had no instrumentation at all, so the only
        # visible boundary was the whole worker task. Each phase below is one of
        # the candidates, and the cache spans record hit or miss.
        with tracer.start_as_current_span("lemma.function.dispatch") as span:
            span.set_attribute("lemma.run_id", str(run_id))
            span.set_attribute("lemma.dispatch_mode", mode.value)
            with tracer.start_as_current_span("lemma.function.dispatch.resolve"):
                dispatch = await self._resolve_dispatch(run_id, mode=mode)
            if isinstance(dispatch, FunctionRunEntity):
                return dispatch
            span.set_attribute("lemma.pod_id", str(dispatch.pod_id))
            return await self._execute_dispatch(run_id, dispatch)

    async def _execute_dispatch(
        self,
        run_id: UUID,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRunEntity:
        endpoint_task = create_inherited_task(self._runtime_endpoint(dispatch))
        token_task = create_inherited_task(self._function_session_token(dispatch))
        organization_task = create_inherited_task(
            self._resolve_organization(dispatch.pod_id)
        )
        endpoint: FunctionRuntimeEndpoint | None = None
        started: FunctionRunRuntimeContext | None = None
        try:
            # Concurrent, so the span around the gather measures the slowest of
            # the three rather than their sum. Which one that is comes from the
            # child spans each of them opens.
            with tracer.start_as_current_span("lemma.function.dispatch.prepare"):
                endpoint, function_token, organization_id = await asyncio.gather(
                    endpoint_task,
                    token_task,
                    organization_task,
                )
            with tracer.start_as_current_span("lemma.function.dispatch.start"):
                started = await self._start_dispatch(dispatch)
            if started is None:
                return await self._load_run(run_id)

            runtime_response = await self._invoke_runtime_with_recovery(
                dispatch,
                context=started,
                endpoint=endpoint,
                function_token=function_token.value,
                organization_id=organization_id,
            )
            if isinstance(runtime_response, RuntimeAcceptedResponse):
                return await self._load_run(run_id)
            return await self._complete_dispatch(started, runtime_response)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                async with self._uow_factory() as uow:
                    await FunctionExecutionRepository(uow).cancel_dispatch(dispatch)
                await self._best_effort_cancel(dispatch, endpoint=endpoint)
                raise
            if (
                isinstance(exc, InvocationOutcomeUnconfirmed)
                and dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS
                and started is not None
            ):
                # The runtime may still own the task. Its callback or the
                # deadline reconciler will durably settle this JOB run.
                return await self._load_run(run_id)
            if isinstance(exc, InvocationOutcomeUnconfirmed) and started is not None:
                await self._best_effort_cancel(dispatch, endpoint=endpoint)
            message = self._execution_error(exc)
            # Every branch above turns a *recognised* failure into a specific
            # message. Anything reaching the generic fallback is by definition
            # unanticipated, so it is the one most worth recording: otherwise a
            # run stores "Function execution failed" with no way to find out why.
            logger.warning(
                "function.function_dispatcher.execution_failed",
                run_id=str(run_id),
                error_type=type(exc).__name__,
            )
            async with self._uow_factory() as uow:
                failed = await FunctionExecutionRepository(uow).fail_dispatch(
                    dispatch,
                    error=message,
                )
            return failed or await self._load_run(run_id)
        finally:
            for task in (endpoint_task, token_task, organization_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                endpoint_task,
                token_task,
                organization_task,
                return_exceptions=True,
            )

    async def cancel(self, run_id: UUID) -> FunctionRunEntity:
        dispatch = await self._active_dispatch(
            run_id,
            mode=FunctionDispatchMode.ASYNCHRONOUS,
        )
        if dispatch is None:
            return await self._load_run(run_id)
        async with self._uow_factory() as uow:
            run = await FunctionExecutionRepository(uow).cancel_dispatch(dispatch)
        await self._best_effort_cancel(dispatch)
        return run or await self._load_run(run_id)

    async def _resolve_dispatch(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionExecutionDispatch | FunctionRunEntity:
        async with self._uow_factory() as uow:
            resolved = await FunctionExecutionRepository(uow).resolve_dispatch(
                run_id,
                mode=mode,
            )
        if resolved is None:
            raise LookupError(f"function run {run_id} does not exist")
        if (
            isinstance(resolved, FunctionExecutionDispatch)
            and self._now() >= resolved.deadline_at
        ):
            async with self._uow_factory() as uow:
                failed = await FunctionExecutionRepository(uow).fail_unfinished(
                    run_id,
                    error="Function execution deadline exceeded",
                )
            return failed or await self._load_run(run_id)
        return resolved

    async def _active_dispatch(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionExecutionDispatch | None:
        async with self._uow_factory() as uow:
            return await FunctionExecutionRepository(uow).active_dispatch(
                run_id,
                mode=mode,
            )

    async def _start_dispatch(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRunRuntimeContext | None:
        async with self._uow_factory() as uow:
            return await FunctionExecutionRepository(uow).start_execution(dispatch)

    async def _complete_dispatch(
        self,
        context: FunctionRunRuntimeContext,
        terminal: RuntimeTerminalRequest,
    ) -> FunctionRunEntity:
        # Off the loop: even after trimming, this is megabytes through thirteen
        # regex passes, and this runs on the API's loop when the runtime posts
        # its terminal callback.
        logs = await run_blocking(
            self._terminal_logs, terminal, limiter="cpu_bound"
        )
        error = (
            self._runtime_failure_message(terminal)
            if terminal.error is not None
            else None
        )
        async with self._uow_factory() as uow:
            run, accepted, _duplicate = await FunctionExecutionRepository(uow).complete(
                context,
                completed=terminal.status == "completed",
                output_data=terminal.output_data,
                error=error,
                logs=logs,
            )
        if run is None:
            raise LookupError(f"function run {context.run_id} does not exist")
        if not accepted:
            raise InvocationOutcomeUnconfirmed("function terminal state was rejected")
        return run

    async def _invoke_runtime_with_recovery(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        context: FunctionRunRuntimeContext,
        endpoint: FunctionRuntimeEndpoint,
        function_token: str,
        organization_id: str | None,
    ) -> RuntimeTerminalRequest | RuntimeAcceptedResponse:
        # Provider-gateway 401/403/404/410 responses happen before user code can
        # start. Refreshing an allocation-fenced endpoint once is safe. Transport
        # errors and runtime 5xx responses are ambiguous and must never be replayed.
        try:
            return await self._invoke_runtime(
                dispatch,
                context=context,
                endpoint=endpoint,
                function_token=function_token,
                organization_id=organization_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {401, 403, 404, 410}:
                raise
            refreshed = await self._runtime_endpoint(dispatch)
            return await self._invoke_runtime(
                dispatch,
                context=context,
                endpoint=refreshed,
                function_token=function_token,
                organization_id=organization_id,
            )

    async def _invoke_runtime(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        context: FunctionRunRuntimeContext,
        endpoint: FunctionRuntimeEndpoint,
        function_token: str,
        organization_id: str | None,
    ) -> RuntimeTerminalRequest | RuntimeAcceptedResponse:
        remaining = (dispatch.deadline_at - self._now()).total_seconds()
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
            "X-Lemma-Gateway-Url": self._runtime_gateway_url(),
        }
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            headers["Prefer"] = "respond-async"
        body = RuntimeInvocationRequest(
            input=context.input_data,
            config=context.config,
            identity=RuntimeIdentity(
                user_id=context.user_id,
                user_email=context.user_email,
                pod_id=context.pod_id,
                function_id=context.function_id,
                function_name=context.function_name,
                organization_id=UUID(organization_id) if organization_id else None,
            ),
            lemma_base_url=self._runtime_gateway_url(),
            deadline_at=context.deadline_at,
        )
        try:
            runtime = self._runtime_http_client_factory()
            response = await runtime.post(
                url,
                headers=headers,
                json=body.model_dump(mode="json"),
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
        except httpx.TransportError as exc:
            raise InvocationOutcomeUnconfirmed(
                "function invocation response was lost"
            ) from exc
        if response.status_code >= 500:
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

    async def _best_effort_cancel(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        endpoint: FunctionRuntimeEndpoint | None = None,
    ) -> None:
        try:
            control_endpoint = endpoint or await self._routes.control_endpoint(dispatch)
            runtime = self._runtime_http_client_factory()
            await runtime.post(
                urljoin(
                    control_endpoint.url,
                    (f"functions/{dispatch.function_id}/runs/{dispatch.run_id}:cancel"),
                ),
                headers=control_endpoint.headers(),
                timeout=httpx.Timeout(5),
            )
        except Exception:
            logger.warning(
                "function.dispatcher.runtime_cancellation.failed",
                run_id=str(dispatch.run_id),
            )

    # Above this, the lease was not reusable and a sandbox had to be brought up.
    # A warm reuse is a dict lookup; anything in this range is a control-plane
    # round trip and, usually, a container start.
    _COLD_ENDPOINT_MS = 250.0

    async def _runtime_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpoint:
        """Acquire the runtime lease, and say how much it cost.

        This is the largest single component of function latency and nothing
        measured it. Production over seven days: 8,877 runs, a median wait of
        2.6s between the run row being created and the function starting, p95
        8.3s. Split by whether the pod had run anything recently, the median is
        727ms warm against 3,272ms cold -- and 64% of runs are cold.

        That is not a bug; it is the cost side of a deliberate trade. The lease
        horizon is kept short on purpose because the sandbox runtime treats a
        lease as activity, so a generous horizon keeps idle sandboxes billing
        (see ``function_runtime_endpoint_reuse_seconds``). But the trade was
        being made blind: there was no signal anywhere saying how often a run
        pays for a cold start, so nobody could tell what lengthening the horizon
        would buy or cost. This is that signal.
        """
        started = time.monotonic()
        endpoint = await self._routes.endpoint(dispatch)
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info(
            "function.runtime.endpoint_acquired",
            pod_id=str(dispatch.pod_id),
            elapsed_ms=round(elapsed_ms, 1),
            cold=elapsed_ms >= self._COLD_ENDPOINT_MS,
            mode=dispatch.mode.value,
        )
        return endpoint

    async def _load_run(self, run_id: UUID) -> FunctionRunEntity:
        async with self._uow_factory() as uow:
            run = await FunctionRunRepository(uow).get_run(run_id)
        if run is None:
            raise LookupError(f"function run {run_id} does not exist")
        return run

    async def _resolve_organization(self, pod_id: UUID) -> str | None:
        return await self._organization_resolver(pod_id)

    async def _function_session_token(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionSessionToken:
        required_until = dispatch.deadline_at
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            required_until += timedelta(seconds=FUNCTION_JOB_CALLBACK_GRACE_SECONDS)
        return await self._token_cache.get(
            FunctionSessionTokenKey(
                user_id=dispatch.user_id,
                pod_id=dispatch.pod_id,
                function_id=dispatch.function_id,
                revision_hash=dispatch.revision_hash,
                workload_name=dispatch.function_name,
                scope=(),
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            ),
            minter=self._token_minter,
            min_validity_until=required_until,
        )

    @staticmethod
    def _runtime_gateway_url() -> str:
        configured = settings.function_runtime_gateway_url or settings.api_url
        return configured.rstrip("/")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _terminal_logs(request: RuntimeTerminalRequest) -> str | None:
        """Redact what we are keeping, not what we are about to throw away.

        This used to redact the whole of stdout+stderr — up to 8 MiB — with
        thirteen regex passes, and then keep the first 4 MiB. Half the work was
        spent on text nobody would ever see, on the event loop.

        Cutting first is safe as long as the cut is not where a secret is: the
        slice keeps a margin past the limit, redacts that, and only then trims
        to size, so any credential straddling the final boundary is still
        inside the window the patterns ran over.
        """
        sections: list[str] = []
        if request.stdout:
            sections.append(request.stdout)
        if request.stderr:
            sections.append(request.stderr)
        if request.output_truncated:
            sections.append("[function output truncated]")
        if not sections:
            return None
        combined = "\n".join(sections)
        return redact_text(combined[: _LOG_LIMIT_BYTES + _REDACTION_MARGIN_BYTES])[
            :_LOG_LIMIT_BYTES
        ]

    @staticmethod
    def _runtime_failure_message(request: RuntimeTerminalRequest) -> str:
        assert request.error is not None
        if request.error.name == "TimeoutError":
            return "Function execution timed out (deadline exceeded)"
        return redact_text(f"{request.error.name}: {request.error.message}")[:16_384]

    @staticmethod
    def _execution_error(exc: BaseException) -> str:
        if isinstance(exc, InvocationOutcomeUnconfirmed):
            if isinstance(exc.__cause__, (httpx.TimeoutException, TimeoutError)):
                # Report the deadline (what the caller can change) while keeping
                # the "may have run" caveat (what they must not assume away).
                return (
                    "Function execution timed out (deadline exceeded); execution "
                    "may have started and was not retried"
                )
            return (
                "Function runtime response was not confirmed; execution may have "
                "started and was not retried"
            )
        if isinstance(exc, TimeoutError):
            return "Function execution timed out (deadline exceeded)"
        if isinstance(exc, SandboxError):
            # The type says whether waiting could have helped; the message says
            # what happened. Both go to a user reading a failed run.
            if isinstance(exc, SandboxUnavailable):
                return f"Function sandbox unavailable ({redact_text(str(exc))})"
            return f"Function sandbox refused the request ({redact_text(str(exc))})"
        if isinstance(exc, ValueError) and "token expires" in str(exc):
            return "Function execution exceeds delegated token lifetime"
        return "Function execution failed"


class InvocationOutcomeUnconfirmed(RuntimeError):
    """The runtime may have begun work, so the invocation must not be replayed."""
