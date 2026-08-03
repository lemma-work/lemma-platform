from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID

from agentbox.domain import (
    AgentBoxError,
    AllocationState,
    ErrorCode,
    PhysicalAllocation,
    ProcessErrorContext,
    ProcessOutputSnapshot,
    ProcessRef,
    ProcessState,
    RetryDisposition,
    SandboxKey,
    StartProcessRequest,
    TerminalSize,
)
from agentbox.observability import create_inherited_task, get_logger
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderProcessMissing,
    ProviderProcessPort,
    ProviderProcessStartAmbiguous,
    ProviderProcessStartRejected,
    ProviderProcessStartRequest,
)


logger = get_logger(__name__)

_TERMINAL_STATES = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}


@dataclass(slots=True)
class _ProcessRecord:
    request_hash: str
    ref: ProcessRef
    # When this manager first stored the record. `ref.started_at` is optional
    # and provider-reported, so it cannot bound a record's lifetime on its own.
    stored_at: datetime


class ProcessExecutionService:
    """Epoch-fenced process routing without global execution-history storage.

    Process state belongs to the allocation that executes it. The manager keeps
    only a bounded routing cache. Losing that cache makes the operation
    unavailable; it never makes an old PID valid in a new allocation.
    """

    def __init__(
        self,
        database: StateDatabase,
        provider: ProviderProcessPort,
        *,
        max_records: int = 2048,
        max_records_per_sandbox: int | None = None,
        max_process_lifetime_seconds: float = 12 * 60 * 60,
    ) -> None:
        if max_records < 1:
            raise ValueError("process routing cache must retain at least one record")
        # Derived from the total unless chosen explicitly, so a caller that only
        # sizes the aggregate still gets per-sandbox fairness rather than a
        # per-sandbox cap it never asked for and cannot satisfy.
        if max_records_per_sandbox is None:
            max_records_per_sandbox = min(64, max_records)
        if not 1 <= max_records_per_sandbox <= max_records:
            raise ValueError(
                "per-sandbox process capacity must be between 1 and the total"
            )
        if max_process_lifetime_seconds <= 0:
            raise ValueError("process lifetime bound must be positive")
        self._database = database
        self._provider = provider
        self._max_records = max_records
        self._max_records_per_sandbox = max_records_per_sandbox
        self._max_process_lifetime = timedelta(seconds=max_process_lifetime_seconds)
        self._records: OrderedDict[
            tuple[str, UUID, UUID], _ProcessRecord
        ] = OrderedDict()
        self._inflight: dict[
            tuple[str, UUID, UUID], tuple[str, asyncio.Task[ProcessRef]]
        ] = {}
        self._lock = asyncio.Lock()

    async def start(
        self, key: SandboxKey, request: StartProcessRequest
    ) -> tuple[ProcessRef, bool]:
        self._check_deadline(request.deadline_at)
        record_key = self._record_key(key, request.operation_id)
        request_hash = self._request_hash(request)
        async with self._lock:
            self._prune_expired_locked(datetime.now(timezone.utc))
            existing = self._records.get(record_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise self._operation_conflict(request.operation_id)
                if existing.ref.state == ProcessState.UNKNOWN:
                    raise self._outcome_unknown(request.operation_id)
                self._records.move_to_end(record_key)
                return existing.ref, False
            in_flight = self._inflight.get(record_key)
            created = in_flight is None
            if in_flight is not None:
                in_flight_hash, task = in_flight
                if in_flight_hash != request_hash:
                    raise self._operation_conflict(request.operation_id)
            else:
                # Capacity is charged per sandbox first. A process-global limit
                # alone let one busy user consume the whole manager's routing
                # capacity and 429 everybody else, because records are retained
                # until their deadline rather than evicted.
                if self._sandbox_load_locked(key) >= self._max_records_per_sandbox:
                    raise AgentBoxError(
                        ErrorCode.CAPACITY_EXHAUSTED,
                        "this sandbox has too many concurrent processes",
                        retry=RetryDisposition.WAIT,
                        status_code=429,
                        retry_after_ms=1000,
                    )
                if len(self._records) + len(self._inflight) >= self._max_records:
                    raise AgentBoxError(
                        ErrorCode.CAPACITY_EXHAUSTED,
                        "manager process routing capacity is temporarily full",
                        retry=RetryDisposition.WAIT,
                        status_code=429,
                        retry_after_ms=1000,
                    )
                task = create_inherited_task(
                    self._start_new(key, request, request_hash),
                    name=f"agentbox-process-start:{request.operation_id}",
                )
                self._inflight[record_key] = (request_hash, task)
                def clear(completed: asyncio.Task[ProcessRef]) -> None:
                    current = self._inflight.get(record_key)
                    if current is not None and current[1] is completed:
                        self._inflight.pop(record_key, None)

                task.add_done_callback(clear)
        return await asyncio.shield(task), created

    async def inspect(self, key: SandboxKey, operation_id: UUID) -> ProcessRef:
        async with self._lock:
            record = self._records.get(self._record_key(key, operation_id))
            if record is not None:
                return record.ref
        raise self._missing(operation_id)

    async def list(self, key: SandboxKey) -> tuple[ProcessRef, ...]:
        prefix = (key.workload_kind.value, key.logical_id)
        async with self._lock:
            return tuple(
                record.ref
                for record_key, record in self._records.items()
                if record_key[:2] == prefix
            )

    async def send_input(
        self,
        key: SandboxKey,
        operation_id: UUID,
        data: bytes,
        *,
        deadline_at: datetime,
    ) -> None:
        self._check_deadline(deadline_at)
        record, allocation = await self._bound_process(key, operation_id)
        if record.ref.state in _TERMINAL_STATES:
            raise self._not_running(operation_id)
        try:
            await self._provider.send_process_input(
                self._provider_ref(allocation),
                process=record.ref,
                data=data,
                deadline_at=deadline_at,
            )
        except ProviderProcessMissing as exc:
            await self._terminalize(
                key, operation_id, state=ProcessState.FAILED, exit_code=None
            )
            raise self._not_running(operation_id) from exc

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
        record, allocation = await self._bound_process(key, operation_id)
        try:
            snapshot = await self._provider.read_process_output(
                self._provider_ref(allocation),
                process=record.ref,
                after_sequence=after_sequence,
                wait_seconds=wait_seconds,
                deadline_at=deadline_at,
            )
        except ProviderProcessMissing as exc:
            await self._terminalize(
                key, operation_id, state=ProcessState.FAILED, exit_code=None
            )
            raise self._not_running(operation_id) from exc
        if snapshot.state in _TERMINAL_STATES:
            completed = await self._terminalize(
                key,
                operation_id,
                state=snapshot.state,
                exit_code=snapshot.exit_code,
            )
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
        record, allocation = await self._bound_process(key, operation_id)
        if record.ref.state in _TERMINAL_STATES:
            raise self._not_running(operation_id)
        try:
            await self._provider.resize_process(
                self._provider_ref(allocation),
                process=record.ref,
                size=size,
                deadline_at=deadline_at,
            )
        except ProviderProcessMissing as exc:
            await self._terminalize(
                key, operation_id, state=ProcessState.FAILED, exit_code=None
            )
            raise self._not_running(operation_id) from exc

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
        record, allocation = await self._bound_process(key, operation_id)
        if record.ref.state not in _TERMINAL_STATES:
            try:
                await self._provider.terminate_process(
                    self._provider_ref(allocation),
                    process=record.ref,
                    grace_seconds=grace_seconds,
                    deadline_at=deadline_at,
                )
            except ProviderProcessMissing:
                # Already absent is the desired result of termination; still
                # terminalize the bounded manager record below.
                pass
        return await self._terminalize(
            key,
            operation_id,
            state=ProcessState.CANCELLED,
            exit_code=record.ref.exit_code,
        )

    async def _start_new(
        self,
        key: SandboxKey,
        request: StartProcessRequest,
        request_hash: str,
    ) -> ProcessRef:
        allocation = await self._lease_allocation(key, request.deadline_at)
        pending = ProcessRef(
            key=key,
            operation_id=request.operation_id,
            allocation_id=allocation.allocation_id,
            allocation_epoch=allocation.allocation_epoch or 0,
            provider_process_id=None,
            state=ProcessState.STARTING,
            cwd=request.cwd,
            tty=request.tty is not None,
            output_limit_bytes=request.output_limit_bytes,
            deadline_at=request.deadline_at,
            started_at=None,
            completed_at=None,
            exit_code=None,
        )
        try:
            result = await self._provider.start_process(
                ProviderProcessStartRequest(
                    allocation=self._provider_ref(allocation),
                    process=pending,
                    request=request,
                )
            )
        except ProviderProcessStartAmbiguous as exc:
            unknown = ProcessRef(
                key=pending.key,
                operation_id=pending.operation_id,
                allocation_id=pending.allocation_id,
                allocation_epoch=pending.allocation_epoch,
                provider_process_id=None,
                state=ProcessState.UNKNOWN,
                cwd=pending.cwd,
                tty=pending.tty,
                output_limit_bytes=pending.output_limit_bytes,
                deadline_at=pending.deadline_at,
                started_at=None,
                completed_at=None,
                exit_code=None,
            )
            await self._store(key, request_hash, unknown)
            raise self._outcome_unknown(request.operation_id) from exc
        except ProviderProcessStartRejected as exc:
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "provider rejected process start before execution",
                retry=RetryDisposition.SAFE_SAME_OPERATION,
                status_code=503,
            ) from exc
        ref = ProcessRef(
            key=key,
            operation_id=request.operation_id,
            allocation_id=allocation.allocation_id,
            allocation_epoch=allocation.allocation_epoch or 0,
            provider_process_id=result.provider_process_id,
            state=ProcessState.RUNNING,
            cwd=request.cwd,
            tty=request.tty is not None,
            output_limit_bytes=request.output_limit_bytes,
            deadline_at=request.deadline_at,
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            exit_code=None,
        )
        await self._store(key, request_hash, ref)
        return ref

    async def _bound_process(
        self, key: SandboxKey, operation_id: UUID
    ) -> tuple[_ProcessRecord, PhysicalAllocation]:
        async with self._lock:
            record = self._records.get(self._record_key(key, operation_id))
        if record is None:
            raise self._missing(operation_id)
        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(key)
            allocation = await uow.repository.get_allocation_by_id(
                record.ref.allocation_id
            )
            await uow.commit()
        if logical is None or allocation is None:
            raise self._missing(operation_id)
        if (
            logical.current_allocation_id != record.ref.allocation_id
            or logical.allocation_epoch != record.ref.allocation_epoch
            or allocation.state != AllocationState.ACTIVE
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "process belongs to a stale sandbox allocation",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        return record, allocation

    async def _lease_allocation(
        self, key: SandboxKey, deadline_at: datetime
    ) -> PhysicalAllocation:
        async with self._database.uow() as uow:
            await uow.repository.protect_activity(key, until=deadline_at)
            allocation = await uow.repository.current_allocation(key)
            await uow.commit()
        if (
            allocation is None
            or allocation.provider_id is None
            or allocation.allocation_epoch is None
            or allocation.state != AllocationState.ACTIVE
        ):
            raise AgentBoxError(
                ErrorCode.PROVISIONING,
                "sandbox allocation is not ready for processes",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        return allocation

    async def _store(
        self, key: SandboxKey, request_hash: str, ref: ProcessRef
    ) -> None:
        async with self._lock:
            record_key = self._record_key(key, ref.operation_id)
            existing = self._records.get(record_key)
            self._records[record_key] = _ProcessRecord(
                request_hash=request_hash,
                ref=ref,
                stored_at=(
                    existing.stored_at
                    if existing is not None
                    else datetime.now(timezone.utc)
                ),
            )
            self._records.move_to_end(record_key)

    def _sandbox_load_locked(self, key: SandboxKey) -> int:
        prefix = (key.workload_kind.value, key.logical_id)
        return sum(
            1 for record_key in self._records if record_key[:2] == prefix
        ) + sum(1 for record_key in self._inflight if record_key[:2] == prefix)

    def _prune_expired_locked(self, now: datetime) -> None:
        """Drop records that can no longer be addressed or replayed.

        A record's deadline is the deadline of the call that *started* it, which
        for a backgrounded process is far shorter than the process itself. That
        deadline bounds the idempotency window, not the process, so a record is
        only dropped once its process is terminal. Otherwise a dev server
        started behind a 60-second tool call would become unreachable, and the
        activity lease that keeps its sandbox awake would end with it.

        Non-terminal records are still bounded, by a maximum lifetime, so a
        process whose completion was never observed cannot pin routing capacity
        or its sandbox forever.
        """

        expired = [
            record_key
            for record_key, record in self._records.items()
            if (
                record.ref.deadline_at <= now
                and record.ref.state in _TERMINAL_STATES
            )
            or record.stored_at + self._max_process_lifetime <= now
        ]
        for record_key in expired:
            self._records.pop(record_key, None)

    async def renew_process_leases(self, *, lease_seconds: float) -> int:
        """Treat a running process as activity so idle cleanup waits for it.

        Activity protection is otherwise taken only for the duration of the
        operation that starts a process, so a background build or dev server
        was released - and killed by quiesce - at the idle threshold after the
        agent's last poll. This does not promise a process survives a pause;
        under the workspace model it never does. It only stops AgentBox pausing
        a sandbox out from under work that is still running.
        """

        now = datetime.now(timezone.utc)
        async with self._lock:
            self._prune_expired_locked(now)
            keys = {
                record.ref.key
                for record in self._records.values()
                if record.ref.state not in _TERMINAL_STATES
            }
        renewed = 0
        until = now + timedelta(seconds=lease_seconds)
        for key in keys:
            try:
                async with self._database.uow() as uow:
                    await uow.repository.protect_activity(key, until=until)
                    await uow.commit()
            except AgentBoxError:
                # The sandbox may already be quiescing, replaced, or gone. Its
                # records become unusable with it and are pruned by lifetime.
                continue
            renewed += 1
        return renewed

    async def _terminalize(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        state: ProcessState,
        exit_code: int | None,
    ) -> ProcessRef:
        record_key = self._record_key(key, operation_id)
        async with self._lock:
            record = self._records.get(record_key)
            if record is None:
                raise self._missing(operation_id)
            if record.ref.state in _TERMINAL_STATES:
                return record.ref
            current = record.ref
            record.ref = ProcessRef(
                key=current.key,
                operation_id=current.operation_id,
                allocation_id=current.allocation_id,
                allocation_epoch=current.allocation_epoch,
                provider_process_id=current.provider_process_id,
                state=state,
                cwd=current.cwd,
                tty=current.tty,
                output_limit_bytes=current.output_limit_bytes,
                deadline_at=current.deadline_at,
                started_at=current.started_at,
                completed_at=datetime.now(timezone.utc),
                exit_code=exit_code,
            )
            return record.ref

    @staticmethod
    def _record_key(key: SandboxKey, operation_id: UUID) -> tuple[str, UUID, UUID]:
        return key.workload_kind.value, key.logical_id, operation_id

    @staticmethod
    def _provider_ref(allocation: PhysicalAllocation) -> ProviderAllocationRef:
        assert allocation.provider_id is not None
        return ProviderAllocationRef(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
            allocation_id=allocation.allocation_id,
            allocation_token=allocation.allocation_token,
            key=allocation.key,
            resource_generation=allocation.resource_generation,
        )

    @staticmethod
    def _request_hash(request: StartProcessRequest) -> str:
        command = (
            f"shell:{request.shell_command}"
            if request.shell_command is not None
            else "argv:" + "\x1e".join(request.argv or ())
        )
        canonical = "\x1f".join(
            (
                str(request.operation_id),
                command,
                request.cwd,
                hashlib.sha256(
                    "\x1e".join(
                        f"{item.name}\x1d{item.value}"
                        for item in request.environment
                    ).encode()
                ).hexdigest(),
                str(request.tty),
                str(request.output_limit_bytes),
                request.deadline_at.isoformat(),
                (
                    hashlib.sha256(request.initial_input).hexdigest()
                    if request.initial_input is not None
                    else "none"
                ),
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

    @staticmethod
    def _missing(operation_id: UUID) -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.PROCESS_NOT_RUNNING,
            "process handle is no longer available in this manager incarnation",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=410,
            context=ProcessErrorContext(
                kind="process", operation_id=operation_id
            ),
        )

    @staticmethod
    def _not_running(operation_id: UUID) -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.PROCESS_NOT_RUNNING,
            "process is no longer running; do not replay stdin",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=410,
            context=ProcessErrorContext(
                kind="process", operation_id=operation_id
            ),
        )

    @staticmethod
    def _operation_conflict(operation_id: UUID) -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.OPERATION_CONFLICT,
            "operation ID was reused with a different request",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=409,
            context=ProcessErrorContext(
                kind="process", operation_id=operation_id
            ),
        )

    @staticmethod
    def _outcome_unknown(operation_id: UUID) -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.UNKNOWN_DISPATCH,
            "process start outcome was lost; start a new operation if needed",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=409,
            context=ProcessErrorContext(
                kind="process", operation_id=operation_id
            ),
        )


async def process_lease_loop(
    service: ProcessExecutionService,
    *,
    interval_seconds: float,
    lease_seconds: float,
) -> None:
    """Keep sandboxes with running processes out of idle cleanup.

    The lease is deliberately short and renewed, so a manager that dies cannot
    leave a sandbox pinned awake: the protection simply expires.
    """

    while True:
        try:
            await service.renew_process_leases(lease_seconds=lease_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "agentbox.process_lease.failed",
                exc_info=True,
            )
        await asyncio.sleep(interval_seconds)
