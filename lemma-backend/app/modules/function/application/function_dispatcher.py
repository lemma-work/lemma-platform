"""Durable function dispatcher over the sandbox runtime's resident API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from opentelemetry import trace


from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.concurrency.offload import run_blocking
from app.core.request_context import create_inherited_task
from app.modules.function.application.runtime_logs import terminal_logs
from app.modules.function.application.dispatch_failures import (
    InvocationOutcomeUnconfirmed,
    RuntimeNeverReached,
    execution_error,
    runtime_failure_message,
)
from app.modules.function.application.function_run_token import function_run_token
from app.core.infrastructure.work import Released
from app.modules.function.application.dispatch_failures import (
    PodAtCapacity,
    SandboxNotReady,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
    FunctionSessionTokenKey,
)
from app.modules.function.application.runtime_policy import (
    FUNCTION_JOB_CALLBACK_GRACE_SECONDS,
)
from sandbox_runtime.errors import SandboxUnavailable
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_invoker import (
    FunctionRuntimeInvoker,
)
from app.modules.function.application.function_runtime_quarantine import (
    FunctionRuntimeQuarantine,
)
from app.modules.function.application.function_runtime_route_resolver import (
    SandboxClientFactory,
    FunctionRuntimeRouteResolver,
)
from app.modules.function.contracts.runtime import (
    RuntimeAcceptedResponse,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunRuntimeContext,
    FunctionRunStatus,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository


logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# Allowed between preparing a run and starting it -- a cold start, mostly -- so
# the lease and token fetched beforehand still cover the execution window that
# starting opens. If a start takes longer, the lease is fetched again.
_START_ALLOWANCE_SECONDS = 60

#: How long a synchronous call waits between looks at a full pod's gate.
_CAPACITY_POLL_SECONDS = (0.25, 2.0)

POD_AT_CAPACITY_ERROR = (
    "Function never started: its pod was running as many functions as it can "
    "until this run's start deadline. Nothing ran, so it is safe to retry."
)
START_DEADLINE_ERROR = (
    "Function never started before its start deadline. Nothing ran, so it is "
    "safe to retry."
)


def _not_a_failure(exc: Exception, *, queued: bool, started: bool) -> bool:
    """Signals for the caller, not outcomes of the run: a full pod, a sandbox
    not up yet, or a queued run stopped before its start."""
    if isinstance(exc, (PodAtCapacity, SandboxNotReady)):
        return True
    return queued and not started and isinstance(exc, Released)


RuntimeHttpClientFactory = Callable[[], httpx.AsyncClient]
SafePoint = Callable[[], None]
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
        max_running_per_pod: int,
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
        self._max_running_per_pod = max_running_per_pod
        self._quarantine = FunctionRuntimeQuarantine(
            routes=self._routes,
            uow_factory=uow_factory,
            runtime_http_client_factory=runtime_http_client_factory,
        )
        self._invoker = FunctionRuntimeInvoker(
            routes=self._routes,
            quarantine=self._quarantine,
            runtime_http_client_factory=runtime_http_client_factory,
        )

    async def execute(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionRunEntity:
        """Run it now, for a caller holding a request open. A full pod is waited
        out up to the start deadline: the budget starts only when the run does."""
        # Spanned per phase because the gap between a run being created and
        # reaching RUNNING was measurable even warm and uncontended, but not
        # attributable: this path had no instrumentation at all, so the only
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
            pause, longest = _CAPACITY_POLL_SECONDS
            while True:
                try:
                    if await self._pod_has_room(dispatch.pod_id):
                        return await self._execute_dispatch(run_id, dispatch)
                except PodAtCapacity:
                    pass
                remaining = (dispatch.deadline_at - self._now()).total_seconds()
                if remaining <= 0:
                    return await self._fail_unstarted(
                        run_id, error=POD_AT_CAPACITY_ERROR
                    )
                await asyncio.sleep(min(pause, remaining))
                pause = min(pause * 2, longest)

    async def run_queued(
        self, run_id: UUID, *, before_start: SafePoint | None = None
    ) -> FunctionRunEntity:
        """Start and invoke a queued run (``function.run``). ``PodAtCapacity``
        and ``SandboxNotReady`` leave it PENDING for the item to come back to;
        a run already started is returned untouched, never started twice."""
        dispatch = await self._resolve_dispatch(
            run_id, mode=FunctionDispatchMode.ASYNCHRONOUS
        )
        if isinstance(dispatch, FunctionRunEntity):
            return dispatch
        # Unlocked and cheap; the start re-checks under the pod's lock.
        if not await self._pod_has_room(dispatch.pod_id):
            raise PodAtCapacity(str(dispatch.pod_id))
        return await self._execute_dispatch(
            run_id, dispatch, queued=True, before_start=before_start
        )

    async def _execute_dispatch(
        self,
        run_id: UUID,
        dispatch: FunctionExecutionDispatch,
        *,
        queued: bool = False,
        before_start: SafePoint | None = None,
    ) -> FunctionRunEntity:
        """Start it and invoke it. A ``queued`` run's work item can come back,
        so before the start an unreachable sandbox raises ``SandboxNotReady``
        and a stop leaves the run PENDING, instead of failing it."""
        execution_ends_at = self._now() + timedelta(
            seconds=dispatch.execution_seconds + _START_ALLOWANCE_SECONDS
        )
        endpoint_task = create_inherited_task(
            self._runtime_endpoint(dispatch, execution_ends_at=execution_ends_at)
        )
        token_task = create_inherited_task(
            self._function_session_token(dispatch, valid_until=execution_ends_at)
        )
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
                try:
                    endpoint, function_token, organization_id = await asyncio.gather(
                        endpoint_task,
                        token_task,
                        organization_task,
                    )
                except SandboxUnavailable as exc:
                    if not queued:
                        raise
                    raise SandboxNotReady(str(exc)) from exc
            if before_start is not None:
                before_start()
            with tracer.start_as_current_span("lemma.function.dispatch.start"):
                started = await self._start_dispatch(dispatch)
            if started is None:
                return await self._not_started(run_id, dispatch)
            endpoint = await self._endpoint_covering(dispatch, endpoint, started)

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
                # A queued run stopped before its start goes back to the queue
                # untouched; anything else that is cancelled ends cancelled.
                if started is not None or not queued:
                    async with self._uow_factory() as uow:
                        await FunctionExecutionRepository(uow).cancel_dispatch(dispatch)
                    await self._best_effort_cancel(dispatch, endpoint=endpoint)
                raise
            # Interpreter teardown is not a dispatch failure. `CancelledError`
            # is handled above because it has a durable outcome to record --
            # the dispatch is cancelled and the run says so. `KeyboardInterrupt`
            # and `SystemExit` have no such outcome: recording one marks the run
            # failed while the process is already on its way out, so a restart
            # leaves behind runs that report a failure that never happened.
            # `agent_runner_service` re-raises at the same boundary and for the
            # same reason -- swallowing it there made every interrupted run look
            # like a success, and a deploy ended every conversation in flight.
            if not isinstance(exc, Exception) or _not_a_failure(
                exc, queued=queued, started=started is not None
            ):
                raise
            return await self._after_failure(
                run_id, dispatch, exc, started=started, endpoint=endpoint
            )
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

    async def _after_failure(
        self,
        run_id: UUID,
        dispatch: FunctionExecutionDispatch,
        exc: Exception,
        *,
        started: FunctionRunRuntimeContext | None,
        endpoint: FunctionRuntimeEndpoint | None,
    ) -> FunctionRunEntity:
        """What a dispatch that raised leaves behind on its run."""
        unconfirmed = isinstance(exc, InvocationOutcomeUnconfirmed)
        if (
            unconfirmed
            and dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS
            and started is not None
        ):
            # The runtime may still own the task. Its callback or the run's
            # deadline item will durably settle this JOB run.
            return await self._load_run(run_id)
        if unconfirmed and started is not None:
            await self._best_effort_cancel(dispatch, endpoint=endpoint)
        message = self._execution_error(exc)
        # An unrecognised failure stores only "Function execution failed", so the
        # log is the one place that says why.
        logger.warning(
            "function.function_dispatcher.execution_failed",
            run_id=str(run_id),
            error_type=type(exc).__name__,
            # A bare `DBAPIError` names the wrapper, not the driver error.
            error=str(exc),
            exc_info=exc,
        )
        async with self._uow_factory() as uow:
            failed = await FunctionExecutionRepository(uow).fail_dispatch(
                dispatch,
                error=message,
            )
        return failed or await self._load_run(run_id)

    async def cancel(self, run_id: UUID) -> FunctionRunEntity:
        async with self._uow_factory() as uow:
            dispatch = await FunctionExecutionRepository(uow).active_dispatch(
                run_id, mode=FunctionDispatchMode.ASYNCHRONOUS
            )
        if dispatch is None:
            return await self._load_run(run_id)
        async with self._uow_factory() as uow:
            run = await FunctionExecutionRepository(uow).cancel_dispatch(dispatch)
        await self._best_effort_cancel(dispatch)
        return run or await self._load_run(run_id)

    async def cancel_in_runtime(self, run_id: UUID) -> None:
        """Ask the sandbox to stop a run already cancelled in the database."""
        async with self._uow_factory() as uow:
            dispatch = await FunctionExecutionRepository(uow).dispatch_of(
                run_id, mode=FunctionDispatchMode.ASYNCHRONOUS
            )
        if dispatch is not None:
            await self._best_effort_cancel(dispatch)

    async def _pod_has_room(self, pod_id: UUID) -> bool:
        async with self._uow_factory() as uow:
            running = await FunctionExecutionRepository(uow).running_on_pod(pod_id)
        return running < self._max_running_per_pod

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
            return await self._fail_unstarted(run_id, error=START_DEADLINE_ERROR)
        return resolved

    async def _not_started(
        self,
        run_id: UUID,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRunEntity:
        """Another dispatcher won the start, or the start deadline passed first.

        Nothing can start a run after its start deadline, so one still PENDING
        then never will: fail it now rather than answer a synchronous caller
        with a run that only the sweep will ever settle.
        """
        run = await self._load_run(run_id)
        if (
            run.status == FunctionRunStatus.PENDING
            and self._now() >= dispatch.deadline_at
        ):
            return await self._fail_unstarted(run_id, error=START_DEADLINE_ERROR)
        return run

    async def _fail_unstarted(self, run_id: UUID, *, error: str) -> FunctionRunEntity:
        async with self._uow_factory() as uow:
            failed = await FunctionExecutionRepository(uow).fail_unfinished(
                run_id,
                error=error,
            )
        return failed or await self._load_run(run_id)

    async def _endpoint_covering(
        self,
        dispatch: FunctionExecutionDispatch,
        endpoint: FunctionRuntimeEndpoint,
        started: FunctionRunRuntimeContext,
    ) -> FunctionRuntimeEndpoint:
        """The prepared lease, unless a slow start left it short of the deadline.

        The lease is what keeps the sandbox from being released as idle, so it
        must last as long as the run that was just started can.
        """
        needed = self._routes.lease_needed_until(dispatch, started.deadline_at)
        if endpoint.expires_at >= needed:
            return endpoint
        return await self._runtime_endpoint(
            dispatch,
            execution_ends_at=started.deadline_at,
            wait_until=started.deadline_at,
        )

    async def _start_dispatch(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRunRuntimeContext | None:
        async with self._uow_factory() as uow:
            return await FunctionExecutionRepository(uow).start_execution(
                dispatch, max_running_per_pod=self._max_running_per_pod
            )

    async def _complete_dispatch(
        self,
        context: FunctionRunRuntimeContext,
        terminal: RuntimeTerminalRequest,
    ) -> FunctionRunEntity:
        # Off the loop: even after trimming, this is megabytes through thirteen
        # regex passes, and this runs on the API's loop when the runtime posts
        # its terminal callback.
        logs = await run_blocking(self._terminal_logs, terminal, limiter="cpu_bound")
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
        # start. Refreshing an allocation-fenced endpoint once is safe. A refused
        # connection is the same situation reached a different way: the request
        # was never delivered. Everything else -- our own deadline, a broken read,
        # a runtime 5xx -- is ambiguous and must never be replayed.
        try:
            return await self._invoker.post(
                dispatch,
                context=context,
                endpoint=endpoint,
                function_token=function_token,
                organization_id=organization_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {401, 403, 404, 410}:
                raise
        except RuntimeNeverReached:
            # A dead runtime was quarantined on the way out, so re-resolving
            # cannot hand it back; a live one is simply asked again. Retried
            # once and once only: a second failure is a real one and is reported.
            logger.info(
                "function.runtime.reresolved_after_refused_connection",
                pod_id=str(dispatch.pod_id),
                run_id=str(dispatch.run_id),
            )
        refreshed = await self._runtime_endpoint(
            dispatch,
            execution_ends_at=context.deadline_at,
            wait_until=context.deadline_at,
        )
        return await self._invoker.post(
            dispatch,
            context=context,
            endpoint=refreshed,
            function_token=function_token,
            organization_id=organization_id,
        )

    async def _best_effort_cancel(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        endpoint: FunctionRuntimeEndpoint | None = None,
    ) -> None:
        await self._invoker.cancel(dispatch, endpoint=endpoint)

    # Above this, the lease was not reusable and a sandbox had to be brought up.
    # A warm reuse is a dict lookup; anything in this range is a control-plane
    # round trip and, usually, a container start.
    _COLD_ENDPOINT_MS = 250.0

    async def _runtime_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        execution_ends_at: datetime,
        wait_until: datetime | None = None,
    ) -> FunctionRuntimeEndpoint:
        """Acquire the runtime lease, and say how much it cost.

        Usually the largest single component of function latency, and most of
        it is cold starts. That is the cost side of a deliberate trade: the
        lease horizon is kept short because the sandbox runtime treats a lease
        as activity, so a generous horizon keeps idle sandboxes billing (see
        ``function_runtime_endpoint_reuse_seconds``). This log line is what
        says how often a run pays for that trade.
        """
        started = time.monotonic()
        endpoint = await self._routes.endpoint(
            dispatch,
            execution_ends_at=execution_ends_at,
            wait_until=wait_until,
        )
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
        *,
        valid_until: datetime,
    ) -> FunctionSessionToken:
        required_until = valid_until
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            required_until += timedelta(seconds=FUNCTION_JOB_CALLBACK_GRACE_SECONDS)
        if dispatch.user_id is None:
            return function_run_token(dispatch, required_until=required_until)
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
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    _terminal_logs = staticmethod(terminal_logs)
    _runtime_failure_message = staticmethod(runtime_failure_message)
    _execution_error = staticmethod(execution_error)
