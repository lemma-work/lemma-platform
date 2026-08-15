"""Workflow run wait repository."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
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
        return wait

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

    async def list_active_older_than(
        self,
        *,
        wait_types: list[WorkflowRunWaitType],
        created_before: datetime,
        limit: int = 100,
    ) -> list[WorkflowRunWaitEntity]:
        """Stale ACTIVE waits — input for the reconciliation sweep."""
        stmt = (
            select(WorkflowRunWaitModel)
            .where(
                WorkflowRunWaitModel.status == WorkflowRunWaitStatus.ACTIVE.value,
                WorkflowRunWaitModel.wait_type.in_(
                    [wait_type.value for wait_type in wait_types]
                ),
                WorkflowRunWaitModel.created_at < created_before,
            )
            .order_by(WorkflowRunWaitModel.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]
