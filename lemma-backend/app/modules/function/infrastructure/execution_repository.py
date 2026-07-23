from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

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
)
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunFailedEvent,
    FunctionRunStartedEvent,
)
from app.modules.function.infrastructure.execution_claim_repository import (
    FunctionExecutionClaimRepository,
)
from app.modules.function.infrastructure.models import (
    FunctionExecutionAttemptModel,
    FunctionExecutionRequestModel,
    FunctionModel,
    FunctionRevisionModel,
    FunctionRunModel,
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
        self._claim_repository = FunctionExecutionClaimRepository(
            self.session, credential_signer
        )

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
        return await self._claim_repository.claim(
            run_id,
            worker_id=worker_id,
            total_units=total_units,
            api_reserved_units=api_reserved_units,
            lease_seconds=lease_seconds,
            timestamp=timestamp,
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
