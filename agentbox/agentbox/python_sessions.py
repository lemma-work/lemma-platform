from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from uuid import UUID

from agentbox.domain import (
    AgentBoxError,
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
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderPythonExecutionAmbiguous,
    ProviderPythonExecutionRejected,
    ProviderPythonSessionCreateAmbiguous,
    ProviderPythonSessionCreateRejected,
    ProviderPythonSessionPort,
)


class PythonSessionService:
    """Durable session/execution intents with provider I/O outside transactions."""

    def __init__(
        self, database: StateDatabase, provider: ProviderPythonSessionPort
    ) -> None:
        self._database = database
        self._provider = provider

    async def create(
        self, key: SandboxKey, request: CreatePythonSessionRequest
    ) -> tuple[PythonSessionRef, bool]:
        self._check_deadline(request.deadline_at)
        async with self._database.uow() as uow:
            session, created = await uow.repository.reserve_python_session(
                key,
                session_id=request.session_id,
                cwd=request.cwd,
                env_keys=request.environment_keys,
            )
            allocation = await uow.repository.get_allocation_by_id(
                session.allocation_id
            )
            await uow.commit()
        if allocation is None:  # pragma: no cover - FK invariant
            raise RuntimeError("Python session allocation disappeared")
        if not created and session.state != PythonSessionState.RESERVED:
            return session, False

        async with self._database.uow() as uow:
            dispatch = await uow.repository.mark_python_session_creating(
                key, request.session_id
            )
            await uow.commit()
        if not dispatch:
            return await self.inspect(key, request.session_id), False

        try:
            result = await self._provider.create_python_session(
                self._provider_ref(allocation), request
            )
        except ProviderPythonSessionCreateAmbiguous as exc:
            async with self._database.uow() as uow:
                await uow.repository.mark_python_session_unknown(
                    key, request.session_id
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "Python session creation outcome is unknown",
                retry=RetryDisposition.WAIT,
                status_code=202,
            ) from exc
        except ProviderPythonSessionCreateRejected as exc:
            async with self._database.uow() as uow:
                session = await uow.repository.set_python_session_state(
                    key,
                    request.session_id,
                    PythonSessionState.RESERVED,
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "provider rejected Python session creation",
                retry=RetryDisposition.SAFE_SAME_OPERATION,
                status_code=503,
            ) from exc

        async with self._database.uow() as uow:
            session = await uow.repository.acknowledge_python_session(
                key,
                request.session_id,
                provider_context_id=result.provider_context_id,
            )
            await uow.commit()
        return session, created

    async def execute(
        self,
        key: SandboxKey,
        session_id: UUID,
        request: ExecutePythonRequest,
    ) -> tuple[PythonResult, bool]:
        self._check_deadline(request.deadline_at)
        request_hash = self._execution_hash(session_id, request)
        async with self._database.uow() as uow:
            result, created = await uow.repository.reserve_python_execution(
                key,
                session_id,
                operation_id=request.operation_id,
                request_hash=request_hash,
                deadline_at=request.deadline_at,
            )
            session = await uow.repository.get_python_session(key, session_id)
            allocation = (
                await uow.repository.get_allocation_by_id(session.allocation_id)
                if session is not None
                else None
            )
            await uow.commit()
        if session is None or allocation is None:  # pragma: no cover - FK invariant
            raise RuntimeError("Python execution owner disappeared")
        if not created and result.state != PythonExecutionState.RESERVED:
            return result, False

        async with self._database.uow() as uow:
            dispatch = await uow.repository.mark_python_execution_starting(
                key, request.operation_id
            )
            await uow.commit()
        if not dispatch:  # pragma: no cover - same reservation owns first dispatch
            return result, False

        try:
            provider_result = await self._provider.execute_python(
                self._provider_ref(allocation), session, request
            )
        except ProviderPythonExecutionAmbiguous as exc:
            async with self._database.uow() as uow:
                result = await uow.repository.mark_python_execution_unknown(
                    key, request.operation_id
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "Python execution outcome is unknown and will not be replayed",
                retry=RetryDisposition.WAIT,
                status_code=202,
            ) from exc
        except ProviderPythonExecutionRejected as exc:
            async with self._database.uow() as uow:
                result = await uow.repository.reset_python_execution_after_rejection(
                    key, request.operation_id
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "provider rejected Python execution before code began",
                retry=RetryDisposition.SAFE_SAME_OPERATION,
                status_code=503,
            ) from exc

        async with self._database.uow() as uow:
            result = await uow.repository.complete_python_execution(
                key, provider_result
            )
            await uow.commit()
        return result, created

    async def inspect(self, key: SandboxKey, session_id: UUID) -> PythonSessionRef:
        async with self._database.uow() as uow:
            session = await uow.repository.get_python_session(key, session_id)
            await uow.commit()
        if session is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "Python session does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        return session

    async def restart(
        self, key: SandboxKey, session_id: UUID, *, deadline_at: datetime
    ) -> PythonSessionRef:
        self._check_deadline(deadline_at)
        session, allocation = await self._bound_session(key, session_id)
        try:
            result = await self._provider.restart_python_session(
                self._provider_ref(allocation),
                session,
                deadline_at=deadline_at,
            )
        except ProviderPythonSessionCreateAmbiguous as exc:
            async with self._database.uow() as uow:
                session = await uow.repository.set_python_session_state(
                    key, session_id, PythonSessionState.UNKNOWN
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "Python session restart outcome is unknown",
                retry=RetryDisposition.WAIT,
                status_code=202,
            ) from exc
        async with self._database.uow() as uow:
            session = await uow.repository.acknowledge_python_session(
                key,
                session_id,
                provider_context_id=result.provider_context_id,
            )
            await uow.commit()
        return session

    async def delete(
        self, key: SandboxKey, session_id: UUID, *, deadline_at: datetime
    ) -> bool:
        self._check_deadline(deadline_at)
        session, allocation = await self._bound_session(key, session_id)
        await self._provider.delete_python_session(
            self._provider_ref(allocation), session, deadline_at=deadline_at
        )
        async with self._database.uow() as uow:
            await uow.repository.set_python_session_state(
                key,
                session_id,
                PythonSessionState.DELETED,
                provider_context_id=None,
            )
            await uow.commit()
        return True

    async def _bound_session(
        self, key: SandboxKey, session_id: UUID
    ) -> tuple[PythonSessionRef, PhysicalAllocation]:
        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(key)
            session = await uow.repository.get_python_session(key, session_id)
            allocation = (
                await uow.repository.get_allocation_by_id(session.allocation_id)
                if session is not None
                else None
            )
            await uow.commit()
        if logical is None or session is None or allocation is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "Python session or allocation does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if (
            logical.current_allocation_id != session.allocation_id
            or logical.allocation_epoch != session.allocation_epoch
            or session.state != PythonSessionState.ACTIVE
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "Python session belongs to a stale allocation",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        return session, allocation

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
    def _execution_hash(session_id: UUID, request: ExecutePythonRequest) -> str:
        canonical = "\x1f".join(
            (
                str(session_id),
                str(request.operation_id),
                request.code,
                "\x1e".join(item.name for item in request.environment),
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
