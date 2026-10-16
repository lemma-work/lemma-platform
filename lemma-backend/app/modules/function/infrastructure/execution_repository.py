"""Persistence for one public function run and exactly one execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
from uuid import UUID

from sqlalchemy import func, select, text

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.function.config import execution_budget_seconds
from app.modules.function.contracts.run_facts import FunctionRunFinished
from app.modules.function.application.dispatch_failures import PodAtCapacity
from app.modules.function.domain.run_deadline import expired_run_error
from app.modules.function.events.run_deadline import add_function_deadline
from app.modules.function.events.run_work import add_function_cancel
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
    FunctionType,
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

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

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

    async def start_execution(
        self,
        dispatch: FunctionExecutionDispatch,
        now: datetime | None = None,
        *,
        max_running_per_pod: int | None = None,
    ) -> FunctionRunRuntimeContext | None:
        """Atomically move the backend-authorized run into RUNNING.

        The runtime is admitted through the sandbox and receives the complete
        immutable context after this transition; it never claims the run back.

        ``max_running_per_pod`` is the pod's gate: its RUNNING runs are counted
        under a lock on the pod, so two starts cannot both take the last slot,
        and a full pod raises ``PodAtCapacity`` with the run left PENDING. The
        same transaction writes the ``function.deadline`` item that ends the
        run if nothing else has by its deadline.
        """

        timestamp = now or datetime.now(timezone.utc)
        rows = await self._run_and_function(dispatch.run_id, for_update=True)
        if rows is None:
            return None
        run, function = rows
        if (
            run.status != FunctionRunStatus.PENDING
            or run.deadline_at is None
            or run.deadline_at <= timestamp
            or run.revision_hash != dispatch.revision_hash
            or (run.input_data or {}) != dispatch.input_data
            or run.user_id != dispatch.user_id
            or run.contact_id != dispatch.contact_id
            or function.pod_id != dispatch.pod_id
            or run.function_id != dispatch.function_id
        ):
            return None

        if max_running_per_pod is not None:
            await self._lock_pod(function.pod_id)
            if await self.running_on_pod(function.pod_id) >= max_running_per_pod:
                raise PodAtCapacity(str(function.pod_id))

        run.status = FunctionRunStatus.RUNNING
        run.started_at = timestamp
        # The execution budget starts now, not when the run was created: time
        # spent queued, waiting for a pod slot, or on a cold start is not the
        # function's to lose.
        run.deadline_at = timestamp + timedelta(seconds=dispatch.execution_seconds)
        await self.session.flush()
        await add_function_deadline(
            self.session,
            pod_id=function.pod_id,
            run_id=run.id,
            deadline_at=run.deadline_at,
        )
        return self._runtime_context(run, function)

    async def running_on_pod(self, pod_id: UUID) -> int:
        """How many of the pod's runs hold a slot now."""
        count = await self.session.scalar(
            select(func.count())
            .select_from(FunctionRunModel)
            .join(FunctionModel, FunctionModel.id == FunctionRunModel.function_id)
            .where(
                FunctionModel.pod_id == pod_id,
                FunctionRunModel.status == FunctionRunStatus.RUNNING,
            )
        )
        return int(count or 0)

    async def _lock_pod(self, pod_id: UUID) -> None:
        # Transaction-scoped: released by the commit that makes this start
        # visible, so the next start on the pod counts it.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"function-pod:{pod_id}"},
        )
        mark_transaction_scoped_lock(self.session)

    async def authorized_runtime_context(
        self,
        run_id: UUID,
        principal: FunctionSessionPrincipal,
        *,
        delegated_tokens_enabled: bool,
    ) -> FunctionRunRuntimeContext | None:
        rows = await self._run_and_function(run_id)
        if rows is None:
            return None
        run, function = rows
        # Synchronous API runs return their terminal report on the invocation
        # response. Only durable JOB runs may complete through this callback
        # authorization path.
        if run.job_id is None:
            return None
        if (
            run.status != FunctionRunStatus.RUNNING
            and run.status not in TERMINAL_RUN_STATES
        ) or run.revision_hash is None:
            return None
        if not self._principal_matches(
            principal,
            user_id=run.user_id,
            pod_id=function.pod_id,
            function_id=run.function_id,
            function_name=function.name,
            revision_hash=run.revision_hash,
            delegated_tokens_enabled=delegated_tokens_enabled,
            run_id=run.id,
        ):
            return None
        return self._runtime_context(run, function)

    async def artifact_generation(
        self, function_id: UUID, revision_hash: str
    ) -> UUID | None:
        from app.modules.function.infrastructure.models import FunctionRevisionModel

        return (
            await self.session.execute(
                select(FunctionRevisionModel.generation)
                .where(
                    FunctionRevisionModel.function_id == function_id,
                    FunctionRevisionModel.revision_hash == revision_hash,
                    FunctionRevisionModel.pruned_at.is_(None),
                )
                .order_by(FunctionRevisionModel.revision_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def authorize_definition_artifact(
        self,
        function_id: UUID,
        revision_hash: str,
        principal: FunctionSessionPrincipal,
        *,
        delegated_tokens_enabled: bool,
    ) -> bool:
        function = await self.session.scalar(
            select(FunctionModel).where(FunctionModel.id == function_id)
        )
        if function is None:
            return False
        return self._principal_matches(
            principal,
            user_id=principal.user_id,
            pod_id=function.pod_id,
            function_id=function.id,
            function_name=function.name,
            revision_hash=revision_hash,
            delegated_tokens_enabled=delegated_tokens_enabled,
        )

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
            FunctionRunStatus.COMPLETED if completed else FunctionRunStatus.FAILED
        )
        run.output_data = output_data if completed else None
        run.error = error
        run.logs = logs
        run.completed_at = timestamp
        await self.session.flush()
        await self._announce_finished(run, pod_id=context.pod_id)
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

    async def fail_in_flight_on_pod(
        self,
        pod_id: UUID,
        *,
        started_before: datetime,
        error: str,
        limit: int,
    ) -> list[UUID]:
        """Fail up to ``limit`` asynchronous runs a destroyed pod sandbox was
        executing, oldest first.

        Only job-backed runs: a synchronous one holds its own connection to
        the sandbox and fails through it. Rows another transaction holds are
        skipped -- that holder is settling the run already.
        """
        runs = (
            await self.session.scalars(
                select(FunctionRunModel)
                .join(FunctionModel, FunctionModel.id == FunctionRunModel.function_id)
                .where(
                    FunctionModel.pod_id == pod_id,
                    FunctionRunModel.status == FunctionRunStatus.RUNNING,
                    FunctionRunModel.job_id.is_not(None),
                    FunctionRunModel.started_at <= started_before,
                )
                .order_by(FunctionRunModel.started_at, FunctionRunModel.id)
                .limit(limit)
                .with_for_update(of=FunctionRunModel, skip_locked=True)
            )
        ).all()
        for run in runs:
            self._mark_finished(run, run_status=FunctionRunStatus.FAILED, error=error)
        if runs:
            await self.session.flush()
        for run in runs:
            await self._announce_finished(run, pod_id=pod_id)
        return [run.id for run in runs]

    async def fail_pending(self, run_id: UUID, *, error: str) -> bool:
        """Fail a run that never started; a started or ended one is left alone."""
        rows = await self._run_and_function(run_id, for_update=True)
        if rows is None or rows[0].status != FunctionRunStatus.PENDING:
            return False
        run, function = rows
        self._mark_finished(run, run_status=FunctionRunStatus.FAILED, error=error)
        await self.session.flush()
        await self._announce_finished(run, pod_id=function.pod_id)
        return True

    async def cancel_open(self, run_id: UUID, *, error: str) -> bool:
        """Cancel a run nobody is waiting for any more, in this transaction.

        A run that had started gets a ``function.cancel`` item too, so its
        sandbox is asked to stop once this commits; the cancellation itself
        does not wait for that.
        """
        rows = await self._run_and_function(run_id, for_update=True)
        if rows is None or rows[0].status in TERMINAL_RUN_STATES:
            return False
        run, function = rows
        was_running = run.status == FunctionRunStatus.RUNNING
        self._mark_finished(run, run_status=FunctionRunStatus.CANCELLED, error=error)
        await self.session.flush()
        await self._announce_finished(run, pod_id=function.pod_id)
        if was_running:
            await add_function_cancel(
                self.session, pod_id=function.pod_id, run_id=run.id
            )
        return True

    async def fail_if_overdue(
        self, run_id: UUID, *, now: datetime, grace: timedelta
    ) -> datetime | None:
        """Fail the run if its window closed more than ``grace`` ago.

        Returns when to look again for a run whose window is still open -- its
        deadline moves when it starts -- and None otherwise.
        """
        rows = await self._run_and_function(run_id, for_update=True)
        if rows is None:
            return None
        run, function = rows
        if run.status in TERMINAL_RUN_STATES or run.deadline_at is None:
            return None
        due = run.deadline_at + grace
        if now < due:
            return due
        self._mark_finished(
            run,
            run_status=FunctionRunStatus.FAILED,
            error=expired_run_error(
                started_at=run.started_at, deadline_at=run.deadline_at, now=now
            ),
        )
        await self.session.flush()
        await self._announce_finished(run, pod_id=function.pod_id)
        return None

    async def dispatch_of(
        self, run_id: UUID, *, mode: FunctionDispatchMode
    ) -> FunctionExecutionDispatch | None:
        """The run's dispatch whatever its state -- enough to reach its sandbox."""
        rows = await self._run_and_function(run_id)
        if rows is None:
            return None
        run, function = rows
        if run.deadline_at is None or run.revision_hash is None:
            return None
        return self._dispatch(run, function, mode=mode)

    async def _finish_without_result(
        self,
        run_id: UUID,
        *,
        run_status: FunctionRunStatus,
        error: str,
    ) -> FunctionRunEntity | None:
        rows = await self._run_and_function(run_id, for_update=True)
        if rows is None:
            return None
        run, function = rows
        if run.status in TERMINAL_RUN_STATES:
            return run.to_entity()

        self._mark_finished(run, run_status=run_status, error=error)
        await self.session.flush()
        await self._announce_finished(run, pod_id=function.pod_id)
        return run.to_entity()

    def _mark_finished(
        self,
        run: FunctionRunModel,
        *,
        run_status: FunctionRunStatus,
        error: str,
    ) -> None:
        run.status = run_status
        run.error = error
        run.completed_at = datetime.now(timezone.utc)

    async def _announce_finished(self, run: FunctionRunModel, *, pod_id: UUID) -> None:
        """Every terminal transition says so, in its own transaction.

        Whatever waits on the run -- a workflow step -- gets its work item
        committed with the outcome, so nothing can be lost between the two.
        """
        await facts.emit(
            self.uow,
            FunctionRunFinished(
                function_run_id=run.id,
                pod_id=pod_id,
                function_id=run.function_id,
                status=FunctionRunStatus(run.status).value,
            ),
        )

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
            user_email=run.user_email,
            contact_id=run.contact_id,
            config=function.config,
            mode=mode,
            deadline_at=run.deadline_at,
            execution_seconds=execution_budget_seconds(
                is_job=function.type == FunctionType.JOB
            ),
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
            contact_id=run.contact_id,
            pod_id=function.pod_id,
            function_id=function.id,
            function_name=function.name,
        )

    @staticmethod
    def _principal_matches(
        principal: FunctionSessionPrincipal,
        *,
        user_id: UUID | None,
        pod_id: UUID,
        function_id: UUID,
        function_name: str,
        revision_hash: str,
        delegated_tokens_enabled: bool,
        run_id: UUID | None = None,
    ) -> bool:
        if principal.pod_id != pod_id or principal.function_id != function_id:
            return False
        if principal.run_id is not None:
            # A function-run token was minted for one run of one revision. It
            # fetches that revision's code and reports that run's end, and
            # nothing else -- in particular not a member's run of the same
            # function, which ``user_id`` being set marks.
            return (
                user_id is None
                and principal.revision_hash == revision_hash
                and (run_id is None or principal.run_id == run_id)
            )
        if user_id is None or principal.user_id != user_id:
            return False
        if principal.session_id is None:
            return False
        expected_session_id = FunctionSessionTokenKey(
            user_id=user_id,
            pod_id=pod_id,
            function_id=function_id,
            revision_hash=revision_hash,
            # Use the signed actor name when present so an in-flight token
            # remains valid if the mutable function name changes.
            workload_name=principal.actor_name or function_name,
            scope=principal.scope,
            delegated_tokens_enabled=delegated_tokens_enabled,
        ).session_id
        return hmac.compare_digest(expected_session_id, principal.session_id)
