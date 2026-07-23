from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.domain.entities import (
    FunctionAttemptStatus,
    FunctionExecutionClaim,
    FunctionExecutionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.models import (
    FunctionExecutionAttemptModel,
    FunctionExecutionRequestModel,
    FunctionRunModel,
)


ACTIVE_REQUEST_STATES = (
    FunctionExecutionStatus.DISPATCHING.value,
    FunctionExecutionStatus.RUNNING.value,
)
TERMINAL_REQUEST_STATES = (
    FunctionExecutionStatus.COMPLETED.value,
    FunctionExecutionStatus.FAILED.value,
    FunctionExecutionStatus.CANCELLED.value,
)


class FunctionExecutionClaimRepository:
    """Atomically reserves per-pod capacity and creates fenced attempts."""

    def __init__(
        self,
        session: AsyncSession,
        credential_signer: FunctionAttemptCredentialSigner,
    ) -> None:
        self._session = session
        self._credential_signer = credential_signer

    async def claim(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        total_units: int,
        api_reserved_units: int,
        lease_seconds: int,
        timestamp: datetime,
    ) -> FunctionExecutionClaim | None:
        request = await self._locked_request(run_id)
        if request is None or request.status in TERMINAL_REQUEST_STATES:
            return None
        if request.status in ACTIVE_REQUEST_STATES:
            return await self._renew_active_claim(
                request,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                timestamp=timestamp,
            )
        if request.status != FunctionExecutionStatus.QUEUED.value:
            return None
        if request.available_at > timestamp or request.deadline_at <= timestamp:
            return None
        if request.kind == FunctionType.JOB.value and await self._api_is_waiting(
            request.pod_id, timestamp
        ):
            return None
        if not await self._has_capacity(
            request,
            total_units=total_units,
            api_reserved_units=api_reserved_units,
        ):
            return None
        return await self._start_attempt(
            request,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            timestamp=timestamp,
        )

    async def _locked_request(
        self, run_id: UUID
    ) -> FunctionExecutionRequestModel | None:
        pod_id = await self._session.scalar(
            select(FunctionExecutionRequestModel.pod_id).where(
                FunctionExecutionRequestModel.run_id == run_id
            )
        )
        if pod_id is None:
            return None
        # Every reservation for a pod locks the same queued/active rows in the
        # same order. Loading only the pod id first avoids a stale ORM request
        # while another dispatcher owns the lock.
        locked_request_ids = await self._session.scalars(
            select(FunctionExecutionRequestModel.id)
            .where(
                FunctionExecutionRequestModel.pod_id == pod_id,
                FunctionExecutionRequestModel.status.in_(
                    (FunctionExecutionStatus.QUEUED.value, *ACTIVE_REQUEST_STATES)
                ),
            )
            .order_by(FunctionExecutionRequestModel.id)
            .with_for_update()
        )
        locked_request_ids.all()
        return await self._session.scalar(
            select(FunctionExecutionRequestModel)
            .where(FunctionExecutionRequestModel.run_id == run_id)
            .execution_options(populate_existing=True)
        )

    async def _renew_active_claim(
        self,
        request: FunctionExecutionRequestModel,
        *,
        worker_id: str,
        lease_seconds: int,
        timestamp: datetime,
    ) -> FunctionExecutionClaim | None:
        lease_is_owned_elsewhere = (
            request.lease_owner is not None
            and request.lease_owner != worker_id
            and request.lease_expires_at is not None
            and request.lease_expires_at > timestamp
        )
        if lease_is_owned_elsewhere:
            return None
        attempt = await self._latest_attempt(request.run_id)
        if attempt is None:
            return None
        request.lease_owner = worker_id
        request.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        return self._execution_claim(request, attempt)

    async def _api_is_waiting(self, pod_id: UUID, timestamp: datetime) -> bool:
        queued_api = await self._session.scalar(
            select(FunctionExecutionRequestModel.id)
            .where(
                FunctionExecutionRequestModel.pod_id == pod_id,
                FunctionExecutionRequestModel.kind == FunctionType.API.value,
                FunctionExecutionRequestModel.status
                == FunctionExecutionStatus.QUEUED.value,
                FunctionExecutionRequestModel.available_at <= timestamp,
                FunctionExecutionRequestModel.deadline_at > timestamp,
            )
            .limit(1)
        )
        return queued_api is not None

    async def _has_capacity(
        self,
        request: FunctionExecutionRequestModel,
        *,
        total_units: int,
        api_reserved_units: int,
    ) -> bool:
        active_units = await self._active_units(request.pod_id)
        job_units = await self._active_units(request.pod_id, kind=FunctionType.JOB)
        total_capacity_available = active_units + request.units <= total_units
        reserved_api_capacity_available = not (
            request.kind == FunctionType.JOB.value
            and job_units + request.units > total_units - api_reserved_units
        )
        return total_capacity_available and reserved_api_capacity_available

    async def _active_units(
        self, pod_id: UUID, *, kind: FunctionType | None = None
    ) -> int:
        query = select(
            func.coalesce(func.sum(FunctionExecutionRequestModel.units), 0)
        ).where(
            FunctionExecutionRequestModel.pod_id == pod_id,
            FunctionExecutionRequestModel.status.in_(ACTIVE_REQUEST_STATES),
        )
        if kind is not None:
            query = query.where(FunctionExecutionRequestModel.kind == kind.value)
        return int(await self._session.scalar(query) or 0)

    async def _start_attempt(
        self,
        request: FunctionExecutionRequestModel,
        *,
        worker_id: str,
        lease_seconds: int,
        timestamp: datetime,
    ) -> FunctionExecutionClaim:
        number = (
            int(
                await self._session.scalar(
                    select(
                        func.coalesce(func.max(FunctionExecutionAttemptModel.number), 0)
                    ).where(FunctionExecutionAttemptModel.run_id == request.run_id)
                )
                or 0
            )
            + 1
        )
        attempt_id = uuid7()
        ticket = self._credential_signer.derive(attempt_id, "ticket")
        runtime_token = self._credential_signer.derive(attempt_id, "runtime")
        fence = request.next_fence
        attempt = FunctionExecutionAttemptModel(
            id=attempt_id,
            run_id=request.run_id,
            request_id=request.id,
            number=number,
            fence=fence,
            operation_id=uuid7(),
            status=FunctionAttemptStatus.PROCESS_STARTING.value,
            ticket_digest=self._credential_signer.digest(ticket),
            runtime_token_digest=self._credential_signer.digest(runtime_token),
            ticket_expires_at=request.deadline_at,
        )
        self._session.add(attempt)
        request.status = FunctionExecutionStatus.DISPATCHING.value
        request.next_fence = fence + 1
        request.lease_owner = worker_id
        request.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        run = await self._session.get(FunctionRunModel, request.run_id)
        if run is None:
            raise RuntimeError("function execution request has no public run")
        run.current_attempt_id = attempt_id
        run.execution_fence = fence
        await self._session.flush()
        return self._execution_claim(request, attempt)

    async def _latest_attempt(
        self, run_id: UUID
    ) -> FunctionExecutionAttemptModel | None:
        return await self._session.scalar(
            select(FunctionExecutionAttemptModel)
            .where(FunctionExecutionAttemptModel.run_id == run_id)
            .order_by(FunctionExecutionAttemptModel.number.desc())
            .limit(1)
        )

    def _execution_claim(
        self,
        request: FunctionExecutionRequestModel,
        attempt: FunctionExecutionAttemptModel,
    ) -> FunctionExecutionClaim:
        return FunctionExecutionClaim(
            run_id=request.run_id,
            attempt_id=attempt.id,
            operation_id=attempt.operation_id,
            fence=attempt.fence,
            pod_id=request.pod_id,
            function_id=request.function_id,
            revision_id=request.revision_id,
            function_type=FunctionType(request.kind),
            deadline_at=request.deadline_at,
            ticket=self._credential_signer.derive(attempt.id, "ticket"),
            runtime_token=self._credential_signer.derive(attempt.id, "runtime"),
        )
