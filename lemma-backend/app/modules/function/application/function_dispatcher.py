"""Durable function dispatcher over AgentBox's generic sandbox/process API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import UUID, uuid7

import httpx

from agentbox_client import (
    AdmissionClass,
    AgentBoxApiError,
    AgentBoxClient,
    EnvironmentVariable,
    ProcessRef,
    ProcessState,
    ProfileRef,
    RetryDisposition,
    WorkloadKind,
)

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.redaction import redact_text
from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.domain.entities import (
    FunctionExecutionClaim,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionType,
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
_TERMINAL_PROCESS_STATES = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}


AgentBoxClientFactory = Callable[[], AgentBoxClient]


class FunctionDispatcher:
    """Reserve in Postgres, execute externally, then persist in a new UoW.

    No method keeps a Unit of Work alive across an AgentBox request, sleep,
    output wait, identity call, or object-store call.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        credential_signer: FunctionAttemptCredentialSigner,
        agentbox_client_factory: AgentBoxClientFactory,
        worker_id: str | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._signer = credential_signer
        self._agentbox_client_factory = agentbox_client_factory
        self._worker_id = worker_id or f"dispatcher-{uuid7()}"
        self._profile = ProfileRef(
            name=settings.agentbox_function_profile_name,
            digest=settings.agentbox_function_profile_digest,
        )

    async def execute(self, run_id: UUID) -> FunctionRunEntity:
        claim = await self._wait_for_claim(run_id)
        if isinstance(claim, FunctionRunEntity):
            return claim

        client = self._agentbox_client_factory()
        try:
            await self._ensure_sandbox(client, claim)
            process = await self._start_process(client, claim)
            async with self._uow_factory() as uow:
                await FunctionExecutionRepository(
                    uow, self._signer
                ).mark_process_started(
                    claim.attempt_id,
                    provider_process_id=None,
                )
            await self._deliver_ticket(client, claim)
            return await self._wait_for_terminal(client, claim, process)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                await self._best_effort_terminate(client, claim)
                raise
            unknown = self._is_unknown_outcome(exc)
            message = self._execution_error(exc, unknown=unknown)
            async with self._uow_factory() as uow:
                failed = await FunctionExecutionRepository(
                    uow, self._signer
                ).fail_dispatch(claim, error=message, unknown=unknown)
            if failed is None:
                return await self._load_run(run_id)
            return failed
        finally:
            await client.close()

    async def cancel(self, run_id: UUID) -> FunctionRunEntity:
        claim = await self._active_claim(run_id)
        if claim is None:
            return await self._load_run(run_id)
        client = self._agentbox_client_factory()
        try:
            await client.terminate_process(
                WorkloadKind.FUNCTION,
                claim.pod_id,
                claim.operation_id,
                deadline_at=self._control_deadline(claim.deadline_at),
                grace_seconds=2,
            )
        finally:
            await client.close()
        async with self._uow_factory() as uow:
            run = await FunctionExecutionRepository(
                uow, self._signer
            ).fail_dispatch(
                claim,
                error="Function execution was cancelled",
                unknown=False,
            )
        return run or await self._load_run(run_id)

    async def _wait_for_claim(
        self, run_id: UUID
    ) -> FunctionExecutionClaim | FunctionRunEntity:
        while True:
            async with self._uow_factory() as uow:
                run = await FunctionRunRepository(uow).get_run(run_id)
                if run is None:
                    raise LookupError(f"function run {run_id} does not exist")
                if run.status in _TERMINAL_RUN_STATES:
                    return run
                deadline_at = run.deadline_at
                claim = await FunctionExecutionRepository(
                    uow, self._signer
                ).claim_run(
                    run_id,
                    worker_id=self._worker_id,
                    total_units=settings.function_execution_units_per_pod,
                    api_reserved_units=(
                        settings.function_execution_api_reserved_units
                    ),
                    lease_seconds=settings.function_execution_claim_lease_seconds,
                )
            if claim is not None:
                return claim
            if deadline_at is None or self._now() >= deadline_at:
                async with self._uow_factory() as uow:
                    failed = await FunctionExecutionRepository(
                        uow, self._signer
                    ).fail_queued(
                        run_id, error="Function execution deadline exceeded in queue"
                    )
                return failed or await self._load_run(run_id)
            await asyncio.sleep(0.05)

    async def _active_claim(self, run_id: UUID) -> FunctionExecutionClaim | None:
        async with self._uow_factory() as uow:
            return await FunctionExecutionRepository(
                uow, self._signer
            ).claim_run(
                run_id,
                worker_id=self._worker_id,
                total_units=settings.function_execution_units_per_pod,
                api_reserved_units=settings.function_execution_api_reserved_units,
                lease_seconds=settings.function_execution_claim_lease_seconds,
            )

    async def _ensure_sandbox(
        self, client: AgentBoxClient, claim: FunctionExecutionClaim
    ) -> None:
        while self._now() < claim.deadline_at:
            try:
                handle = await client.ensure_sandbox(
                    WorkloadKind.FUNCTION,
                    claim.pod_id,
                    profile=self._profile,
                    admission_class=(
                        AdmissionClass.LATENCY
                        if claim.function_type == FunctionType.API
                        else AdmissionClass.BATCH
                    ),
                    deadline_at=claim.deadline_at,
                )
            except AgentBoxApiError as exc:
                if exc.retry not in {
                    RetryDisposition.WAIT,
                    RetryDisposition.SAFE_SAME_OPERATION,
                }:
                    raise
                await self._wait_retry(exc.retry_after_ms, claim.deadline_at)
                continue
            except httpx.TransportError:
                # Ensuring a logical key is idempotent; AgentBox owns exact-create
                # reconciliation through its durable allocation token.
                await self._wait_retry(None, claim.deadline_at)
                continue
            if handle.ready:
                return
            await self._wait_retry(handle.retry_after_ms, claim.deadline_at)
        raise TimeoutError("function sandbox was not ready before the deadline")

    async def _start_process(
        self, client: AgentBoxClient, claim: FunctionExecutionClaim
    ) -> ProcessRef:
        environment = (
            EnvironmentVariable(
                name="LEMMA_FUNCTION_GATEWAY_URL",
                value=self._runtime_gateway_url(),
            ),
        )
        while self._now() < claim.deadline_at:
            try:
                process = await client.start_process(
                    WorkloadKind.FUNCTION,
                    claim.pod_id,
                    operation_id=claim.operation_id,
                    deadline_at=claim.deadline_at,
                    cwd="/tmp",
                    argv=("lemma-function-runtime", "execute"),
                    environment=environment,
                    output_limit_bytes=8 * 1024 * 1024,
                )
            except AgentBoxApiError as exc:
                if exc.retry not in {
                    RetryDisposition.WAIT,
                    RetryDisposition.SAFE_SAME_OPERATION,
                }:
                    raise
                await self._wait_retry(exc.retry_after_ms, claim.deadline_at)
                continue
            except httpx.TransportError:
                # Retry only the same operation_id. AgentBox's process intent
                # makes this exact retry safe even if the response was lost.
                await self._wait_retry(None, claim.deadline_at)
                continue
            if process.state == ProcessState.UNKNOWN:
                await self._wait_retry(100, claim.deadline_at)
                continue
            return process
        raise UnknownFunctionDispatch("process start outcome remained unknown")

    async def _deliver_ticket(
        self, client: AgentBoxClient, claim: FunctionExecutionClaim
    ) -> None:
        try:
            await client.send_process_input(
                WorkloadKind.FUNCTION,
                claim.pod_id,
                claim.operation_id,
                f"{claim.ticket}\n".encode(),
                deadline_at=claim.deadline_at,
            )
        except httpx.TransportError:
            # Arbitrary stdin writes cannot be made exactly-once across a lost
            # response. Do not blindly write again; monitor this exact process.
            logger.warning(
                "function.dispatcher.ticket_delivery.unknown",
                run_id=str(claim.run_id),
                attempt_id=str(claim.attempt_id),
            )
            return
        except AgentBoxApiError as exc:
            if exc.retry == RetryDisposition.WAIT:
                raise UnknownFunctionDispatch(
                    "ticket delivery could not be confirmed"
                ) from exc
            raise

    async def _wait_for_terminal(
        self,
        client: AgentBoxClient,
        claim: FunctionExecutionClaim,
        process: ProcessRef,
    ) -> FunctionRunEntity:
        sequence = 0
        process_state = process.state
        terminal_seen_at: datetime | None = None
        while self._now() < claim.deadline_at:
            run = await self._load_run(claim.run_id)
            if run.status in _TERMINAL_RUN_STATES:
                return run
            if process_state in _TERMINAL_PROCESS_STATES:
                terminal_seen_at = terminal_seen_at or self._now()
                if self._now() - terminal_seen_at >= timedelta(seconds=2):
                    raise UnknownFunctionDispatch(
                        "function process exited without a terminal callback"
                    )
                await asyncio.sleep(0.05)
                continue
            try:
                snapshot = await client.read_process_output(
                    WorkloadKind.FUNCTION,
                    claim.pod_id,
                    claim.operation_id,
                    deadline_at=claim.deadline_at,
                    after_sequence=sequence,
                    wait_seconds=1,
                )
                sequence = snapshot.next_sequence
                process_state = snapshot.state
            except AgentBoxApiError as exc:
                if exc.retry == RetryDisposition.DO_NOT_RETRY:
                    raise UnknownFunctionDispatch(
                        "function process could not be inspected"
                    ) from exc
                await self._wait_retry(exc.retry_after_ms, claim.deadline_at)
            except httpx.TransportError:
                await self._wait_retry(None, claim.deadline_at)

        await self._best_effort_terminate(client, claim)
        raise TimeoutError("function execution exceeded its deadline")

    async def _best_effort_terminate(
        self, client: AgentBoxClient, claim: FunctionExecutionClaim
    ) -> None:
        try:
            await client.terminate_process(
                WorkloadKind.FUNCTION,
                claim.pod_id,
                claim.operation_id,
                deadline_at=self._control_deadline(claim.deadline_at),
                grace_seconds=2,
            )
        except Exception:
            logger.warning(
                "function.dispatcher.process_termination.failed",
                run_id=str(claim.run_id),
                attempt_id=str(claim.attempt_id),
            )

    async def _load_run(self, run_id: UUID) -> FunctionRunEntity:
        async with self._uow_factory() as uow:
            run = await FunctionRunRepository(uow).get_run(run_id)
        if run is None:
            raise LookupError(f"function run {run_id} does not exist")
        return run

    @staticmethod
    async def _wait_retry(
        retry_after_ms: int | None, deadline_at: datetime
    ) -> None:
        remaining = (deadline_at - FunctionDispatcher._now()).total_seconds()
        if remaining <= 0:
            return
        delay = max(0.05, (retry_after_ms or 200) / 1000)
        await asyncio.sleep(min(delay, remaining))

    @staticmethod
    def _runtime_gateway_url() -> str:
        configured = settings.function_runtime_gateway_url or settings.api_url
        parsed = urlparse(configured)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return configured.rstrip("/")
        host = "host.docker.internal"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return f"{parsed.scheme or 'http'}://{host}"

    @staticmethod
    def _control_deadline(execution_deadline: datetime) -> datetime:
        return max(execution_deadline, FunctionDispatcher._now() + timedelta(seconds=5))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _is_unknown_outcome(exc: BaseException) -> bool:
        if isinstance(exc, UnknownFunctionDispatch):
            return True
        if isinstance(exc, AgentBoxApiError):
            return exc.code in {"UNKNOWN_DISPATCH", "AMBIGUOUS_CREATE"}
        return False

    @staticmethod
    def _execution_error(exc: BaseException, *, unknown: bool) -> str:
        if unknown:
            return (
                "Function execution outcome is unknown; the attempt was not replayed"
            )
        if isinstance(exc, TimeoutError):
            return "Function execution deadline exceeded"
        if isinstance(exc, AgentBoxApiError):
            return f"Function sandbox error ({redact_text(exc.code)})"
        return "Function execution failed"


class UnknownFunctionDispatch(RuntimeError):
    """The attempt may have crossed an external side-effect boundary."""
