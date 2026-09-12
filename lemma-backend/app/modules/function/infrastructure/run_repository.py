"""Execution records for a function: creating them, finishing them, sweeping them.

Split out of ``repositories`` because that module crossed the architecture
ratchet's per-file ceiling, and because the seam is the one the services
already draw -- that file owns a function's identity and its builds, this owns
the runs it has had. The same split ``revision_repository`` made for the same
reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import load_only

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.domain.entities import (
    FunctionRunEntity,
    FunctionRunStatus,
)
from app.modules.function.domain.errors import FunctionRunNotFoundError
from app.modules.function.domain.events import FunctionRunFailedEvent
from app.modules.function.domain.ports import FunctionRunRepositoryPort
from app.modules.function.infrastructure.models import (
    FunctionRunModel,
)


def _expired_run_error(run, *, now: datetime) -> str:
    """Why a swept run failed, in the terms its reader can act on.

    The budget is stated because it is not visible anywhere else: it comes from
    a deployment-wide setting chosen by function type, so a reader looking at a
    failed run has no way to know whether it was given two minutes or ten.
    """

    started = getattr(run, "started_at", None)
    deadline = getattr(run, "deadline_at", None)
    if started is None or deadline is None:
        return (
            "Function execution deadline exceeded; the runtime never reported a result"
        )
    budget = round((deadline - started).total_seconds())
    ran_for = round((now - started).total_seconds())
    return (
        f"Function execution deadline exceeded: no result after {ran_for}s "
        f"against a {budget}s budget. The run was ended by the platform sweep, "
        "so either the function is still working or the runtime never reported "
        "back."
    )


class FunctionRunRepository(FunctionRunRepositoryPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: FunctionRunEntity) -> None:
        events = entity.collect_events()
        if events:
            self.uow.collect_events(events)

    async def create_run(self, entity: FunctionRunEntity) -> FunctionRunEntity:
        payload = entity.model_dump(exclude_unset=True)
        model = FunctionRunModel(**payload)
        self.session.add(model)
        await self.session.flush()
        self._collect_events(entity)
        return model.to_entity()

    async def update_run_and_collect(
        self, run: FunctionRunEntity, **kwargs
    ) -> FunctionRunEntity:
        """Field-update a run (like ``update_run``) and collect the entity's
        domain events into the UoW so they publish on commit.

        Used for terminal transitions so ``FunctionRunCompletedEvent`` /
        ``FunctionRunFailedEvent`` added to ``run`` are emitted after the row is
        committed. Plain ``update_run`` deliberately stays event-free for the
        many non-terminal status updates.
        """
        if run.id is None:
            raise FunctionRunNotFoundError("Cannot update a run without an id")
        model = await self.session.get(FunctionRunModel, run.id)
        if not model:
            raise FunctionRunNotFoundError(f"Run {run.id} not found")

        for key, value in kwargs.items():
            if hasattr(model, key):
                setattr(model, key, value)

        await self.session.flush()
        self._collect_events(run)
        return model.to_entity()

    async def get_run(self, run_id: UUID) -> FunctionRunEntity | None:
        stmt = select(FunctionRunModel).where(
            FunctionRunModel.id == run_id,
            # A run detached from a deleted function is history, not something
            # to execute or reconcile against. Everything asking here is asking
            # about live work -- the dispatcher, the workflow adapter, the event
            # handlers -- and "gone" is the answer each of them already handles.
            # The row keeps its record of what happened; nothing here can act on
            # it, and `FunctionRunEntity` requires the function it belonged to.
            FunctionRunModel.function_id.is_not(None),
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def fail_expired(
        self,
        *,
        now: datetime,
        limit: int = 100,
        job_callback_grace_seconds: int = 0,
    ) -> int:
        """Terminalize runs whose one allowed execution window has elapsed.

        Note the sweep is deliberately late for job-backed runs: the deadline
        plus ``job_callback_grace_seconds``, and then only at the next tick of
        the once-a-minute cron. A run can therefore be marked failed a minute or
        two after its deadline, which is the grace window doing its job and not
        a stuck sweep.
        """

        statement = (
            select(FunctionRunModel)
            # The sweep needs the deadline arithmetic, the failure event's
            # fields, and nothing else. Without this it also dragged
            # ``input_data`` and ``output_data`` -- two JSONB columns, TOASTed
            # and detoasted per row -- through a query that never reads them.
            .options(
                load_only(
                    FunctionRunModel.id,
                    FunctionRunModel.function_id,
                    FunctionRunModel.status,
                    FunctionRunModel.error,
                    FunctionRunModel.logs,
                    FunctionRunModel.started_at,
                    FunctionRunModel.deadline_at,
                    FunctionRunModel.completed_at,
                )
            )
            .where(
                or_(
                    and_(
                        FunctionRunModel.status == FunctionRunStatus.PENDING,
                        FunctionRunModel.deadline_at <= now,
                    ),
                    and_(
                        FunctionRunModel.status == FunctionRunStatus.RUNNING,
                        FunctionRunModel.job_id.is_(None),
                        FunctionRunModel.deadline_at <= now,
                    ),
                    and_(
                        FunctionRunModel.status == FunctionRunStatus.RUNNING,
                        FunctionRunModel.job_id.is_not(None),
                        FunctionRunModel.deadline_at
                        <= now - timedelta(seconds=job_callback_grace_seconds),
                    ),
                ),
                FunctionRunModel.deadline_at.is_not(None),
            )
            .order_by(FunctionRunModel.deadline_at, FunctionRunModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        runs = list((await self.session.scalars(statement)).all())
        for run in runs:
            run.status = FunctionRunStatus.FAILED
            # Says which of the two timeouts this was, and what the budget
            # actually was. The dispatcher reports its own inline timeout as
            # "Function execution timed out (deadline exceeded)"; this one is
            # the sweeper finding a run whose result never came back at all.
            # The two read identically in a run list and have opposite
            # remedies — make the function faster, versus find out why the
            # runtime never reported — so 41 failures in one afternoon said
            # "timed out" and left the reader to guess which kind.
            run.error = _expired_run_error(run, now=now)
            run.completed_at = now
            self.uow.collect_events(
                [
                    FunctionRunFailedEvent(
                        run_id=run.id,
                        function_id=run.function_id,
                        error=run.error,
                        logs=run.logs,
                        completed_at=now,
                    )
                ]
            )
        if runs:
            await self.session.flush()
        return len(runs)

    async def delete_terminal_before(
        self,
        *,
        cutoff: datetime,
        batch_size: int,
    ) -> int:
        """Remove one batch of finished runs older than ``cutoff``.

        Only terminal runs are eligible: an unfinished run is either live work
        or something the deadline sweep still has to fail, and deleting it
        would strand whatever is waiting on the result.

        ``SKIP LOCKED`` keeps this off rows another statement is already
        holding, so the sweep never blocks the execution path -- it just leaves
        those rows for the next batch.
        """

        claimed = (
            select(FunctionRunModel.id)
            .where(
                FunctionRunModel.status.in_(
                    (
                        FunctionRunStatus.COMPLETED,
                        FunctionRunStatus.FAILED,
                        FunctionRunStatus.CANCELLED,
                    )
                ),
                FunctionRunModel.created_at < cutoff,
            )
            .order_by(FunctionRunModel.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("function_run_retention_batch")
        )
        result = await self.session.execute(
            delete(FunctionRunModel).where(
                FunctionRunModel.id.in_(select(claimed.c.id))
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_pending_async_runs(
        self, *, now: datetime, min_age_seconds: int, limit: int = 100
    ) -> list[UUID]:
        """Return asynchronous runs whose one execution was never queued.

        ``job_id`` is the durable asynchronous-dispatch intent, and publication
        follows the unit of work that sets it -- so every asynchronous run is
        briefly PENDING-and-unqueued even when nothing went wrong. Republishing
        one is harmless (Streaq deduplicates the deterministic task identity)
        but claiming one is not, which is what ``min_age_seconds`` is for; the
        argument for its value lives on the constant it is passed from.
        """

        statement = (
            select(FunctionRunModel.id)
            .where(
                FunctionRunModel.status == FunctionRunStatus.PENDING,
                FunctionRunModel.job_id.is_not(None),
                FunctionRunModel.deadline_at.is_not(None),
                FunctionRunModel.deadline_at > now,
                FunctionRunModel.created_at <= now - timedelta(seconds=min_age_seconds),
            )
            .order_by(FunctionRunModel.created_at, FunctionRunModel.id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def list_runs_by_function(
        self, function_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionRunEntity], str | None]:
        statement = (
            select(FunctionRunModel)
            .options(
                load_only(
                    FunctionRunModel.id,
                    FunctionRunModel.function_id,
                    FunctionRunModel.user_id,
                    FunctionRunModel.status,
                    FunctionRunModel.started_at,
                    FunctionRunModel.completed_at,
                    FunctionRunModel.created_at,
                )
            )
            .where(FunctionRunModel.function_id == function_id)
        )
        if cursor:
            statement = statement.where(FunctionRunModel.id < UUID(cursor))
        statement = statement.order_by(FunctionRunModel.id.desc()).limit(limit + 1)

        result = await self.session.execute(statement)
        models = list(result.scalars().all())

        next_cursor = None
        if len(models) > limit:
            next_cursor = str(models[limit - 1].id)
            models = models[:limit]

        return [
            FunctionRunEntity(
                id=m.id,
                function_id=m.function_id,
                user_id=m.user_id,
                status=m.status,
                started_at=m.started_at,
                completed_at=m.completed_at,
                created_at=m.created_at,
            )
            for m in models
        ], next_cursor
