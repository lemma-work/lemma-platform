"""Function execution engine — all sandbox/agentbox machinery for running a
function and extracting its schemas.

``FunctionRunExecutor`` operates on already-resolved + already-authorized
``(function, run)`` entities. It holds **no** ``FunctionRepository``, **no**
``Context``, and performs **no** authorization. It owns the resilience core
(sandbox recovery, JOB polling, sandbox heartbeat, readiness wait, retry/timeout
semantics) and persists run status in its OWN short Units of Work, so it never
holds a pooled DB connection across the multi-second sandbox round-trip.

Transitional note: during the function-module migration the executor accepts
either a ``uow_factory`` (preferred — short UoW per status write) or a bound
``run_repository`` (legacy bound-mode callers). The bound path is removed once
every caller routes through ``FunctionUseCases``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from agentbox_client.apps.function_executor import (
    FunctionExecuteRequest,
    FunctionExecutorClient,
    FunctionInvokeResponse,
    FunctionJobAcceptedResponse,
    RuntimeErrorInfo,
)

from app.core.config import settings
from app.core.domain.events import DomainEvent
from app.core.log.log import get_logger
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionType,
    RunAsWorkload,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunFailedEvent,
)
from app.modules.function.services.function_runtime_command import (
    function_workspace_cwd,
)
from app.modules.workspace.agentbox_retry import (
    CONNECT_PHASE_TRANSPORT_ERRORS,
    RETRYABLE_HTTP_STATUS_CODES,
    RETRYABLE_TRANSPORT_ERRORS,
    retry_on_transient_agentbox_error,
    truncate_message,
)
from app.modules.workspace.services.agentbox_manager import agentbox_sandbox_id

logger = get_logger(__name__)

_API_FUNCTION_TIMEOUT_SECONDS = int(
    os.getenv("LEMMA_API_FUNCTION_TIMEOUT_SECONDS", "120")
)
_JOB_FUNCTION_TIMEOUT_SECONDS = 600

# How long to wait for the in-sandbox function_executor app to become ready
# before posting an execute request (the app starts lazily after the VM is up).
_FUNCTION_EXECUTOR_READY_TIMEOUT_SECONDS = 30.0
# Retry budgets for transient (proxy not-ready / 5xx / connection-refused)
# errors. The execute call gets the full readiness window; per-poll calls get a
# small budget because the outer poll deadline provides the macro retry budget.
_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS = 12
_FUNCTION_POLL_RETRY_MAX_ATTEMPTS = 4
# How often to poll a JOB function's status while it runs. Kept coarse (the run
# is async/background) so we don't hammer the manager proxy + in-sandbox app.
_FUNCTION_POLL_INTERVAL_SECONDS = int(
    os.getenv("LEMMA_FUNCTION_POLL_INTERVAL_SECONDS", "5")
)
# How often to heartbeat the sandbox while a JOB runs. A JOB occupies the
# sandbox through the function_executor app and holds no runtime session, so
# without this the idle reaper deletes the pod mid-run once it exceeds the
# sandbox idle timeout (default 300s). Must stay comfortably below that timeout.
_SANDBOX_HEARTBEAT_INTERVAL_SECONDS = int(
    os.getenv("LEMMA_SANDBOX_HEARTBEAT_INTERVAL_SECONDS", "30")
)
# A function run must execute despite transient/internal sandbox churn -- the pod
# being idle-reaped, evicted, or restarted mid-run, or a manager proxy blip. On a
# recoverable sandbox error we reprovision the sandbox (the next attempt's
# get_session recreates a missing/dead pod) and re-run, bounded by these attempts.
# Only a sandbox that cannot be provisioned (error persists across attempts) is
# allowed to surface as a run failure.
_SANDBOX_RECOVERY_MAX_ATTEMPTS = int(
    os.getenv("LEMMA_SANDBOX_RECOVERY_MAX_ATTEMPTS", "3")
)
_SANDBOX_RECOVERY_BACKOFF_SECONDS = 2.0
_SANDBOX_RECOVERY_MAX_BACKOFF_SECONDS = 10.0
# Manager HTTP statuses meaning "the sandbox/pod is missing or not usable right
# now" (as opposed to a real client error like 400/401/403) -- reprovision+retry.
_RECOVERABLE_SANDBOX_STATUS_CODES = frozenset({404, 409, 500, 502, 503, 504})
# httpx transport failures worth recovering from. Deliberately NOT bare
# OSError/TimeoutError: the poll's own "job did not finish before timeout"
# (a real function timeout) is a builtin TimeoutError and must stay terminal.
_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
)
# A synchronous (API) function execute is NON-IDEMPOTENT: re-running it re-runs
# whatever side effect the function already performed (e.g. creating an Outlook
# draft). Its sandbox-recovery may therefore only re-run on errors that prove the
# request never reached a running app -- the pod is missing/conflicted (404/409)
# or the connection was never established (ConnectError/ConnectTimeout). A 5xx or
# a response-leg transport error (read/remote-protocol/write) is ambiguous (the
# app may have already run), so it must surface as a failure rather than trigger
# a re-run. Mirrors the inner ``CONNECT_PHASE_TRANSPORT_ERRORS`` / 504-drop logic
# in ``_execute_via_function_executor``.
_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES = frozenset({404, 409})
_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS = CONNECT_PHASE_TRANSPORT_ERRORS


class FunctionRunExecutor:
    """Sandbox execution engine for function runs. No repos held, no ctx, no
    authorization — operates on already-resolved ``(function, run)`` entities and
    writes run status in its own short UoWs."""

    _SCHEMA_OUTPUT_MARKER = "__LEMMA_FUNCTION_SCHEMAS__"

    def __init__(
        self,
        *,
        uow_factory=None,
        run_repository=None,
        workspace_service,
        storage_factory,
        function_executor_client_factory=None,
    ):
        self._uow_factory = uow_factory
        self.run_repository = run_repository
        self.workspace_service = workspace_service
        self.storage_factory = storage_factory
        self.function_executor_client_factory = function_executor_client_factory

    # -- Short-UoW run-status writers -------------------------------------
    #
    # With a uow_factory each write runs in its own SHORT UoW (connection
    # released on exit). Without one (legacy bound callers) the bound
    # run_repository is used. The terminal write emits the completion/failure
    # event on the SAME UoW commit so a workflow suspended on the run wakes.

    async def _run_status_update(self, run_id: UUID, **kwargs) -> None:
        """Persist a non-terminal run-status update with no pooled connection
        held across the surrounding sandbox I/O."""
        if self._uow_factory is not None:
            from app.modules.function.infrastructure.repositories import (
                FunctionRunRepository,
            )
            from app.core.infrastructure.events.message_bus import get_message_bus

            async with self._uow_factory() as uow:
                await FunctionRunRepository(
                    uow, message_bus=get_message_bus()
                ).update_run(run_id, **kwargs)
            return
        await self.run_repository.update_run(run_id, **kwargs)

    async def _persist_terminal_run(
        self, function: FunctionEntity, run: FunctionRunEntity
    ) -> FunctionRunEntity:
        """Persist a run's terminal state AND emit its completion/failure event.

        The event wakes any workflow suspended on this function run and feeds the
        function self-projector. Use only for terminal (COMPLETED/FAILED)
        transitions; non-terminal status updates stay on plain ``update_run``.
        """
        run.add_event(self._terminal_run_event(function, run))
        if self._uow_factory is not None:
            from app.modules.function.infrastructure.repositories import (
                FunctionRunRepository,
            )
            from app.core.infrastructure.events.message_bus import get_message_bus

            async with self._uow_factory() as uow:
                return await FunctionRunRepository(
                    uow, message_bus=get_message_bus()
                ).update_run_and_collect(
                    run,
                    status=run.status,
                    output_data=run.output_data,
                    error=run.error,
                    logs=run.logs,
                    completed_at=run.completed_at,
                    workspace_session_id=run.workspace_session_id,
                    workspace_process_id=run.workspace_process_id,
                )
        return await self.run_repository.update_run_and_collect(
            run,
            status=run.status,
            output_data=run.output_data,
            error=run.error,
            logs=run.logs,
            completed_at=run.completed_at,
            workspace_session_id=run.workspace_session_id,
            workspace_process_id=run.workspace_process_id,
        )

    # -- Public surface ----------------------------------------------------

    async def execute(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        user_email: str | None = None,
        timeout_seconds: int,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        if function.type == FunctionType.JOB:
            try:
                return await self._execute_job_run(
                    function=function,
                    run=run,
                    user_email=user_email,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                run.status = FunctionRunStatus.FAILED
                # Clean, user-facing error only; full server detail goes to
                # logger.exception below. Leave run.logs as the container logs
                # (if any) -- never append a server traceback.
                run.error = self._user_facing_execution_error(exc)
                run.completed_at = datetime.now()
                await self._persist_terminal_run(function, run)
                logger.exception("Function job run %s failed during execution", run.id)
                return run

        try:
            await self._execute_api_run(
                function=function,
                run=run,
                user_email=user_email,
                timeout_seconds=timeout_seconds,
                run_as_workload=run_as_workload,
            )
        except Exception as exc:
            run.status = FunctionRunStatus.FAILED
            # Clean, user-facing error only; full server detail goes to
            # logger.exception below. Preserve any container logs already on the
            # run -- never append a server traceback or raw HTTP body.
            run.error = self._user_facing_execution_error(exc)
            logger.exception("Function run %s failed during execution", run.id)

        run.completed_at = datetime.now()
        await self._persist_terminal_run(function, run)
        return run

    async def write_code(self, function_id: UUID, path: str, code: str) -> None:
        """Write a function's source to storage. Storage only — no DB connection.
        Lets the use case keep the storage manager out of its own dependencies."""
        storage = self.storage_factory(function_id)
        await storage.write_file(path, code)

    async def extract_schemas(
        self, user_id: UUID, code: str, code_path: str, pod_id: UUID, function_id: UUID
    ) -> tuple[dict, dict, dict | None]:
        del code_path  # persisted for future validation/logging parity
        session = await self.workspace_service.get_session(
            user_id=user_id,
            pod_id=pod_id,
            session_id=str(function_id),
            initial_cwd=f"tasks/{function_id}",
            close_on_exit=False,
            workload_type="function",
            workload_id=function_id,
        )

        async with session:
            input_model, output_model, _, config_model = self._parse_code_headers(code)
            config_schema_expr = (
                f"{config_model}.model_json_schema()" if config_model else "None"
            )
            schema_extract_code = (
                f"{code}\n\n"
                "import json\n"
                f"print('{self._SCHEMA_OUTPUT_MARKER}' + json.dumps({{'input': {input_model}.model_json_schema(), 'output': {output_model}.model_json_schema(), 'config': {config_schema_expr}}}))\n"
            )
            result = await session.execute_code(schema_extract_code)
            if not result.success:
                raise FunctionValidationError(
                    self._build_execution_error_message(
                        result, stage="schema extraction"
                    ),
                    details=self._build_execution_error_details(
                        result, stage="schema_extraction"
                    ),
                )

            try:
                payload = self._extract_marked_json(
                    result.stdout,
                    self._SCHEMA_OUTPUT_MARKER,
                )
                if not isinstance(payload, dict):
                    raise FunctionValidationError(
                        "Function code ran but did not emit valid schema output.",
                        details={
                            "stage": "schema_extraction",
                            "stdout": result.stdout,
                            "expected_marker": self._SCHEMA_OUTPUT_MARKER,
                        },
                    )
                input_schema = payload.get("input")
                output_schema = payload.get("output")
                config_schema = payload.get("config")
                if not isinstance(input_schema, dict) or not isinstance(
                    output_schema, dict
                ):
                    raise FunctionValidationError(
                        "Function code emitted invalid input or output schema data.",
                        details={
                            "stage": "schema_extraction",
                            "stdout": result.stdout,
                            "parsed_payload": payload,
                        },
                    )
                if config_schema is not None and not isinstance(config_schema, dict):
                    raise FunctionValidationError(
                        "Function code emitted invalid config schema data.",
                        details={
                            "stage": "schema_extraction",
                            "stdout": result.stdout,
                            "parsed_payload": payload,
                        },
                    )
                return input_schema, output_schema, config_schema
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise FunctionValidationError(
                    "Function code emitted invalid JSON schema output.",
                    details={
                        "stage": "schema_extraction",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "parse_error": str(exc),
                    },
                ) from exc

    # -- API + JOB attempts -----------------------------------------------

    async def _execute_api_run(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        user_email: str | None,
        timeout_seconds: int,
        run_as_workload: RunAsWorkload | None = None,
    ) -> FunctionRunEntity:
        assert function.id is not None
        assert run.id is not None

        run.started_at = datetime.now()
        run.status = FunctionRunStatus.RUNNING
        await self._run_status_update(
            run.id,
            status=run.status,
            started_at=run.started_at,
            user_email=user_email,
        )

        # When called from an agent tool, reuse the agent's cached delegation token
        # (keyed by workload_type/workload_id) instead of minting a separate function token.
        effective_workload_type = run_as_workload.workload_type if run_as_workload else "function"
        effective_workload_id = run_as_workload.workload_id if run_as_workload else function.id
        effective_workload_name = run_as_workload.workload_name if run_as_workload else function.name

        async def _attempt() -> FunctionInvokeResponse:
            session = await self.workspace_service.get_session(
                user_id=run.user_id,
                pod_id=function.pod_id,
                session_id=self._api_workspace_session_id(function.id),
                initial_cwd=self._api_workspace_cwd(function),
                close_on_exit=False,
                workload_type=effective_workload_type,
                workload_id=effective_workload_id,
                workload_name=effective_workload_name,
            )
            try:
                run.workspace_session_id = session.session_id
                await self._run_status_update(
                    run.id,
                    workspace_session_id=session.session_id,
                    workspace_process_id=None,
                )
                return await self._execute_via_function_executor(
                    function=function,
                    run=run,
                    session=session,
                    timeout_seconds=timeout_seconds,
                    async_job=False,
                )
            finally:
                close = getattr(session, "close", None)
                if close is not None:
                    await close()

        # A synchronous (API) execute is non-idempotent, so sandbox-recovery may
        # only re-run on "the request provably never ran" errors (pod missing /
        # connect-phase) -- never on an ambiguous 5xx or response-leg transport
        # error that may have already executed the function and run its side
        # effect (the cause of the duplicate-draft storm).
        executor_response = await self._execute_with_sandbox_recovery(
            run=run,
            make_attempt=_attempt,
            recoverable_status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
            recoverable_transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
        )
        self._apply_executor_response_to_run(run, executor_response)

    async def _execute_job_run(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        user_email: str | None,
        timeout_seconds: int,
    ) -> FunctionRunEntity:
        started_at = run.started_at or datetime.now()
        if run.status != FunctionRunStatus.RUNNING:
            run.status = FunctionRunStatus.RUNNING
            run.started_at = started_at

        async def _update_run_status(**kwargs):
            await self._run_status_update(run.id, **kwargs)

        async def _attempt() -> FunctionInvokeResponse:
            session = await self.workspace_service.get_session(
                user_id=run.user_id,
                pod_id=function.pod_id,
                session_id=run.workspace_session_id
                or self._job_workspace_session_id(run.id),
                initial_cwd=function_workspace_cwd(function),
                close_on_exit=False,
                workload_type="function",
                workload_id=function.id,
                workload_name=function.name,
            )
            try:
                await _update_run_status(
                    status=run.status,
                    started_at=run.started_at,
                    user_email=user_email,
                    workspace_session_id=session.session_id,
                    workspace_process_id=None,
                )
                run.workspace_session_id = session.session_id
                async with self._keep_sandbox_alive(session):
                    executor_response = await self._execute_via_function_executor(
                        function=function,
                        run=run,
                        session=session,
                        timeout_seconds=timeout_seconds,
                        async_job=True,
                    )
                    if isinstance(executor_response, FunctionJobAcceptedResponse):
                        executor_response = await self._poll_executor_job(
                            session=session,
                            run_id=run.id,
                            timeout_seconds=timeout_seconds,
                        )
                return executor_response
            finally:
                close = getattr(session, "close", None)
                if close is not None:
                    await close()

        executor_response = await self._execute_with_sandbox_recovery(
            run=run, make_attempt=_attempt
        )
        self._apply_executor_response_to_run(run, executor_response)

        run.completed_at = datetime.now()
        await self._persist_terminal_run(function, run)
        return run

    # -- Resilience helpers ------------------------------------------------

    @staticmethod
    def _is_recoverable_sandbox_error(
        exc: BaseException,
        *,
        status_codes: frozenset[int] = _RECOVERABLE_SANDBOX_STATUS_CODES,
        transport_errors: tuple[type[BaseException], ...] = _RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    ) -> bool:
        """True for errors that mean "the sandbox is internally unavailable right
        now" -- a missing/not-running pod or a manager/transport blip -- as
        opposed to a real function failure (which comes back as a 200 response,
        never an exception) or a genuine function timeout (a builtin TimeoutError
        from the poll, deliberately excluded so it stays terminal).

        ``status_codes``/``transport_errors`` are narrowed by the caller for a
        non-idempotent (synchronous) execute so a request that may have already
        run is not re-run."""
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in status_codes
        return isinstance(exc, transport_errors)

    async def _execute_with_sandbox_recovery(
        self,
        *,
        run: FunctionRunEntity,
        make_attempt,
        recoverable_status_codes: frozenset[int] = _RECOVERABLE_SANDBOX_STATUS_CODES,
        recoverable_transport_errors: tuple[type[BaseException], ...] = _RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    ) -> FunctionInvokeResponse:
        """Run an execution attempt, recovering from transient sandbox failures.

        A function run must execute despite internal sandbox churn: the pod being
        idle-reaped, evicted, or restarted mid-run, or a manager proxy blip. On a
        recoverable sandbox error we reprovision (the next attempt's get_session
        recreates a missing/dead pod) and re-run, bounded by
        ``_SANDBOX_RECOVERY_MAX_ATTEMPTS``. A real function failure comes back as a
        200 response (status ``failed``/``timeout``) -- never an exception -- so it
        is returned and never retried. Only a sandbox that cannot be provisioned
        (the error persists across every attempt) surfaces as a run failure.

        For a non-idempotent (synchronous) execute the caller narrows
        ``recoverable_status_codes``/``recoverable_transport_errors`` to the
        "request provably never ran" set, so an ambiguous failure does not re-run
        the function and duplicate its side effect.
        """
        last_exc: BaseException | None = None
        for attempt in range(1, _SANDBOX_RECOVERY_MAX_ATTEMPTS + 1):
            try:
                return await make_attempt()
            except Exception as exc:
                if (
                    not self._is_recoverable_sandbox_error(
                        exc,
                        status_codes=recoverable_status_codes,
                        transport_errors=recoverable_transport_errors,
                    )
                    or attempt == _SANDBOX_RECOVERY_MAX_ATTEMPTS
                ):
                    raise
                last_exc = exc
                logger.warning(
                    "Function run %s hit a transient sandbox error on attempt "
                    "%s/%s; reprovisioning the sandbox and retrying: %s",
                    run.id,
                    attempt,
                    _SANDBOX_RECOVERY_MAX_ATTEMPTS,
                    exc,
                )
                await asyncio.sleep(
                    min(
                        _SANDBOX_RECOVERY_BACKOFF_SECONDS * attempt,
                        _SANDBOX_RECOVERY_MAX_BACKOFF_SECONDS,
                    )
                )
        # Unreachable: the final attempt returns or re-raises above.
        raise last_exc if last_exc is not None else RuntimeError(
            "sandbox recovery exhausted without result"
        )

    @contextlib.asynccontextmanager
    async def _keep_sandbox_alive(self, session):
        """Heartbeat the session's sandbox so the manager's idle reaper does not
        delete it while a JOB function runs.

        JOB functions occupy the sandbox through the function_executor app and
        never create a runtime session, so nothing else resets the sandbox idle
        clock. Best-effort: heartbeat failures are swallowed and retried on the
        next tick; a genuinely dead sandbox is surfaced by the run's own
        execute/poll retry + deadline logic. No-ops cleanly when the session has
        no manager client (e.g. in tests).
        """
        sandbox_id = getattr(session, "sandbox_id", None)
        client = getattr(session, "client", None)
        heartbeat = getattr(client, "heartbeat_sandbox", None)
        if not sandbox_id or heartbeat is None:
            yield
            return

        async def _loop() -> None:
            # Heartbeat immediately so a reused, near-idle sandbox is kept alive
            # from the first moment of the run -- waiting a full interval first
            # leaves a window where the idle reaper can delete the pod mid-run.
            first = True
            while True:
                if not first:
                    await asyncio.sleep(_SANDBOX_HEARTBEAT_INTERVAL_SECONDS)
                first = False
                try:
                    await heartbeat(sandbox_id)
                except Exception as exc:  # best-effort keepalive
                    logger.debug(
                        "sandbox heartbeat failed sandbox=%s: %s", sandbox_id, exc
                    )

        task = asyncio.create_task(_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _execute_via_function_executor(
        self,
        *,
        function: FunctionEntity,
        run: FunctionRunEntity,
        session,
        timeout_seconds: int,
        async_job: bool,
    ) -> FunctionInvokeResponse | FunctionJobAcceptedResponse:
        assert function.id is not None
        assert run.id is not None
        env_vars = getattr(session, "env_vars", {}) or {}
        lemma_token = env_vars.get("LEMMA_TOKEN")
        if not lemma_token:
            raise FunctionValidationError("Workspace session did not include LEMMA_TOKEN")
        sandbox_id = getattr(session, "sandbox_id", agentbox_sandbox_id(run.user_id))
        client = self._build_function_executor_client(lemma_token)
        try:
            # The in-sandbox function_executor app starts lazily after the VM is
            # RUNNING, so wait for it to be serving before posting, then retry the
            # execute on transient proxy errors as a backstop for the manager's
            # cached-ready race. A 200 response carrying status="failed" is a real
            # function failure and is NOT retried.
            await client.wait_until_ready(
                sandbox_id=sandbox_id,
                timeout_seconds=_FUNCTION_EXECUTOR_READY_TIMEOUT_SECONDS,
            )

            async def _do_execute():
                return await client.execute(
                    sandbox_id=sandbox_id,
                    pod_id=function.pod_id,
                    function_name=function.name,
                    request=FunctionExecuteRequest(
                        run_id=run.id,
                        input_data=run.input_data or {},
                        async_job=async_job,
                        timeout_seconds=timeout_seconds,
                    ),
                )

            # A synchronous execute is non-idempotent: a 504 means the request
            # reached the in-sandbox app and it ran past its budget without
            # responding, so re-sending could run the function again. Drop 504
            # from the retryable set for sync and surface a timeout instead. For
            # the same reason, drop the response-leg transport errors (read /
            # remote-protocol / write / OSError such as "connection reset by
            # peer"): each can fire *after* the function already ran and produced
            # its side effect, so retrying would duplicate it -- the bug behind
            # the Outlook duplicate-draft storm. Only connect-phase errors
            # (ConnectError/ConnectTimeout) are safe, and they still cover the
            # cold-start "app not ready" race. An async_job execute returns
            # immediately, so it keeps the full sets.
            retryable_status_codes = (
                RETRYABLE_HTTP_STATUS_CODES
                if async_job
                else RETRYABLE_HTTP_STATUS_CODES - {504}
            )
            retryable_transport_errors = (
                RETRYABLE_TRANSPORT_ERRORS
                if async_job
                else CONNECT_PHASE_TRANSPORT_ERRORS
            )
            try:
                return await retry_on_transient_agentbox_error(
                    _do_execute,
                    max_attempts=_FUNCTION_EXECUTE_RETRY_MAX_ATTEMPTS,
                    retryable_status_codes=retryable_status_codes,
                    retryable_transport_errors=retryable_transport_errors,
                    on_retry=lambda attempt, message: logger.info(
                        "function_executor execute not ready sandbox=%s run=%s attempt=%s: %s",
                        sandbox_id,
                        run.id,
                        attempt,
                        message,
                    ),
                )
            except httpx.HTTPStatusError as exc:
                if not async_job and exc.response.status_code == 504:
                    logger.warning(
                        "function_executor sync execute timed out at proxy "
                        "sandbox=%s run=%s",
                        sandbox_id,
                        run.id,
                    )
                    return FunctionInvokeResponse(
                        status="timeout",
                        output_data=None,
                        error=RuntimeErrorInfo(
                            name="GatewayTimeout",
                            message=(
                                "Function did not return before the execution "
                                "timeout."
                            ),
                        ),
                        logs=[],
                        code_hash="",
                        duration_ms=0,
                    )
                raise
        finally:
            await client.close()

    async def _poll_executor_job(
        self,
        *,
        session,
        run_id: UUID,
        timeout_seconds: int,
    ) -> FunctionInvokeResponse:
        env_vars = getattr(session, "env_vars", {}) or {}
        lemma_token = env_vars.get("LEMMA_TOKEN")
        if not lemma_token:
            raise FunctionValidationError("Workspace session did not include LEMMA_TOKEN")
        sandbox_id = getattr(session, "sandbox_id", None)
        if not sandbox_id:
            raise FunctionValidationError("Workspace session did not include sandbox_id")
        client = self._build_function_executor_client(lemma_token)
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                # Absorb a transient blip (the outer deadline loop provides the
                # macro retry budget); a real TimeoutError below is not retried.
                status = await retry_on_transient_agentbox_error(
                    lambda: client.get_status(sandbox_id=sandbox_id, run_id=run_id),
                    max_attempts=_FUNCTION_POLL_RETRY_MAX_ATTEMPTS,
                )
                if status.status in {"completed", "failed", "cancelled", "timeout"}:
                    logs = await retry_on_transient_agentbox_error(
                        lambda: client.get_logs(sandbox_id=sandbox_id, run_id=run_id),
                        max_attempts=_FUNCTION_POLL_RETRY_MAX_ATTEMPTS,
                    )
                    mapped_status = {
                        "completed": "completed",
                        "failed": "failed",
                        "cancelled": "cancelled",
                        "timeout": "timeout",
                    }[status.status]
                    return FunctionInvokeResponse(
                        status=mapped_status,
                        output_data=status.output_data,
                        error=status.error,
                        logs=logs.logs,
                        code_hash=status.code_hash or "",
                        duration_ms=status.duration_ms or 0,
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("Function job did not finish before timeout")
                await asyncio.sleep(_FUNCTION_POLL_INTERVAL_SECONDS)
        finally:
            await client.close()

    def _build_function_executor_client(self, lemma_token: str):
        if self.function_executor_client_factory is not None:
            return self.function_executor_client_factory(lemma_token)
        api_key = settings.agentbox_api_key
        if not api_key:
            raise RuntimeError("AGENTBOX_API_KEY is required")
        return FunctionExecutorClient(
            manager_base_url=settings.agentbox_api_url,
            manager_api_key=api_key,
            lemma_token=lemma_token,
            timeout_seconds=300.0,
        )

    def _apply_executor_response_to_run(
        self,
        run: FunctionRunEntity,
        response: FunctionInvokeResponse,
    ) -> None:
        run.logs = "\n".join(
            entry.message for entry in response.logs if entry.message
        ) or None
        if response.status == "completed":
            run.status = FunctionRunStatus.COMPLETED
            run.output_data = response.output_data
            return
        run.status = FunctionRunStatus.FAILED
        if response.error is not None:
            run.error = response.error.message
        else:
            run.error = f"Function executor returned status {response.status}"

    def _terminal_run_event(
        self, function: FunctionEntity, run: FunctionRunEntity
    ) -> DomainEvent:
        if run.status == FunctionRunStatus.COMPLETED:
            return FunctionRunCompletedEvent(
                run_id=run.id,
                function_id=function.id,
                output_data=run.output_data,
                logs=run.logs,
                completed_at=run.completed_at or datetime.now(),
                workspace_session_id=run.workspace_session_id,
                workspace_process_id=run.workspace_process_id,
            )
        return FunctionRunFailedEvent(
            run_id=run.id,
            function_id=function.id,
            error=run.error,
            logs=run.logs,
            completed_at=run.completed_at or datetime.now(),
            workspace_session_id=run.workspace_session_id,
            workspace_process_id=run.workspace_process_id,
        )

    # -- Pure formatting / parsing helpers --------------------------------

    def _build_execution_error_details(self, result: Any, *, stage: str) -> dict[str, Any]:
        details: dict[str, Any] = {"stage": stage}

        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if stdout:
            details["stdout"] = stdout
        if stderr:
            details["stderr"] = stderr

        error = getattr(result, "error", None)
        if error:
            details["error"] = error

        error_in_exec = getattr(result, "error_in_exec", None)
        if error_in_exec:
            details["execution_error"] = error_in_exec

        return details

    def _build_execution_error_message(self, result: Any, *, stage: str) -> str:
        error_in_exec = getattr(result, "error_in_exec", None)
        if isinstance(error_in_exec, dict):
            evalue = error_in_exec.get("evalue")
            ename = error_in_exec.get("ename")
            if ename and evalue:
                return f"Function {stage} failed: {ename}: {evalue}"
            if evalue:
                return f"Function {stage} failed: {evalue}"

        stderr = getattr(result, "stderr", None)
        if isinstance(stderr, str) and stderr.strip():
            first_line = stderr.strip().splitlines()[0]
            return f"Function {stage} failed: {first_line}"

        error = getattr(result, "error", None)
        if isinstance(error, str) and error.strip():
            return f"Function {stage} failed: {error.strip()}"

        return f"Function {stage} failed."

    def _build_execution_logs(self, result: Any) -> str | None:
        parts: list[str] = []
        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if isinstance(stdout, str) and stdout:
            parts.append(stdout)
        if isinstance(stderr, str) and stderr:
            parts.append(stderr)

        error = getattr(result, "error", None)
        if isinstance(error, str) and error:
            parts.append(error)
        elif error:
            parts.append(json.dumps(error, default=str))

        error_in_exec = getattr(result, "error_in_exec", None)
        if error_in_exec:
            parts.append(json.dumps(error_in_exec, default=str))

        return "\n".join(part for part in parts if part) or None

    def _user_facing_execution_error(self, exc: Exception) -> str:
        """A concise, user-facing message for a backend<->sandbox failure.

        ``run.error`` and ``run.logs`` are surfaced to pod authors (and embedded
        in workflow node errors), so they must NOT carry server internals -- raw
        HTTP response bodies, ``str(OSError)`` like ``[Errno 104] Connection reset
        by peer``, or Python tracebacks. The full detail is captured server-side
        via ``logger.exception`` at the call site; here we map the failure class
        to a clean sentence. A real function failure never reaches this path (it
        returns a 200 response whose structured ``error.message`` is used by
        ``_apply_executor_response_to_run``); only backend<->sandbox transport /
        HTTP / timeout failures and our own ``FunctionValidationError`` do.
        """
        if isinstance(exc, FunctionValidationError):
            # Already authored to be user-facing (e.g. schema mismatch).
            return truncate_message(str(exc)) or "Function validation failed."
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            if status_code in (502, 503, 504):
                return "The function sandbox was temporarily unavailable. Please retry."
            return "The function sandbox returned an unexpected error."
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return "The function did not complete before the execution timeout."
        if isinstance(exc, (httpx.TransportError, OSError)):
            return (
                "The function sandbox connection was interrupted; the function "
                "may not have completed."
            )
        return "The function failed to execute due to an internal error."

    def _job_workspace_session_id(self, run_id: UUID) -> str:
        return f"function-run-{run_id}"

    def _api_workspace_session_id(self, function_id: UUID) -> str:
        return f"function-api-{function_id}"

    def _api_workspace_cwd(self, function: FunctionEntity) -> str:
        return function_workspace_cwd(function)

    def _extract_marked_json(self, stdout: str | None, marker: str) -> Any | None:
        if not stdout:
            return None
        for line in reversed(stdout.splitlines()):
            if line.startswith(marker):
                payload = line[len(marker) :].strip()
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("Failed to decode marked JSON for marker %s", marker)
                    return None
        return None

    def _parse_code_headers(self, code: str) -> tuple[str, str, str, str | None]:
        headers: dict[str, str] = {}
        for line in code.splitlines()[:8]:
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("#") or ":" not in stripped:
                break
            key, value = stripped[1:].split(":", 1)
            headers[key.strip()] = value.strip()

        input_model = headers.get("input_type_name")
        output_model = headers.get("output_type_name")
        function_name_in_code = headers.get("function_name")
        if not input_model or not output_model or not function_name_in_code:
            raise FunctionValidationError(
                "Function code must begin with header lines for input type, output type, and function name.",
                details={
                    "expected_header_lines": [
                        "#input_type_name: CreateExpenseInput",
                        "#output_type_name: CreateExpenseResult",
                        "#function_name: run_function",
                        "#config_type_name: ExpenseFunctionConfig  # optional",
                    ]
                },
            )
        return (
            input_model,
            output_model,
            function_name_in_code,
            headers.get("config_type_name") or None,
        )
