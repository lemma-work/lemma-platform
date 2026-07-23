from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.domain.entities import (
    FunctionAttemptRuntimeContext,
    FunctionAttemptStatus,
    FunctionExecutionClaim,
    FunctionExecutionStatus,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionType,
)
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunFailedEvent,
    FunctionRunStartedEvent,
)
from app.modules.function.infrastructure.models import (
    FunctionExecutionAttemptModel,
    FunctionExecutionRequestModel,
    FunctionModel,
    FunctionRevisionModel,
    FunctionRunModel,
)


ACTIVE_REQUEST_STATES = (
    FunctionExecutionStatus.DISPATCHING.value,
    FunctionExecutionStatus.RUNNING.value,
)
TERMINAL_ATTEMPT_STATES = (
    FunctionAttemptStatus.COMPLETED.value,
    FunctionAttemptStatus.FAILED.value,
    FunctionAttemptStatus.CANCELLED.value,
    FunctionAttemptStatus.UNKNOWN.value,
)


class FunctionExecutionRepository:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        credential_signer: FunctionAttemptCredentialSigner,
    ) -> None:
        self.uow = uow
        self.session = uow.session
        self._credential_signer = credential_signer

    async def claim_run(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        total_units: int,
        api_reserved_units: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> FunctionExecutionClaim | None:
        timestamp = now or datetime.now(timezone.utc)
        pod_id = await self.session.scalar(
            select(FunctionExecutionRequestModel.pod_id).where(
                FunctionExecutionRequestModel.run_id == run_id
            )
        )
        if pod_id is None:
            return None
        # All reservations for a pod lock the same queued/active rows in the same
        # order. This makes the capacity calculation atomic without repeatedly
        # locking the pod's unbounded terminal history or keeping a connection
        # while the resulting provider work runs. Read only the pod id before
        # acquiring the lock: loading the ORM request here would leave a stale
        # QUEUED/next_fence value in SQLAlchemy's identity map while another
        # dispatcher owns the lock.
        locked_request_ids = await self.session.scalars(
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
        request = await self.session.scalar(
            select(FunctionExecutionRequestModel)
            .where(FunctionExecutionRequestModel.run_id == run_id)
            .execution_options(populate_existing=True)
        )
        if request is None:
            return None
        if request.status in {
            FunctionExecutionStatus.COMPLETED.value,
            FunctionExecutionStatus.FAILED.value,
            FunctionExecutionStatus.CANCELLED.value,
        }:
            return None
        if request.status in ACTIVE_REQUEST_STATES:
            if (
                request.lease_owner is not None
                and request.lease_owner != worker_id
                and request.lease_expires_at is not None
                and request.lease_expires_at > timestamp
            ):
                return None
            attempt = await self._latest_attempt(run_id)
            if attempt is None:
                return None
            request.lease_owner = worker_id
            request.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
            ticket = self._credential_signer.derive(attempt.id, "ticket")
            runtime_token = self._credential_signer.derive(attempt.id, "runtime")
            return FunctionExecutionClaim(
                run_id=run_id,
                attempt_id=attempt.id,
                operation_id=attempt.operation_id,
                fence=attempt.fence,
                pod_id=request.pod_id,
                function_id=request.function_id,
                revision_id=request.revision_id,
                function_type=FunctionType(request.kind),
                deadline_at=request.deadline_at,
                ticket=ticket,
                runtime_token=runtime_token,
            )
        if request.status != FunctionExecutionStatus.QUEUED.value:
            return None
        if request.available_at > timestamp or request.deadline_at <= timestamp:
            return None

        if request.kind == FunctionType.JOB.value:
            queued_api = await self.session.scalar(
                select(FunctionExecutionRequestModel.id)
                .where(
                    FunctionExecutionRequestModel.pod_id == request.pod_id,
                    FunctionExecutionRequestModel.kind == FunctionType.API.value,
                    FunctionExecutionRequestModel.status
                    == FunctionExecutionStatus.QUEUED.value,
                    FunctionExecutionRequestModel.available_at <= timestamp,
                    FunctionExecutionRequestModel.deadline_at > timestamp,
                )
                .limit(1)
            )
            if queued_api is not None:
                return None

        active_units = int(
            await self.session.scalar(
                select(
                    func.coalesce(func.sum(FunctionExecutionRequestModel.units), 0)
                ).where(
                    FunctionExecutionRequestModel.pod_id == request.pod_id,
                    FunctionExecutionRequestModel.status.in_(ACTIVE_REQUEST_STATES),
                )
            )
            or 0
        )
        job_units = int(
            await self.session.scalar(
                select(
                    func.coalesce(func.sum(FunctionExecutionRequestModel.units), 0)
                ).where(
                    FunctionExecutionRequestModel.pod_id == request.pod_id,
                    FunctionExecutionRequestModel.status.in_(ACTIVE_REQUEST_STATES),
                    FunctionExecutionRequestModel.kind == FunctionType.JOB.value,
                )
            )
            or 0
        )
        if active_units + request.units > total_units:
            return None
        if (
            request.kind == FunctionType.JOB.value
            and job_units + request.units > total_units - api_reserved_units
        ):
            return None

        number = (
            int(
                await self.session.scalar(
                    select(
                        func.coalesce(func.max(FunctionExecutionAttemptModel.number), 0)
                    ).where(FunctionExecutionAttemptModel.run_id == run_id)
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
            run_id=run_id,
            request_id=request.id,
            number=number,
            fence=fence,
            operation_id=uuid7(),
            status=FunctionAttemptStatus.PROCESS_STARTING.value,
            ticket_digest=self._credential_signer.digest(ticket),
            runtime_token_digest=self._credential_signer.digest(runtime_token),
            ticket_expires_at=request.deadline_at,
        )
        self.session.add(attempt)
        request.status = FunctionExecutionStatus.DISPATCHING.value
        request.next_fence = fence + 1
        request.lease_owner = worker_id
        request.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        run = await self.session.get(FunctionRunModel, run_id)
        if run is None:
            raise RuntimeError("function execution request has no public run")
        run.current_attempt_id = attempt_id
        run.execution_fence = fence
        await self.session.flush()
        return FunctionExecutionClaim(
            run_id=run_id,
            attempt_id=attempt_id,
            operation_id=attempt.operation_id,
            fence=fence,
            pod_id=request.pod_id,
            function_id=request.function_id,
            revision_id=request.revision_id,
            function_type=FunctionType(request.kind),
            deadline_at=request.deadline_at,
            ticket=ticket,
            runtime_token=runtime_token,
        )

    async def mark_process_started(
        self, attempt_id: UUID, *, provider_process_id: str | None
    ) -> None:
        attempt = await self.session.get(FunctionExecutionAttemptModel, attempt_id)
        if (
            attempt is None
            or attempt.status != FunctionAttemptStatus.PROCESS_STARTING.value
        ):
            return
        attempt.status = FunctionAttemptStatus.PROCESS_STARTED.value
        attempt.provider_process_id = provider_process_id
        await self.session.flush()

    async def claim_ticket(
        self, ticket_digest: str, *, now: datetime | None = None
    ) -> FunctionAttemptRuntimeContext | None:
        timestamp = now or datetime.now(timezone.utc)
        row = (
            await self.session.execute(
                select(
                    FunctionExecutionAttemptModel,
                    FunctionExecutionRequestModel,
                    FunctionRunModel,
                    FunctionModel,
                    FunctionRevisionModel,
                )
                .join(
                    FunctionExecutionRequestModel,
                    FunctionExecutionRequestModel.id
                    == FunctionExecutionAttemptModel.request_id,
                )
                .join(
                    FunctionRunModel,
                    FunctionRunModel.id == FunctionExecutionAttemptModel.run_id,
                )
                .join(
                    FunctionModel,
                    FunctionModel.id == FunctionExecutionRequestModel.function_id,
                )
                .join(
                    FunctionRevisionModel,
                    FunctionRevisionModel.id
                    == FunctionExecutionRequestModel.revision_id,
                )
                .where(FunctionExecutionAttemptModel.ticket_digest == ticket_digest)
                .with_for_update(of=FunctionExecutionAttemptModel)
            )
        ).one_or_none()
        if row is None:
            return None
        attempt, request, run, function, revision = row
        if (
            attempt.ticket_expires_at <= timestamp
            or attempt.status
            not in {
                FunctionAttemptStatus.PROCESS_STARTED.value,
                FunctionAttemptStatus.RUNNING.value,
            }
            or run.current_attempt_id != attempt.id
            or run.execution_fence != attempt.fence
        ):
            return None
        # Idempotent for a lost claim response. The ticket identifies only this
        # process attempt and cannot authorize another run; accepting it again
        # is required to recover after the response is lost post-commit.
        if attempt.ticket_claimed_at is None:
            attempt.ticket_claimed_at = timestamp
        await self.session.flush()
        return self._runtime_context(attempt, request, run, function, revision)

    async def runtime_context(
        self, runtime_token_digest: str
    ) -> FunctionAttemptRuntimeContext | None:
        row = (
            await self.session.execute(
                select(
                    FunctionExecutionAttemptModel,
                    FunctionExecutionRequestModel,
                    FunctionRunModel,
                    FunctionModel,
                    FunctionRevisionModel,
                )
                .join(
                    FunctionExecutionRequestModel,
                    FunctionExecutionRequestModel.id
                    == FunctionExecutionAttemptModel.request_id,
                )
                .join(
                    FunctionRunModel,
                    FunctionRunModel.id == FunctionExecutionAttemptModel.run_id,
                )
                .join(
                    FunctionModel,
                    FunctionModel.id == FunctionExecutionRequestModel.function_id,
                )
                .join(
                    FunctionRevisionModel,
                    FunctionRevisionModel.id
                    == FunctionExecutionRequestModel.revision_id,
                )
                .where(
                    FunctionExecutionAttemptModel.runtime_token_digest
                    == runtime_token_digest
                )
            )
        ).one_or_none()
        if row is None:
            return None
        attempt, request, run, function, revision = row
        if attempt.ticket_claimed_at is None:
            return None
        return self._runtime_context(attempt, request, run, function, revision)

    async def mark_started(
        self,
        context: FunctionAttemptRuntimeContext,
        *,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or datetime.now(timezone.utc)
        attempt = await self.session.get(
            FunctionExecutionAttemptModel, context.attempt_id
        )
        run = await self.session.get(FunctionRunModel, context.run_id)
        request = await self.session.scalar(
            select(FunctionExecutionRequestModel).where(
                FunctionExecutionRequestModel.run_id == context.run_id
            )
        )
        if attempt is None or run is None or request is None:
            return False
        if run.current_attempt_id != attempt.id or run.execution_fence != context.fence:
            return False
        if attempt.status == FunctionAttemptStatus.RUNNING.value:
            return True
        if attempt.status != FunctionAttemptStatus.PROCESS_STARTED.value:
            return False
        attempt.status = FunctionAttemptStatus.RUNNING.value
        attempt.started_at = timestamp
        request.status = FunctionExecutionStatus.RUNNING.value
        run.status = FunctionRunStatus.RUNNING.value
        run.started_at = timestamp
        self.uow.collect_events(
            [
                FunctionRunStartedEvent(
                    run_id=run.id,
                    function_id=run.function_id,
                    started_at=timestamp,
                    user_email=run.user_email,
                    workspace_process_id=str(attempt.operation_id),
                )
            ]
        )
        await self.session.flush()
        return True

    async def complete(
        self,
        context: FunctionAttemptRuntimeContext,
        *,
        payload_hash: str,
        completed: bool,
        output_data: dict | None,
        error: str | None,
        logs: str | None,
        now: datetime | None = None,
    ) -> tuple[FunctionRunEntity | None, bool, bool]:
        timestamp = now or datetime.now(timezone.utc)
        attempt = await self.session.get(
            FunctionExecutionAttemptModel, context.attempt_id
        )
        run = await self.session.get(FunctionRunModel, context.run_id)
        request = await self.session.scalar(
            select(FunctionExecutionRequestModel).where(
                FunctionExecutionRequestModel.run_id == context.run_id
            )
        )
        if attempt is None or run is None or request is None:
            return None, False, False
        if run.current_attempt_id != attempt.id or run.execution_fence != context.fence:
            return None, False, False
        if attempt.status in TERMINAL_ATTEMPT_STATES:
            same_payload = attempt.terminal_payload_hash == payload_hash
            return run.to_entity(), same_payload, same_payload

        attempt.status = (
            FunctionAttemptStatus.COMPLETED.value
            if completed
            else FunctionAttemptStatus.FAILED.value
        )
        attempt.terminal_payload_hash = payload_hash
        attempt.completed_at = timestamp
        request.status = (
            FunctionExecutionStatus.COMPLETED.value
            if completed
            else FunctionExecutionStatus.FAILED.value
        )
        request.lease_owner = None
        request.lease_expires_at = None
        run.status = (
            FunctionRunStatus.COMPLETED.value
            if completed
            else FunctionRunStatus.FAILED.value
        )
        run.output_data = output_data if completed else None
        run.error = error
        run.logs = logs
        run.completed_at = timestamp
        event = (
            FunctionRunCompletedEvent(
                run_id=run.id,
                function_id=run.function_id,
                output_data=run.output_data,
                logs=logs,
                completed_at=timestamp,
                workspace_process_id=str(attempt.operation_id),
            )
            if completed
            else FunctionRunFailedEvent(
                run_id=run.id,
                function_id=run.function_id,
                error=error,
                logs=logs,
                completed_at=timestamp,
                workspace_process_id=str(attempt.operation_id),
            )
        )
        self.uow.collect_events([event])
        await self.session.flush()
        return run.to_entity(), True, False

    async def fail_dispatch(
        self,
        claim: FunctionExecutionClaim,
        *,
        error: str,
        unknown: bool,
    ) -> FunctionRunEntity | None:
        attempt = await self.session.get(
            FunctionExecutionAttemptModel, claim.attempt_id
        )
        run = await self.session.get(FunctionRunModel, claim.run_id)
        request = await self.session.scalar(
            select(FunctionExecutionRequestModel).where(
                FunctionExecutionRequestModel.run_id == claim.run_id
            )
        )
        if attempt is None or run is None or request is None:
            return None
        if run.current_attempt_id != attempt.id or run.execution_fence != claim.fence:
            return run.to_entity()
        if attempt.status in TERMINAL_ATTEMPT_STATES:
            return run.to_entity()
        timestamp = datetime.now(timezone.utc)
        attempt.status = (
            FunctionAttemptStatus.UNKNOWN.value
            if unknown
            else FunctionAttemptStatus.FAILED.value
        )
        attempt.completed_at = timestamp
        request.status = FunctionExecutionStatus.FAILED.value
        request.lease_owner = None
        request.lease_expires_at = None
        run.status = FunctionRunStatus.FAILED.value
        run.error = error
        run.completed_at = timestamp
        self.uow.collect_events(
            [
                FunctionRunFailedEvent(
                    run_id=run.id,
                    function_id=run.function_id,
                    error=error,
                    logs=run.logs,
                    completed_at=timestamp,
                    workspace_process_id=str(attempt.operation_id),
                )
            ]
        )
        await self.session.flush()
        return run.to_entity()

    async def fail_queued(
        self, run_id: UUID, *, error: str
    ) -> FunctionRunEntity | None:
        request = await self.session.scalar(
            select(FunctionExecutionRequestModel)
            .where(FunctionExecutionRequestModel.run_id == run_id)
            .with_for_update()
        )
        run = await self.session.get(FunctionRunModel, run_id)
        if request is None or run is None:
            return None
        if request.status != FunctionExecutionStatus.QUEUED.value:
            return run.to_entity()
        timestamp = datetime.now(timezone.utc)
        request.status = FunctionExecutionStatus.FAILED.value
        run.status = FunctionRunStatus.FAILED.value
        run.error = error
        run.completed_at = timestamp
        self.uow.collect_events(
            [
                FunctionRunFailedEvent(
                    run_id=run.id,
                    function_id=run.function_id,
                    error=error,
                    logs=None,
                    completed_at=timestamp,
                )
            ]
        )
        await self.session.flush()
        return run.to_entity()

    async def _latest_attempt(
        self, run_id: UUID
    ) -> FunctionExecutionAttemptModel | None:
        return await self.session.scalar(
            select(FunctionExecutionAttemptModel)
            .where(FunctionExecutionAttemptModel.run_id == run_id)
            .order_by(FunctionExecutionAttemptModel.number.desc())
            .limit(1)
        )

    @staticmethod
    def _runtime_context(
        attempt: FunctionExecutionAttemptModel,
        request: FunctionExecutionRequestModel,
        run: FunctionRunModel,
        function: FunctionModel,
        revision: FunctionRevisionModel,
    ) -> FunctionAttemptRuntimeContext:
        return FunctionAttemptRuntimeContext(
            attempt_id=attempt.id,
            run_id=run.id,
            fence=attempt.fence,
            operation_id=attempt.operation_id,
            deadline_at=request.deadline_at,
            revision=revision.to_entity(),
            input_data=run.input_data or {},
            config=function.config,
            user_id=run.user_id,
            user_email=run.user_email,
            pod_id=request.pod_id,
            function_id=function.id,
            function_name=function.name,
        )
