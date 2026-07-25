"""Durable function dispatcher over AgentBox's resident sandbox runtime API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from uuid import UUID

import httpx

from agentbox_client import (
    AdmissionClass,
    AgentBoxApiError,
    AgentBoxClient,
    ProfileRef,
    RetryDisposition,
    WorkloadKind,
)

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.redaction import redact_text
from app.core.request_context import create_inherited_task
from app.modules.function.application.function_callback_credentials import (
    FunctionCallbackCredentialSigner,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenCache,
    FunctionSessionTokenKey,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
    FunctionRuntimeEndpointKey,
)
from app.modules.function.contracts.runtime import (
    RuntimeAcceptedResponse,
    RuntimeTerminalRequest,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunStatus,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository


logger = get_logger(__name__)
_TERMINAL_RUN_STATES = {
    FunctionRunStatus.COMPLETED,
    FunctionRunStatus.FAILED,
    FunctionRunStatus.CANCELLED,
}
_FUNCTION_RUNTIME_PORT = 8090


AgentBoxClientFactory = Callable[[], AgentBoxClient]
RuntimeHttpClientFactory = Callable[[], httpx.AsyncClient]
TokenMinter = Callable[..., Awaitable[str]]


class FunctionDispatcher:
    """Resolve one persisted run, execute externally, then read terminal state.

    No method keeps a Unit of Work alive across an AgentBox request, sleep,
    output wait, identity call, or object-store call.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        credential_signer: FunctionCallbackCredentialSigner,
        agentbox_client_factory: AgentBoxClientFactory,
        token_minter: TokenMinter,
        token_cache: FunctionSessionTokenCache,
        endpoint_cache: FunctionRuntimeEndpointCache,
        runtime_http_client_factory: RuntimeHttpClientFactory,
        delegated_tokens_enabled: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._signer = credential_signer
        self._agentbox_client_factory = agentbox_client_factory
        self._token_minter = token_minter
        self._token_cache = token_cache
        self._endpoint_cache = endpoint_cache
        self._runtime_http_client_factory = runtime_http_client_factory
        self._delegated_tokens_enabled = delegated_tokens_enabled
        self._profile = ProfileRef(
            name=settings.agentbox_function_profile_name,
            digest=settings.agentbox_function_profile_digest,
        )

    async def execute(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionRunEntity:
        dispatch = await self._resolve_dispatch(run_id, mode=mode)
        if isinstance(dispatch, FunctionRunEntity):
            return dispatch

        function_token_task = create_inherited_task(
            self._function_session_token(dispatch)
        )
        try:
            endpoint = await self._runtime_endpoint(dispatch)
            function_token = await function_token_task
            runtime_response = await self._invoke_runtime_with_recovery(
                dispatch,
                endpoint=endpoint,
                function_token=function_token,
            )
            if isinstance(runtime_response, FunctionRunEntity):
                result = runtime_response
            elif isinstance(runtime_response, RuntimeAcceptedResponse):
                # The resident runtime returns 202 only after its backend claim
                # committed PENDING -> RUNNING. Long JOB/deferred execution then
                # belongs to the runtime callback, not to a backend worker poll.
                result = await self._load_run(dispatch.run_id)
            else:
                result = await self._load_run(dispatch.run_id)
            if (
                dispatch.mode == FunctionDispatchMode.SYNCHRONOUS
                and result.status not in _TERMINAL_RUN_STATES
            ):
                raise InvocationOutcomeUnconfirmed(
                    "function runtime returned before durable terminal state"
                )
            return result
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                await self._best_effort_cancel(dispatch)
                async with self._uow_factory() as uow:
                    await FunctionExecutionRepository(
                        uow, self._signer
                    ).cancel_dispatch(dispatch)
                raise
            if isinstance(exc, InvocationOutcomeUnconfirmed):
                current = await self._load_run(run_id)
                if self._durably_confirms_invocation(dispatch, current):
                    return current
                await self._best_effort_cancel(dispatch)
            message = self._execution_error(exc)
            async with self._uow_factory() as uow:
                failed = await FunctionExecutionRepository(
                    uow, self._signer
                ).fail_dispatch(dispatch, error=message)
            if failed is None:
                failed = await self._load_run(run_id)
            return failed
        finally:
            if not function_token_task.done():
                function_token_task.cancel()
            await asyncio.gather(function_token_task, return_exceptions=True)

    async def cancel(self, run_id: UUID) -> FunctionRunEntity:
        dispatch = await self._active_dispatch(
            run_id,
            mode=FunctionDispatchMode.ASYNCHRONOUS,
        )
        if dispatch is None:
            return await self._load_run(run_id)
        endpoint = await self._runtime_endpoint(
            dispatch,
            deadline_at=self._control_deadline(dispatch.deadline_at),
        )
        runtime = self._runtime_http_client_factory()
        await runtime.post(
            urljoin(
                endpoint.url,
                f"runs/{dispatch.run_id}:cancel",
            ),
            headers={"Authorization": self._callback_authorization(run_id)},
            timeout=httpx.Timeout(5, read=5),
        )
        async with self._uow_factory() as uow:
            run = await FunctionExecutionRepository(uow, self._signer).cancel_dispatch(
                dispatch
            )
        return run or await self._load_run(run_id)

    async def _resolve_dispatch(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionExecutionDispatch | FunctionRunEntity:
        async with self._uow_factory() as uow:
            resolved = await FunctionExecutionRepository(
                uow, self._signer
            ).resolve_dispatch(run_id, mode=mode)
        if resolved is None:
            raise LookupError(f"function run {run_id} does not exist")
        if (
            isinstance(resolved, FunctionExecutionDispatch)
            and self._now() >= resolved.deadline_at
        ):
            async with self._uow_factory() as uow:
                failed = await FunctionExecutionRepository(
                    uow, self._signer
                ).fail_unfinished(
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
            return await FunctionExecutionRepository(uow, self._signer).active_dispatch(
                run_id, mode=mode
            )

    async def _ensure_sandbox(
        self,
        client: AgentBoxClient,
        dispatch: FunctionExecutionDispatch,
        *,
        deadline_at: datetime,
    ) -> None:
        while self._now() < deadline_at:
            try:
                handle = await client.ensure_sandbox(
                    WorkloadKind.FUNCTION,
                    dispatch.pod_id,
                    profile=self._profile,
                    admission_class=(
                        AdmissionClass.LATENCY
                        if dispatch.mode == FunctionDispatchMode.SYNCHRONOUS
                        else AdmissionClass.BATCH
                    ),
                    deadline_at=deadline_at,
                )
            except AgentBoxApiError as exc:
                if exc.retry not in {
                    RetryDisposition.WAIT,
                    RetryDisposition.SAFE_SAME_OPERATION,
                }:
                    raise
                await self._wait_retry(exc.retry_after_ms, deadline_at)
                continue
            except httpx.TransportError:
                # Ensuring a logical key is idempotent; AgentBox owns exact-create
                # reconciliation through its durable allocation token.
                await self._wait_retry(None, deadline_at)
                continue
            if handle.ready:
                return
            await self._wait_retry(handle.retry_after_ms, deadline_at)
        raise TimeoutError("function sandbox was not ready before the deadline")

    async def _invoke_runtime_with_recovery(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        endpoint: FunctionRuntimeEndpoint,
        function_token: str,
    ) -> RuntimeTerminalRequest | RuntimeAcceptedResponse | FunctionRunEntity:
        current_endpoint = endpoint
        for attempt in range(2):
            try:
                return await self._invoke_runtime(
                    dispatch,
                    endpoint=current_endpoint,
                    function_token=function_token,
                )
            except InvocationOutcomeUnconfirmed as exc:
                current = await self._load_run(dispatch.run_id)
                if self._durably_confirms_invocation(dispatch, current):
                    return current
                if attempt == 0 and exc.safe_same_operation:
                    # A transport may discard a stale keep-alive connection
                    # before the request reaches the resident runtime. Reusing
                    # the exact immutable run identity is safe: the runtime
                    # deduplicates run IDs and the backend claim is an atomic
                    # PENDING -> RUNNING transition.
                    await self._wait_retry(
                        exc.retry_after_ms,
                        dispatch.deadline_at,
                    )
                    current_endpoint = await self._runtime_endpoint(dispatch)
                    continue
                raise
        raise AssertionError("unreachable function invocation retry state")

    async def _invoke_runtime(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        endpoint: FunctionRuntimeEndpoint,
        function_token: str,
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
            # This capability cannot authorize execution. It lets the backend
            # cancel the exact run even during the narrow interval between
            # runtime task creation and the durable claim response.
            "X-Lemma-Run-Token": self._signer.derive(dispatch.run_id),
        }
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            headers["Prefer"] = "respond-async"
        try:
            runtime = self._runtime_http_client_factory()
            response = await runtime.post(
                url,
                headers=headers,
                json={"input": dispatch.input_data},
                timeout=httpx.Timeout(
                    max(0.1, min(10.0, remaining)),
                    read=max(0.1, remaining),
                ),
            )
        except httpx.TransportError as exc:
            await self._endpoint_cache.invalidate(
                self._endpoint_key(dispatch),
                endpoint=endpoint,
            )
            raise InvocationOutcomeUnconfirmed(
                "function invocation response was lost",
                safe_same_operation=True,
                retry_after_ms=100,
            ) from exc
        if response.status_code >= 500:
            await self._endpoint_cache.invalidate(
                self._endpoint_key(dispatch),
                endpoint=endpoint,
            )
            raise InvocationOutcomeUnconfirmed(
                f"function runtime returned {response.status_code}",
                safe_same_operation=True,
                retry_after_ms=self._runtime_retry_after_ms(response),
            )
        if response.status_code in {401, 403, 404, 410}:
            await self._endpoint_cache.invalidate(
                self._endpoint_key(dispatch),
                endpoint=endpoint,
            )
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

    async def _best_effort_cancel(self, dispatch: FunctionExecutionDispatch) -> None:
        try:
            endpoint = await self._runtime_endpoint(
                dispatch,
                deadline_at=self._control_deadline(dispatch.deadline_at),
            )
            runtime = self._runtime_http_client_factory()
            await runtime.post(
                urljoin(
                    endpoint.url,
                    f"runs/{dispatch.run_id}:cancel",
                ),
                headers={
                    "Authorization": self._callback_authorization(dispatch.run_id)
                },
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
        *,
        deadline_at: datetime | None = None,
    ) -> FunctionRuntimeEndpoint:
        effective_deadline = deadline_at or dispatch.deadline_at
        return await self._endpoint_cache.get(
            self._endpoint_key(dispatch),
            loader=lambda: self._load_runtime_endpoint(
                dispatch,
                deadline_at=effective_deadline,
            ),
        )

    async def _load_runtime_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        deadline_at: datetime,
    ) -> FunctionRuntimeEndpoint:
        client = self._agentbox_client_factory()
        try:
            await self._ensure_sandbox(
                client,
                dispatch,
                deadline_at=deadline_at,
            )
            grant = await client.create_port_access(
                WorkloadKind.FUNCTION,
                dispatch.pod_id,
                _FUNCTION_RUNTIME_PORT,
                expires_at=self._port_access_expiry(deadline_at),
            )
            return FunctionRuntimeEndpoint(
                url=grant.url,
                expires_at=grant.expires_at,
            )
        finally:
            await client.close()

    def _endpoint_key(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpointKey:
        return FunctionRuntimeEndpointKey(
            pod_id=dispatch.pod_id,
            profile_digest=self._profile.digest,
        )

    async def _load_run(self, run_id: UUID) -> FunctionRunEntity:
        async with self._uow_factory() as uow:
            run = await FunctionRunRepository(uow).get_run(run_id)
        if run is None:
            raise LookupError(f"function run {run_id} does not exist")
        return run

    async def _function_session_token(self, dispatch: FunctionExecutionDispatch) -> str:
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
        )

    @staticmethod
    async def _wait_retry(retry_after_ms: int | None, deadline_at: datetime) -> None:
        remaining = (deadline_at - FunctionDispatcher._now()).total_seconds()
        if remaining <= 0:
            return
        delay = max(0.05, (retry_after_ms or 200) / 1000)
        await asyncio.sleep(min(delay, remaining))

    @staticmethod
    def _runtime_gateway_url() -> str:
        configured = settings.function_runtime_gateway_url or settings.api_url
        return configured.rstrip("/")

    @staticmethod
    def _control_deadline(execution_deadline: datetime) -> datetime:
        return max(execution_deadline, FunctionDispatcher._now() + timedelta(seconds=5))

    @staticmethod
    def _port_access_expiry(execution_deadline: datetime) -> datetime:
        return min(
            execution_deadline + timedelta(seconds=10),
            FunctionDispatcher._now() + timedelta(hours=23, minutes=55),
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _callback_authorization(self, run_id: UUID) -> str:
        return f"Bearer {self._signer.derive(run_id)}"

    @staticmethod
    def _durably_confirms_invocation(
        dispatch: FunctionExecutionDispatch,
        run: FunctionRunEntity,
    ) -> bool:
        return run.status in _TERMINAL_RUN_STATES or (
            dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS
            and run.status == FunctionRunStatus.RUNNING
        )

    @staticmethod
    def _execution_error(exc: BaseException) -> str:
        if isinstance(exc, InvocationOutcomeUnconfirmed):
            return "Function execution failed because the runtime response was not confirmed"
        if isinstance(exc, TimeoutError):
            return "Function execution timed out (deadline exceeded)"
        if isinstance(exc, AgentBoxApiError):
            code = str(getattr(exc, "code", "PROVIDER_UNAVAILABLE"))
            if code.upper() == "DEADLINE_EXCEEDED":
                return "Function execution timed out (deadline exceeded)"
            return f"Function sandbox error ({redact_text(code)})"
        return "Function execution failed"

    @staticmethod
    def _runtime_retry_after_ms(response: httpx.Response) -> int:
        """Read AgentBox's bounded retry hint without trusting response detail."""

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
    """The runtime response was not confirmed; the run still terminates FAILED."""

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
