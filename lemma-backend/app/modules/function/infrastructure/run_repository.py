"""Execution records for a function: creating them, finishing them, sweeping them.

Split out of ``repositories`` because that module crossed the architecture
ratchet's per-file ceiling, and because the seam is the one the services
already draw -- that file owns a function's identity and its builds, this owns
the runs it has had. The same split ``revision_repository`` made for the same
reason.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import load_only

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.domain.entities import (
    FunctionRunEntity,
    FunctionRunStatus,
)
from app.modules.function.domain.errors import FunctionRunNotFoundError
from app.modules.function.domain.ports import FunctionRunRepositoryPort
from app.modules.function.events.run_deadline import add_function_deadline
from app.modules.function.events.run_work import add_function_run_work
from app.modules.function.infrastructure.models import (
    FunctionModel,
    FunctionRunModel,
)


class FunctionRunRepository(FunctionRunRepositoryPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
    ):
        self.uow = uow
        self.session = uow.session

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
        await self._schedule(model)
        return model.to_entity()

    async def _schedule(self, run: FunctionRunModel) -> None:
        """The work that settles this run, committed with it.

        A queued run gets ``function.run``, which starts it. A synchronous run
        is started by the request that made it, so it gets only the deadline
        item, which fails it if that request dies before it ends -- the start
        writes another for the execution deadline.
        """
        pod_id = await self.session.scalar(
            select(FunctionModel.pod_id).where(FunctionModel.id == run.function_id)
        )
        if pod_id is None:
            return
        if run.job_id is not None:
            await add_function_run_work(self.session, pod_id=pod_id, run_id=run.id)
        elif run.deadline_at is not None:
            await add_function_deadline(
                self.session, pod_id=pod_id, run_id=run.id, deadline_at=run.deadline_at
            )

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
                    FunctionRunModel.contact_id,
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
                contact_id=m.contact_id,
                status=m.status,
                started_at=m.started_at,
                completed_at=m.completed_at,
                created_at=m.created_at,
            )
            for m in models
        ], next_cursor
