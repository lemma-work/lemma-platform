from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID

from agentbox.domain import (
    AgentBoxError,
    AllocationState,
    CreatePythonSessionRequest,
    ErrorCode,
    ExecutePythonRequest,
    PhysicalAllocation,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    PythonSessionState,
    RetryDisposition,
    SandboxKey,
)
from agentbox.observability import create_inherited_task
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderPythonExecutionAmbiguous,
    ProviderPythonExecutionRejected,
    ProviderPythonSessionCreateAmbiguous,
    ProviderPythonSessionCreateRejected,
    ProviderPythonSessionPort,
)


@dataclass(slots=True)
class _ExecutionRecord:
    request_hash: str
    result: PythonResult
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class _SessionTombstone:
    allocation_id: UUID
    allocation_epoch: int
    expires_at: datetime


class PythonSessionService:
    """Allocation-local Python sessions with explicit loss semantics.

    Stateful interpreters and execution results are data-plane state. They are
    intentionally bounded to this manager/allocation incarnation instead of
    being copied into the lifecycle database.
    """

    def __init__(
        self,
        database: StateDatabase,
        provider: ProviderPythonSessionPort,
        *,
        max_sessions: int = 512,
        max_execution_results: int = 32,
        session_idle_seconds: int = 3600,
    ) -> None:
        if (
            max_sessions < 1
            or max_execution_results < 1
            or session_idle_seconds < 1
        ):
            raise ValueError("Python runtime caches must retain at least one record")
        self._database = database
        self._provider = provider
        self._sessions: dict[tuple[str, UUID, UUID], PythonSessionRef] = {}
        self._ambiguous_sessions: dict[
            tuple[str, UUID, UUID], _SessionTombstone
        ] = {}
        self._creating_sessions: set[tuple[str, UUID, UUID]] = set()
        self._session_last_used: dict[tuple[str, UUID, UUID], datetime] = {}
        self._results: OrderedDict[
            tuple[str, UUID, UUID], _ExecutionRecord
        ] = OrderedDict()
        self._inflight_executions: dict[
            tuple[str, UUID, UUID], tuple[str, asyncio.Task[PythonResult]]
        ] = {}
        self._max_sessions = max_sessions
        self._max_execution_results = max_execution_results
        self._session_idle = timedelta(seconds=session_idle_seconds)
        self._state_lock = asyncio.Lock()
        # Provider calls for one stateful interpreter must be serialized, but
        # unrelated sessions must never queue behind each other.
        self._session_locks = tuple(asyncio.Lock() for _ in range(64))

    async def create(
        self, key: SandboxKey, request: CreatePythonSessionRequest
    ) -> tuple[PythonSessionRef, bool]:
        self._check_deadline(request.deadline_at)
        allocation = await self._lease_allocation(key, request.deadline_at)
        cache_key = self._runtime_key(key, request.session_id)
        async with self._session_lock(cache_key):
            async with self._state_lock:
                now = datetime.now(timezone.utc)
                self._prune_expired_sessions_locked(now)
                allocation_fenced = False
                for tombstone_key, tombstone in tuple(
                    self._ambiguous_sessions.items()
                ):
                    if tombstone_key[:2] != cache_key[:2]:
                        continue
                    if self._tombstone_matches(tombstone, allocation):
                        allocation_fenced = True
                    else:
                        self._ambiguous_sessions.pop(tombstone_key, None)
                if allocation_fenced:
                    raise AgentBoxError(
                        ErrorCode.UNKNOWN_DISPATCH,
                        (
                            "Python context outcome was lost for this "
                            "allocation; retry after allocation replacement"
                        ),
                        retry=RetryDisposition.DO_NOT_RETRY,
                        status_code=409,
                    )
                existing = self._sessions.get(cache_key)
            if existing is not None and self._matches_allocation(
                existing, allocation
            ):
                if (
                    existing.cwd != request.cwd
                    or existing.environment_keys != request.environment_keys
                ):
                    raise AgentBoxError(
                        ErrorCode.OPERATION_CONFLICT,
                        "session ID was reused with different configuration",
                        retry=RetryDisposition.DO_NOT_RETRY,
                        status_code=409,
                    )
                async with self._state_lock:
                    self._session_last_used[cache_key] = datetime.now(timezone.utc)
                return existing, False
            if existing is not None:
                async with self._state_lock:
                    self._sessions.pop(cache_key, None)
                    self._session_last_used.pop(cache_key, None)
            async with self._state_lock:
                if (
                    len(self._sessions)
                    + len(self._ambiguous_sessions)
                    + len(self._creating_sessions)
                    >= self._max_sessions
                ):
                    raise AgentBoxError(
                        ErrorCode.CAPACITY_EXHAUSTED,
                        "manager Python session capacity is temporarily full",
                        retry=RetryDisposition.WAIT,
                        status_code=429,
                        retry_after_ms=1000,
                    )
                self._creating_sessions.add(cache_key)
            try:
                result = await self._provider.create_python_session(
                    self._provider_ref(allocation), request
                )
            except ProviderPythonSessionCreateAmbiguous as exc:
                async with self._state_lock:
                    self._creating_sessions.discard(cache_key)
                    self._tombstone_session_locked(cache_key, allocation)
                raise AgentBoxError(
                    ErrorCode.UNKNOWN_DISPATCH,
                    (
                        "Python session creation outcome was lost for this "
                        "allocation; retry after allocation replacement"
                    ),
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                ) from exc
            except ProviderPythonSessionCreateRejected as exc:
                async with self._state_lock:
                    self._creating_sessions.discard(cache_key)
                raise AgentBoxError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "provider rejected Python session creation",
                    retry=RetryDisposition.SAFE_SAME_OPERATION,
                    status_code=503,
                ) from exc
            except asyncio.CancelledError:
                async with self._state_lock:
                    self._creating_sessions.discard(cache_key)
                    self._tombstone_session_locked(cache_key, allocation)
                raise
            except BaseException:
                async with self._state_lock:
                    self._creating_sessions.discard(cache_key)
                raise
            session = PythonSessionRef(
                key=key,
                session_id=request.session_id,
                allocation_id=allocation.allocation_id,
                allocation_epoch=allocation.allocation_epoch or 0,
                provider_context_id=result.provider_context_id,
                cwd=request.cwd,
                environment_keys=request.environment_keys,
                state=PythonSessionState.ACTIVE,
            )
            async with self._state_lock:
                self._creating_sessions.discard(cache_key)
                self._sessions[cache_key] = session
                self._session_last_used[cache_key] = datetime.now(timezone.utc)
            return session, True

    async def execute(
        self,
        key: SandboxKey,
        session_id: UUID,
        request: ExecutePythonRequest,
    ) -> tuple[PythonResult, bool]:
        self._check_deadline(request.deadline_at)
        request_hash = self._execution_hash(session_id, request)
        result_key = self._runtime_key(key, request.operation_id)
        async with self._state_lock:
            self._prune_expired_results_locked(datetime.now(timezone.utc))
            existing = self._results.get(result_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise AgentBoxError(
                        ErrorCode.OPERATION_CONFLICT,
                        "Python operation ID was reused with a different request",
                        retry=RetryDisposition.DO_NOT_RETRY,
                        status_code=409,
                    )
                if existing.result.state == PythonExecutionState.UNKNOWN:
                    raise self._execution_outcome_unknown()
                self._results.move_to_end(result_key)
                return existing.result, False
            in_flight = self._inflight_executions.get(result_key)
            created = in_flight is None
            if in_flight is not None:
                in_flight_hash, task = in_flight
                if in_flight_hash != request_hash:
                    raise AgentBoxError(
                        ErrorCode.OPERATION_CONFLICT,
                        "Python operation ID was reused with a different request",
                        retry=RetryDisposition.DO_NOT_RETRY,
                        status_code=409,
                    )
            else:
                if (
                    len(self._results) + len(self._inflight_executions)
                    >= self._max_execution_results
                ):
                    raise AgentBoxError(
                        ErrorCode.CAPACITY_EXHAUSTED,
                        "manager Python result capacity is temporarily full",
                        retry=RetryDisposition.WAIT,
                        status_code=429,
                        retry_after_ms=1000,
                    )
                task = create_inherited_task(
                    self._execute_new(
                        key,
                        session_id,
                        request,
                        request_hash=request_hash,
                    ),
                    name=f"agentbox-python-execute:{request.operation_id}",
                )
                self._inflight_executions[result_key] = (request_hash, task)
                def clear(completed: asyncio.Task[PythonResult]) -> None:
                    current = self._inflight_executions.get(result_key)
                    if current is not None and current[1] is completed:
                        self._inflight_executions.pop(result_key, None)

                task.add_done_callback(clear)
        return await asyncio.shield(task), created

    async def _execute_new(
        self,
        key: SandboxKey,
        session_id: UUID,
        request: ExecutePythonRequest,
        *,
        request_hash: str,
    ) -> PythonResult:
        result_key = self._runtime_key(key, request.operation_id)
        session_key = self._runtime_key(key, session_id)
        async with self._session_lock(session_key):
            allocation = await self._lease_allocation(key, request.deadline_at)
            async with self._state_lock:
                session = self._sessions.get(session_key)
                if session is not None:
                    self._session_last_used[session_key] = datetime.now(timezone.utc)
            if session is None or not self._matches_allocation(session, allocation):
                raise AgentBoxError(
                    ErrorCode.ALLOCATION_CHANGED,
                    "Python session was lost with its allocation incarnation",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                )
            try:
                result = await self._provider.execute_python(
                    self._provider_ref(allocation), session, request
                )
            except ProviderPythonExecutionAmbiguous as exc:
                cleanup_succeeded = False
                try:
                    await self._provider.delete_python_session(
                        self._provider_ref(allocation),
                        session,
                        deadline_at=max(
                            request.deadline_at,
                            datetime.now(timezone.utc) + timedelta(seconds=5),
                        ),
                    )
                    cleanup_succeeded = True
                except Exception:
                    # The exact context may still be running. Fence this
                    # session ID for the allocation rather than creating a
                    # second interpreter that can race its side effects.
                    cleanup_succeeded = False
                async with self._state_lock:
                    self._results[result_key] = _ExecutionRecord(
                        request_hash=request_hash,
                        result=PythonResult(
                            operation_id=request.operation_id,
                            state=PythonExecutionState.UNKNOWN,
                            stdout="",
                            stderr="",
                            result=None,
                            error_name="OutcomeUnknown",
                            error_message=(
                                "Python execution may have partially mutated the session"
                            ),
                            traceback=None,
                            output_truncated=False,
                        ),
                        deadline_at=request.deadline_at,
                    )
                    self._sessions.pop(session_key, None)
                    self._session_last_used.pop(session_key, None)
                    if cleanup_succeeded:
                        self._ambiguous_sessions.pop(session_key, None)
                    else:
                        self._tombstone_session_locked(session_key, allocation)
                raise self._execution_outcome_unknown() from exc
            except ProviderPythonExecutionRejected as exc:
                raise AgentBoxError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "provider rejected Python execution before code began",
                    retry=RetryDisposition.SAFE_SAME_OPERATION,
                    status_code=503,
                ) from exc
            async with self._state_lock:
                self._results[result_key] = _ExecutionRecord(
                    request_hash=request_hash,
                    result=result,
                    deadline_at=request.deadline_at,
                )
                self._results.move_to_end(result_key)
            return result

    async def inspect(self, key: SandboxKey, session_id: UUID) -> PythonSessionRef:
        session_key = self._runtime_key(key, session_id)
        async with self._session_lock(session_key):
            async with self._state_lock:
                self._prune_expired_sessions_locked(datetime.now(timezone.utc))
                session = self._sessions.get(session_key)
                if session is not None:
                    self._session_last_used[session_key] = datetime.now(timezone.utc)
            if session is None:
                raise AgentBoxError(
                    ErrorCode.SANDBOX_NOT_FOUND,
                    "Python session handle is no longer available",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=404,
                )
            async with self._database.uow() as uow:
                allocation = await uow.repository.current_allocation(key)
                await uow.commit()
            if (
                allocation is not None
                and allocation.state == AllocationState.ACTIVE
                and self._matches_allocation(session, allocation)
            ):
                return session
            stale = replace(session, state=PythonSessionState.STALE)
            async with self._state_lock:
                if self._sessions.get(session_key) is session:
                    self._sessions[session_key] = stale
            return stale

    async def restart(
        self, key: SandboxKey, session_id: UUID, *, deadline_at: datetime
    ) -> PythonSessionRef:
        self._check_deadline(deadline_at)
        session_key = self._runtime_key(key, session_id)
        async with self._session_lock(session_key):
            allocation = await self._lease_allocation(key, deadline_at)
            async with self._state_lock:
                session = self._sessions.get(session_key)
                if session is not None:
                    self._session_last_used[session_key] = datetime.now(timezone.utc)
            if session is None or not self._matches_allocation(session, allocation):
                raise AgentBoxError(
                    ErrorCode.ALLOCATION_CHANGED,
                    "Python session was lost with its allocation incarnation",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                )
            try:
                result = await self._provider.restart_python_session(
                    self._provider_ref(allocation),
                    session,
                    deadline_at=deadline_at,
                )
            except ProviderPythonSessionCreateAmbiguous as exc:
                async with self._state_lock:
                    self._sessions.pop(session_key, None)
                    self._session_last_used.pop(session_key, None)
                    self._tombstone_session_locked(session_key, allocation)
                raise AgentBoxError(
                    ErrorCode.UNKNOWN_DISPATCH,
                    "Python session restart outcome was lost; open a new session",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                ) from exc
            replacement = PythonSessionRef(
                key=session.key,
                session_id=session.session_id,
                allocation_id=session.allocation_id,
                allocation_epoch=session.allocation_epoch,
                provider_context_id=result.provider_context_id,
                cwd=session.cwd,
                environment_keys=session.environment_keys,
                state=PythonSessionState.ACTIVE,
            )
            async with self._state_lock:
                self._sessions[session_key] = replacement
                self._session_last_used[session_key] = datetime.now(timezone.utc)
            return replacement

    async def delete(
        self, key: SandboxKey, session_id: UUID, *, deadline_at: datetime
    ) -> bool:
        self._check_deadline(deadline_at)
        session_key = self._runtime_key(key, session_id)
        async with self._session_lock(session_key):
            allocation = await self._lease_allocation(key, deadline_at)
            async with self._state_lock:
                session = self._sessions.get(session_key)
            if session is None:
                # An ambiguous create has no safe provider context identity to
                # delete. Keep its tombstone until bounded expiry and require a
                # fresh session ID.
                return False
            if not self._matches_allocation(session, allocation):
                return False
            await self._provider.delete_python_session(
                self._provider_ref(allocation),
                session,
                deadline_at=deadline_at,
            )
            async with self._state_lock:
                current = self._sessions.get(session_key)
                if current == session:
                    self._sessions.pop(session_key, None)
                    self._session_last_used.pop(session_key, None)
                    self._ambiguous_sessions.pop(session_key, None)
            return True

    def _session_lock(self, key: tuple[str, UUID, UUID]) -> asyncio.Lock:
        return self._session_locks[hash(key) % len(self._session_locks)]

    def _prune_expired_sessions_locked(self, now: datetime) -> None:
        expired = [
            session_key
            for session_key, last_used_at in self._session_last_used.items()
            if last_used_at + self._session_idle <= now
        ]
        for session_key in expired:
            self._sessions.pop(session_key, None)
            self._session_last_used.pop(session_key, None)
        expired_tombstones = [
            session_key
            for session_key, tombstone in self._ambiguous_sessions.items()
            if tombstone.expires_at <= now
        ]
        for session_key in expired_tombstones:
            self._ambiguous_sessions.pop(session_key, None)

    def _tombstone_session_locked(
        self,
        session_key: tuple[str, UUID, UUID],
        allocation: PhysicalAllocation,
    ) -> None:
        self._ambiguous_sessions[session_key] = _SessionTombstone(
            allocation_id=allocation.allocation_id,
            allocation_epoch=allocation.allocation_epoch or 0,
            expires_at=datetime.now(timezone.utc) + self._session_idle,
        )

    @staticmethod
    def _tombstone_matches(
        tombstone: _SessionTombstone,
        allocation: PhysicalAllocation,
    ) -> bool:
        return (
            tombstone.allocation_id == allocation.allocation_id
            and tombstone.allocation_epoch == (allocation.allocation_epoch or 0)
        )

    def _prune_expired_results_locked(self, now: datetime) -> None:
        expired = [
            result_key
            for result_key, record in self._results.items()
            if record.deadline_at <= now
        ]
        for result_key in expired:
            self._results.pop(result_key, None)

    @staticmethod
    def _runtime_key(key: SandboxKey, operation_id: UUID) -> tuple[str, UUID, UUID]:
        return key.workload_kind.value, key.logical_id, operation_id

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
                "workspace allocation is not ready for Python",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        return allocation

    @staticmethod
    def _matches_allocation(
        session: PythonSessionRef, allocation: PhysicalAllocation
    ) -> bool:
        return (
            session.allocation_id == allocation.allocation_id
            and session.allocation_epoch == allocation.allocation_epoch
            and session.state == PythonSessionState.ACTIVE
        )

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
    def _execution_hash(session_id: UUID, request: ExecutePythonRequest) -> str:
        canonical = "\x1f".join(
            (
                str(session_id),
                str(request.operation_id),
                request.code,
                hashlib.sha256(
                    "\x1e".join(
                        f"{item.name}\x1d{item.value}"
                        for item in request.environment
                    ).encode()
                ).hexdigest(),
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
                "Python operation deadline has elapsed",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=408,
            )

    @staticmethod
    def _execution_outcome_unknown() -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.UNKNOWN_DISPATCH,
            "Python execution outcome was lost; the session was invalidated",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=409,
        )
