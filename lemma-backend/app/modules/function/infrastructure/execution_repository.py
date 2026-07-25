"""Persistence for one public function run and exactly one execution."""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.application.function_runtime_credentials import (
    FunctionRuntimeCapabilitySigner,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenKey,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunRuntimeContext,
    FunctionRunStatus,
    FunctionSessionPrincipal,
)
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunFailedEvent,
    FunctionRunStartedEvent,
)
from app.modules.function.domain.types import JsonObject
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel


TERMINAL_RUN_STATES = {
    FunctionRunStatus.COMPLETED,
    FunctionRunStatus.FAILED,
    FunctionRunStatus.CANCELLED,
}


class FunctionExecutionRepository:
    """Own the durable state transitions carried by ``function_runs`` itself."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        credential_signer: FunctionRuntimeCapabilitySigner,
    ) -> None:
        self.uow = uow
        self.session = uow.session
        self._credential_signer = credential_signer

    async def resolve_dispatch(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionExecutionDispatch | FunctionRunEntity | None:
        rows = await self._run_and_function(run_id)
        if rows is None:
            return None
        run, function = rows
        if run.status != FunctionRunStatus.PENDING:
            return run.to_entity()
        return self._dispatch(run, function, mode=mode)

    async def active_dispatch(
        self,
        run_id: UUID,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionExecutionDispatch | None:
        rows = await self._run_and_function(run_id)
        if rows is None:
            return None
        run, function = rows
        if run.status in TERMINAL_RUN_STATES:
            return None
        return self._dispatch(run, function, mode=mode)

    async def claim_execution(
        self,
        run_id: UUID,
        principal: FunctionSessionPrincipal,
        *,
        revision_hash: str,
        input_data: JsonObject,
        delegated_tokens_enabled: bool,
        now: datetime | None = None,
    ) -> FunctionRunRuntimeContext | None:
        timestamp = now or datetime.now(timezone.utc)
        rows = await self._run_and_function(run_id, for_update=True)
        if rows is None:
            return None
        run, function = rows
        if (
            run.status != FunctionRunStatus.PENDING
            or run.deadline_at is None
            or run.deadline_at <= timestamp
            or run.revision_hash != revision_hash
            or (run.input_data or {}) != input_data
            or run.user_id != principal.user_id
            or function.pod_id != principal.pod_id
            or run.function_id != principal.function_id
        ):
            return None
        expected_session_id = FunctionSessionTokenKey(
            user_id=run.user_id,
            pod_id=function.pod_id,
            function_id=run.function_id,
            revision_hash=revision_hash,
            workload_name=function.name,
            scope=(),
            delegated_tokens_enabled=delegated_tokens_enabled,
        ).session_id
        if not hmac.compare_digest(expected_session_id, principal.session_id):
            return None

        run.status = FunctionRunStatus.RUNNING
        run.started_at = timestamp
        self.uow.collect_events(
            [
                FunctionRunStartedEvent(
                    run_id=run.id,
                    function_id=run.function_id,
                    started_at=timestamp,
                    user_email=run.user_email,
                )
            ]
        )
        await self.session.flush()
        return self._runtime_context(run, function)

    async def runtime_context(
        self,
        run_id: UUID,
        callback_token: str,
    ) -> FunctionRunRuntimeContext | None:
        expected = self._credential_signer.derive(run_id)
        if not hmac.compare_digest(expected, callback_token):
            return None
        rows = await self._run_and_function(run_id)
        if rows is None:
            return None
        run, function = rows
        if (
            run.status != FunctionRunStatus.RUNNING
            and run.status not in TERMINAL_RUN_STATES
        ):
            return None
        return self._runtime_context(run, function)

    async def active_runtime_context(
        self,
        run_id: UUID,
        callback_token: str,
        *,
        now: datetime | None = None,
    ) -> FunctionRunRuntimeContext | None:
        """Authorize reads that are useful only while the exact run is active.

        Callback credentials are restart-stable so a terminal report can be
        acknowledged idempotently. That property must not turn the artifact
        endpoint into an indefinitely valid download capability.
        """

        expected = self._credential_signer.derive(run_id)
        if not hmac.compare_digest(expected, callback_token):
            return None
        rows = await self._run_and_function(run_id)
        if rows is None:
            return None
        run, function = rows
        timestamp = now or datetime.now(timezone.utc)
        if (
            run.status != FunctionRunStatus.RUNNING
            or run.deadline_at is None
            or run.deadline_at <= timestamp
        ):
            return None
        return self._runtime_context(run, function)

    async def complete(
        self,
        context: FunctionRunRuntimeContext,
        *,
        completed: bool,
        output_data: JsonObject | None,
        error: str | None,
        logs: str | None,
        now: datetime | None = None,
    ) -> tuple[FunctionRunEntity | None, bool, bool]:
        timestamp = now or datetime.now(timezone.utc)
        run = await self.session.scalar(
            select(FunctionRunModel)
            .where(FunctionRunModel.id == context.run_id)
            .with_for_update()
        )
        if run is None:
            return None, False, False
        if run.status in TERMINAL_RUN_STATES:
            return run.to_entity(), True, True
        if run.status != FunctionRunStatus.RUNNING:
            return run.to_entity(), False, False

        run.status = (
            FunctionRunStatus.COMPLETED
            if completed
            else FunctionRunStatus.FAILED
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
            )
            if completed
            else FunctionRunFailedEvent(
                run_id=run.id,
                function_id=run.function_id,
                error=error,
                logs=logs,
                completed_at=timestamp,
            )
        )
        self.uow.collect_events([event])
        await self.session.flush()
        return run.to_entity(), True, False

    async def fail_dispatch(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        error: str,
    ) -> FunctionRunEntity | None:
        return await self._finish_without_result(
            dispatch.run_id,
            run_status=FunctionRunStatus.FAILED,
            error=error,
        )

    async def cancel_dispatch(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        error: str = "Function execution was cancelled",
    ) -> FunctionRunEntity | None:
        return await self._finish_without_result(
            dispatch.run_id,
            run_status=FunctionRunStatus.CANCELLED,
            error=error,
        )

    async def fail_unfinished(
        self,
        run_id: UUID,
        *,
        error: str,
    ) -> FunctionRunEntity | None:
        return await self._finish_without_result(
            run_id,
            run_status=FunctionRunStatus.FAILED,
            error=error,
        )

    async def _finish_without_result(
        self,
        run_id: UUID,
        *,
        run_status: FunctionRunStatus,
        error: str,
    ) -> FunctionRunEntity | None:
        run = await self.session.scalar(
            select(FunctionRunModel)
            .where(FunctionRunModel.id == run_id)
            .with_for_update()
        )
        if run is None:
            return None
        if run.status in TERMINAL_RUN_STATES:
            return run.to_entity()

        timestamp = datetime.now(timezone.utc)
        run.status = run_status
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
                )
            ]
        )
        await self.session.flush()
        return run.to_entity()

    async def _run_and_function(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[FunctionRunModel, FunctionModel] | None:
        statement = (
            select(FunctionRunModel, FunctionModel)
            .join(
                FunctionModel,
                FunctionModel.id == FunctionRunModel.function_id,
            )
            .where(FunctionRunModel.id == run_id)
        )
        if for_update:
            statement = statement.with_for_update(of=FunctionRunModel)
        row = (await self.session.execute(statement)).one_or_none()
        return tuple(row) if row is not None else None

    @staticmethod
    def _dispatch(
        run: FunctionRunModel,
        function: FunctionModel,
        *,
        mode: FunctionDispatchMode,
    ) -> FunctionExecutionDispatch:
        if run.deadline_at is None or run.revision_hash is None:
            raise RuntimeError("function run is missing immutable execution state")
        return FunctionExecutionDispatch(
            run_id=run.id,
            pod_id=function.pod_id,
            function_id=function.id,
            function_name=function.name,
            user_id=run.user_id,
            mode=mode,
            deadline_at=run.deadline_at,
            revision_hash=run.revision_hash,
            input_data=run.input_data or {},
        )

    @staticmethod
    def _runtime_context(
        run: FunctionRunModel,
        function: FunctionModel,
    ) -> FunctionRunRuntimeContext:
        if run.deadline_at is None or run.revision_hash is None:
            raise RuntimeError("function run is missing immutable execution state")
        return FunctionRunRuntimeContext(
            run_id=run.id,
            deadline_at=run.deadline_at,
            revision_hash=run.revision_hash,
            artifact_path=(
                f"artifacts/{run.revision_hash.removeprefix('sha256:')}.zip"
            ),
            input_data=run.input_data or {},
            config=function.config,
            user_id=run.user_id,
            user_email=run.user_email,
            pod_id=function.pod_id,
            function_id=function.id,
            function_name=function.name,
        )
