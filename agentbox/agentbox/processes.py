from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from agentbox.domain import (
    AgentBoxError,
    ErrorCode,
    PhysicalAllocation,
    ProcessErrorContext,
    ProcessIntent,
    ProcessOutputSnapshot,
    ProcessRef,
    ProcessState,
    RetryDisposition,
    SandboxKey,
    StartProcessRequest,
    TerminalSize,
)
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderProcessPort,
    ProviderProcessStartAmbiguous,
    ProviderProcessStartRejected,
    ProviderProcessStartRequest,
)


class ProcessExecutionService:
    """Durable process intentions around provider I/O with no open DB transaction."""

    def __init__(self, database: StateDatabase, provider: ProviderProcessPort) -> None:
        self._database = database
        self._provider = provider

    async def start(
        self, key: SandboxKey, request: StartProcessRequest
    ) -> tuple[ProcessRef, bool]:
        self._check_deadline(request.deadline_at)
        request_hash = self._request_hash(request)
        async with self._database.uow() as uow:
            intent, created = await uow.repository.reserve_process(
                key,
                operation_id=request.operation_id,
                request_hash=request_hash,
                env_keys=tuple(item.name for item in request.environment),
                cwd=request.cwd,
                tty=request.tty is not None,
                output_limit_bytes=request.output_limit_bytes,
                deadline_at=request.deadline_at,
            )
            allocation = await uow.repository.get_allocation_by_id(intent.allocation_id)
            await uow.commit()
        if allocation is None:  # pragma: no cover - FK invariant
            raise RuntimeError("process allocation disappeared")
        if not created and intent.state != ProcessState.RESERVED:
            return self._ref(intent), False

        async with self._database.uow() as uow:
            dispatch = await uow.repository.mark_process_starting(
                key, request.operation_id
            )
            await uow.commit()
        if not dispatch:
            current = await self.inspect(key, request.operation_id)
            return current, False

        try:
            result = await self._provider.start_process(
                ProviderProcessStartRequest(
                    allocation=self._provider_ref(allocation),
                    process=self._ref(intent),
                    request=request,
                )
            )
        except ProviderProcessStartAmbiguous as exc:
            async with self._database.uow() as uow:
                intent = await uow.repository.mark_process_unknown(
                    key, request.operation_id
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process start outcome is unknown and will be reconciled",
                retry=RetryDisposition.WAIT,
                status_code=202,
                context=ProcessErrorContext(
                    kind="process", operation_id=request.operation_id
                ),
            ) from exc
        except ProviderProcessStartRejected as exc:
            async with self._database.uow() as uow:
                await uow.repository.reset_process_after_rejection(
                    key, request.operation_id
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "provider rejected process start",
                retry=RetryDisposition.SAFE_SAME_OPERATION,
                status_code=503,
            ) from exc

        async with self._database.uow() as uow:
            intent = await uow.repository.acknowledge_process(
                key,
                request.operation_id,
                provider_process_id=result.provider_process_id,
                provider_tag=result.provider_tag,
            )
            await uow.commit()
        return self._ref(intent), created

    async def inspect(self, key: SandboxKey, operation_id: UUID) -> ProcessRef:
        async with self._database.uow() as uow:
            intent = await uow.repository.get_process(key, operation_id)
            await uow.commit()
        if intent is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
                context=ProcessErrorContext(
                    kind="process", operation_id=operation_id
                ),
            )
        return self._ref(intent)

    async def list(self, key: SandboxKey) -> tuple[ProcessRef, ...]:
        async with self._database.uow() as uow:
            intents = await uow.repository.list_processes(key)
            await uow.commit()
        return tuple(self._ref(intent) for intent in intents)

    async def send_input(
        self,
        key: SandboxKey,
        operation_id: UUID,
        data: bytes,
        *,
        deadline_at: datetime,
    ) -> None:
        self._check_deadline(deadline_at)
        intent, allocation = await self._bound_process(key, operation_id)
        await self._provider.send_process_input(
            self._provider_ref(allocation),
            process=self._ref_with_provider_identity(intent),
            data=data,
            deadline_at=deadline_at,
        )

    async def read_output(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        self._check_deadline(deadline_at)
        if after_sequence < 0 or not 0 <= wait_seconds <= 30:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "after_sequence and wait_seconds are out of range",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        intent, allocation = await self._bound_process(key, operation_id)
        snapshot = await self._provider.read_process_output(
            self._provider_ref(allocation),
            process=self._ref_with_provider_identity(intent),
            after_sequence=after_sequence,
            wait_seconds=wait_seconds,
            deadline_at=deadline_at,
        )
        if snapshot.state in {
            ProcessState.SUCCEEDED,
            ProcessState.FAILED,
            ProcessState.CANCELLED,
            ProcessState.TIMED_OUT,
        }:
            async with self._database.uow() as uow:
                completed = await uow.repository.complete_process(
                    key,
                    operation_id,
                    state=snapshot.state,
                    exit_code=snapshot.exit_code,
                )
                await uow.commit()
            # Terminal durable state is a fence. In particular, providers
            # commonly report a signal-killed process as FAILED after AgentBox
            # has explicitly recorded it as CANCELLED. Preserve output chunks,
            # but never let that late provider observation rewrite semantics.
            if (
                completed.state != snapshot.state
                or completed.exit_code != snapshot.exit_code
            ):
                snapshot = ProcessOutputSnapshot(
                    chunks=snapshot.chunks,
                    next_sequence=snapshot.next_sequence,
                    truncated_before_sequence=snapshot.truncated_before_sequence,
                    state=completed.state,
                    exit_code=completed.exit_code,
                )
        return snapshot

    async def resize(
        self,
        key: SandboxKey,
        operation_id: UUID,
        size: TerminalSize,
        *,
        deadline_at: datetime,
    ) -> None:
        self._check_deadline(deadline_at)
        intent, allocation = await self._bound_process(key, operation_id)
        await self._provider.resize_process(
            self._provider_ref(allocation),
            process=self._ref_with_provider_identity(intent),
            size=size,
            deadline_at=deadline_at,
        )

    async def terminate(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> ProcessRef:
        self._check_deadline(deadline_at)
        if not 0 <= grace_seconds <= 30:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "grace_seconds must be in 0..30",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        intent, allocation = await self._bound_process(key, operation_id)
        await self._provider.terminate_process(
            self._provider_ref(allocation),
            process=self._ref_with_provider_identity(intent),
            grace_seconds=grace_seconds,
            deadline_at=deadline_at,
        )
        async with self._database.uow() as uow:
            intent = await uow.repository.mark_process_terminated(key, operation_id)
            await uow.commit()
        return self._ref(intent)

    async def _bound_process(
        self, key: SandboxKey, operation_id: UUID
    ) -> tuple[ProcessIntent, PhysicalAllocation]:
        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(key)
            intent = await uow.repository.get_process(key, operation_id)
            allocation = (
                await uow.repository.get_allocation_by_id(intent.allocation_id)
                if intent is not None
                else None
            )
            await uow.commit()
        if logical is None or intent is None or allocation is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process or its allocation does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
                context=ProcessErrorContext(
                    kind="process", operation_id=operation_id
                ),
            )
        if (
            logical.current_allocation_id != intent.allocation_id
            or logical.allocation_epoch != intent.allocation_epoch
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "process belongs to a stale sandbox allocation",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        return intent, allocation

    @staticmethod
    def _provider_process_id(intent: ProcessIntent) -> str:
        if intent.provider_process_id is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process has no acknowledged provider identity",
                retry=RetryDisposition.WAIT,
                status_code=409,
                context=ProcessErrorContext(
                    kind="process", operation_id=intent.operation_id
                ),
            )
        return intent.provider_process_id

    @staticmethod
    def _ref_with_provider_identity(intent: ProcessIntent) -> ProcessRef:
        ProcessExecutionService._provider_process_id(intent)
        return ProcessExecutionService._ref(intent)

    @staticmethod
    def _ref(intent: ProcessIntent) -> ProcessRef:
        return ProcessRef(
            key=intent.key,
            operation_id=intent.operation_id,
            allocation_id=intent.allocation_id,
            allocation_epoch=intent.allocation_epoch,
            provider_process_id=intent.provider_process_id,
            state=intent.state,
            cwd=intent.cwd,
            tty=intent.tty,
            output_limit_bytes=intent.output_limit_bytes,
            deadline_at=intent.deadline_at,
            started_at=intent.started_at,
            completed_at=intent.completed_at,
            exit_code=intent.exit_code,
        )

    @staticmethod
    def _provider_ref(allocation: PhysicalAllocation) -> ProviderAllocationRef:
        if allocation.provider_id is None:
            raise AgentBoxError(
                ErrorCode.PROVISIONING,
                "sandbox provider allocation is not ready",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        return ProviderAllocationRef(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
            allocation_id=allocation.allocation_id,
            allocation_token=allocation.allocation_token,
            key=allocation.key,
        )

    @staticmethod
    def _request_hash(request: StartProcessRequest) -> str:
        command = (
            f"shell:{request.shell_command}"
            if request.shell_command is not None
            else "argv:" + "\x1e".join(request.argv or ())
        )
        tty = (
            f"{request.tty.cols}x{request.tty.rows}"
            if request.tty is not None
            else "none"
        )
        canonical = "\x1f".join(
            (
                str(request.operation_id),
                command,
                request.cwd,
                "\x1e".join(item.name for item in request.environment),
                tty,
                str(request.output_limit_bytes),
                request.deadline_at.isoformat(),
            )
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _check_deadline(deadline_at: datetime) -> None:
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "deadline_at must include a timezone",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        if deadline_at <= datetime.now(timezone.utc):
            raise AgentBoxError(
                ErrorCode.DEADLINE_EXCEEDED,
                "process deadline has elapsed",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=408,
            )
