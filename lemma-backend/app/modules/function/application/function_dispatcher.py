"""Durable function dispatcher over AgentBox's resident sandbox runtime API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import random
from urllib.parse import urljoin
from uuid import UUID

import httpx

from agentbox_client import AgentBoxApiError

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.redaction import redact_text
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
    AgentBoxClientFactory,
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


RuntimeHttpClientFactory = Callable[[], httpx.AsyncClient]
TokenMinter = Callable[..., Awaitable[FunctionSessionToken]]
OrganizationResolver = Callable[[UUID | None], Awaitable[str | None]]


class FunctionDispatcher:
    """Start one persisted run, invoke the sandbox, and persist its result.

    No method keeps a Unit of Work alive across AgentBox, identity, sleep, or
    sandbox I/O. The backend owns the PENDING -> RUNNING transition; the
    runtime receives the complete immutable envelope and never claims it back.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        agentbox_client_factory: AgentBoxClientFactory,
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
            agentbox_client_factory=agentbox_client_factory,
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
        dispatch = await self._resolve_dispatch(run_id, mode=mode)
        if isinstance(dispatch, FunctionRunEntity):
            return dispatch

        endpoint_task = create_inherited_task(self._runtime_endpoint(dispatch))
        token_task = create_inherited_task(self._function_session_token(dispatch))
        organization_task = create_inherited_task(
            self._resolve_organization(dispatch.pod_id)
        )
        endpoint: FunctionRuntimeEndpoint | None = None
        started: FunctionRunRuntimeContext | None = None
        try:
            endpoint, function_token, organization_id = await asyncio.gather(
                endpoint_task,
                token_task,
                organization_task,
            )
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
        logs = self._terminal_logs(terminal)
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
        for attempt in range(2):
            try:
                return await self._invoke_runtime(
                    dispatch,
                    context=context,
                    endpoint=endpoint,
                    function_token=function_token,
                    organization_id=organization_id,
                )
            except InvocationOutcomeUnconfirmed as exc:
                if attempt == 0 and exc.safe_same_operation:
                    await self._wait_retry(
                        exc.retry_after_ms,
                        dispatch.deadline_at,
                    )
                    # Retry the exact operation through the same allocation
                    # grant. The runtime deduplicates the immutable run ID.
                    continue
                raise
        raise AssertionError("unreachable function invocation retry state")

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
        except httpx.TransportError as exc:
            raise InvocationOutcomeUnconfirmed(
                "function invocation response was lost",
                safe_same_operation=True,
                retry_after_ms=100,
            ) from exc
        if response.status_code >= 500:
            raise InvocationOutcomeUnconfirmed(
                f"function runtime returned {response.status_code}",
                safe_same_operation=True,
                retry_after_ms=self._runtime_retry_after_ms(response),
            )
        if response.status_code in {404, 410}:
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
                timeout=httpx.Timeout(5),
            )
        except Exception:
            logger.warning(
                "function.dispatcher.runtime_cancellation.failed",
                run_id=str(dispatch.run_id),
            )

    async def _runtime_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpoint:
        return await self._routes.endpoint(dispatch)

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
    async def _wait_retry(
        retry_after_ms: int | None,
        deadline_at: datetime,
        *,
        attempt: int = 0,
    ) -> None:
        remaining = (deadline_at - FunctionDispatcher._now()).total_seconds()
        if remaining <= 0:
            return
        server_floor = max(0.0, (retry_after_ms or 0) / 1000)
        backoff = min(5.0, 0.5 * (2 ** min(attempt, 4)))
        delay = max(server_floor, backoff) * random.uniform(1.0, 1.2)
        await asyncio.sleep(min(delay, remaining))

    @staticmethod
    def _runtime_gateway_url() -> str:
        configured = settings.function_runtime_gateway_url or settings.api_url
        return configured.rstrip("/")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _terminal_logs(request: RuntimeTerminalRequest) -> str | None:
        sections: list[str] = []
        if request.stdout:
            sections.append(request.stdout)
        if request.stderr:
            sections.append(request.stderr)
        if request.output_truncated:
            sections.append("[function output truncated]")
        if not sections:
            return None
        return redact_text("\n".join(sections))[: 4 * 1024 * 1024]

    @staticmethod
    def _runtime_failure_message(request: RuntimeTerminalRequest) -> str:
        assert request.error is not None
        if request.error.name == "TimeoutError":
            return "Function execution timed out (deadline exceeded)"
        return redact_text(f"{request.error.name}: {request.error.message}")[:16_384]

    @staticmethod
    def _execution_error(exc: BaseException) -> str:
        if isinstance(exc, InvocationOutcomeUnconfirmed):
            return (
                "Function execution failed because the runtime response "
                "was not confirmed"
            )
        if isinstance(exc, TimeoutError):
            return "Function execution timed out (deadline exceeded)"
        if isinstance(exc, AgentBoxApiError):
            code = str(getattr(exc, "code", "PROVIDER_UNAVAILABLE"))
            if code.upper() == "DEADLINE_EXCEEDED":
                return "Function execution timed out (deadline exceeded)"
            return f"Function sandbox error ({redact_text(code)})"
        if isinstance(exc, ValueError) and "token expires" in str(exc):
            return "Function execution exceeds delegated token lifetime"
        return "Function execution failed"

    @staticmethod
    def _runtime_retry_after_ms(response: httpx.Response) -> int:
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            configured = (
                error.get("retry_after_ms") if isinstance(error, dict) else None
            )
            if isinstance(configured, int) and not isinstance(configured, bool):
                return max(50, min(configured, 1_000))
        except TypeError, ValueError:
            pass
        return 100


class InvocationOutcomeUnconfirmed(RuntimeError):
    """The runtime response was not confirmed."""

    def __init__(
        self,
        message: str,
        *,
        safe_same_operation: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.safe_same_operation = safe_same_operation
        self.retry_after_ms = retry_after_ms
