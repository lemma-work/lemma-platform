"""Workflow run wait repository."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.workflow.domain.ports import WorkflowRunWaitRepository
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitStatus,
    WorkflowRunWaitType,
)
from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel


class SqlAlchemyWorkflowRunWaitRepository(WorkflowRunWaitRepository):
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.session: AsyncSession = uow.session

    def _to_entity(self, model: WorkflowRunWaitModel) -> WorkflowRunWaitEntity:
        return model.to_entity()

    async def create(self, wait: WorkflowRunWaitEntity) -> WorkflowRunWaitEntity:
        model = WorkflowRunWaitModel(
            id=wait.id,
            run_id=wait.run_id,
            flow_id=wait.flow_id,
            pod_id=wait.pod_id,
            node_id=wait.node_id,
            wait_type=wait.wait_type.value,
            status=wait.status.value,
            assigned_pod_member_id=wait.assigned_pod_member_id,
            external_ref=wait.external_ref,
            # Persisted rather than dropped. `WaitRequest.scheduled_at` has
            # existed and been populated by the WAIT_UNTIL executor all along;
            # this repository never wrote it, so the only durable copy was the
            # one inside `payload`.
            scheduled_at=wait.scheduled_at,
            payload=wait.payload,
            completed_at=wait.completed_at,
        )
        self.session.add(model)
        await self.session.flush()
        return self._to_entity(model)

    async def update(self, wait: WorkflowRunWaitEntity) -> WorkflowRunWaitEntity:
        stmt = (
            update(WorkflowRunWaitModel)
            .where(WorkflowRunWaitModel.id == wait.id)
            .values(
                status=wait.status.value,
                payload=wait.payload,
                completed_at=wait.completed_at,
            )
        )
        await self.session.execute(stmt)
        if wait.status is not WorkflowRunWaitStatus.ACTIVE:
            # Its expiry would only be claimed to find the wait resolved.
            from app.modules.workflow.execution.wait_work import cancel_wait_expire

            await cancel_wait_expire(
                WorkWriter(self.session), pod_id=wait.pod_id, wait_id=wait.id
            )
        return wait

    async def get(self, wait_id: UUID) -> WorkflowRunWaitEntity | None:
        model = await self.session.get(WorkflowRunWaitModel, wait_id)
        return self._to_entity(model) if model else None

    async def get_active_for_run(
        self,
        run_id: UUID,
    ) -> WorkflowRunWaitEntity | None:
        stmt = (
            select(WorkflowRunWaitModel)
            .where(
                WorkflowRunWaitModel.run_id == run_id,
                WorkflowRunWaitModel.status == WorkflowRunWaitStatus.ACTIVE.value,
            )
            .order_by(WorkflowRunWaitModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def active_for_runs(
        self, run_ids: Sequence[UUID]
    ) -> dict[UUID, WorkflowRunWaitEntity]:
        """The active wait of each run in a page, keyed by run id -- one
        statement for the page, on `ix_workflow_run_waits_run_status`.

        A run holds at most one active wait; should two ever coexist, the
        newest wins, as it does in `get_active_for_run`.
        """
        if not run_ids:
            return {}
        stmt = (
            select(WorkflowRunWaitModel)
            .where(
                WorkflowRunWaitModel.run_id.in_(list(run_ids)),
                WorkflowRunWaitModel.status == WorkflowRunWaitStatus.ACTIVE.value,
            )
            .order_by(WorkflowRunWaitModel.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return {model.run_id: self._to_entity(model) for model in result.scalars()}

    async def find_active_by_external_ref(
        self,
        wait_type: WorkflowRunWaitType,
        external_ref: str,
    ) -> WorkflowRunWaitEntity | None:
        stmt = (
            select(WorkflowRunWaitModel)
            .where(
                WorkflowRunWaitModel.wait_type == wait_type.value,
                WorkflowRunWaitModel.external_ref == str(external_ref),
                WorkflowRunWaitModel.status == WorkflowRunWaitStatus.ACTIVE.value,
            )
            .order_by(WorkflowRunWaitModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def list_active_for_assignee(
        self,
        *,
        pod_id: UUID,
        assigned_pod_member_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[WorkflowRunWaitEntity], UUID | None]:
        stmt = (
            select(WorkflowRunWaitModel)
            .where(
                WorkflowRunWaitModel.pod_id == pod_id,
                WorkflowRunWaitModel.assigned_pod_member_id == assigned_pod_member_id,
                WorkflowRunWaitModel.status == WorkflowRunWaitStatus.ACTIVE.value,
            )
            .order_by(WorkflowRunWaitModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(WorkflowRunWaitModel.id > cursor)
        result = await self.session.execute(stmt)
        models = list(result.scalars().all())

        next_cursor = None
        if len(models) > limit:
            next_cursor = models[limit - 1].id
            models = models[:limit]

        return [self._to_entity(m) for m in models], next_cursor

    async def oldest_queued(
        self, flow_id: UUID, *, limit: int
    ) -> list[WorkflowRunWaitEntity]:
        """Runs of a workflow waiting for a place, first come first served."""
        model = WorkflowRunWaitModel
        stmt = (
            select(model)
            .where(
                model.flow_id == flow_id,
                model.wait_type == WorkflowRunWaitType.QUEUED.value,
                model.status == WorkflowRunWaitStatus.ACTIVE.value,
            )
            .order_by(model.created_at, model.id)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_active_older_than(
        self,
        *,
        wait_types: list[WorkflowRunWaitType],
        created_before: datetime,
        limit: int = 100,
        after: tuple[datetime, UUID] | None = None,
        human_created_before: datetime | None = None,
        timers_due_by: datetime | None = None,
    ) -> list[WorkflowRunWaitEntity]:
        """Stale ACTIVE waits — input for the reconciliation sweep.

        One page, oldest first, after the ``(created_at, id)`` of the last
        page. The oldest waits are the ones the sweep can least act on -- a
        form nobody has answered, an agent that is healthily waiting -- so a
        sweep that read only the first page re-read them every tick and never
        reached a newer wait whose completion was lost.

        ``human_created_before`` and ``timers_due_by`` leave out forms short of
        their ceiling and timers not yet due: there is nothing to do for either.
        """
        model = WorkflowRunWaitModel
        stmt = (
            select(model)
            .where(
                model.status == WorkflowRunWaitStatus.ACTIVE.value,
                model.wait_type.in_([wait_type.value for wait_type in wait_types]),
                model.created_at < created_before,
            )
            .order_by(model.created_at, model.id)
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(tuple_(model.created_at, model.id) > after)
        if human_created_before is not None:
            stmt = stmt.where(
                or_(
                    model.wait_type != WorkflowRunWaitType.HUMAN.value,
                    model.created_at < human_created_before,
                )
            )
        if timers_due_by is not None:
            stmt = stmt.where(
                or_(
                    model.wait_type != WorkflowRunWaitType.TIME.value,
                    model.scheduled_at.is_(None),
                    model.scheduled_at <= timers_due_by,
                )
            )
        result = await self.session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]
