"""Schedule run-history and manual-redrive application service."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.errors import (
    ScheduleNotFoundError,
    ScheduleRunNotRetryableError,
)
from app.modules.schedule.domain.interfaces import (
    DatastoreSchedulePolicy,
    ScheduleRepository,
)
from app.modules.schedule.domain.schedule import DatastoreScheduleConfig, ScheduleType
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)


class ScheduleRunService:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        schedule_repository: ScheduleRepository,
        datastore_policy: DatastoreSchedulePolicy,
    ) -> None:
        self.uow = uow
        self.schedule_repository = schedule_repository
        self.datastore_policy = datastore_policy
        self.run_repository = ScheduleRunRepository(uow)

    async def list_schedule_runs(
        self, *, pod_id: UUID, schedule_id: UUID, ctx: Context, limit: int
    ):
        schedule = await self.schedule_repository.get(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            raise ScheduleNotFoundError()
        await ctx.require(
            Permissions.SCHEDULE_READ, ResourceRef.schedule(pod_id, schedule_id)
        )
        run_user_id = await self._visible_run_user_id(
            schedule=schedule, pod_id=pod_id, ctx=ctx
        )
        return await self.run_repository.list_for_schedule(
            schedule_id, limit=limit, user_id=run_user_id
        )

    async def _visible_run_user_id(
        self, *, schedule, pod_id: UUID, ctx: Context
    ) -> UUID | None:
        if schedule.schedule_type == ScheduleType.DATASTORE:
            try:
                config = DatastoreScheduleConfig(**schedule.config)
                can_view_all = await self.datastore_policy.can_view_all_runs(
                    pod_id=pod_id, table_name=config.table_name, ctx=ctx
                )
            except ValueError:
                can_view_all = False
            if not can_view_all:
                return ctx.user_id
        return None

    async def retry_schedule_run(
        self, *, pod_id: UUID, schedule_id: UUID, run_id: UUID, ctx: Context
    ):
        schedule = await self.schedule_repository.get(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            raise ScheduleNotFoundError()
        await ctx.require(
            Permissions.SCHEDULE_UPDATE, ResourceRef.schedule(pod_id, schedule_id)
        )
        required_user_id = await self._visible_run_user_id(
            schedule=schedule, pod_id=pod_id, ctx=ctx
        )
        result = await self.run_repository.create_redrive(
            schedule_id=schedule_id,
            run_id=run_id,
            redriven_by_user_id=ctx.user_id,
            fallback_user_id=schedule.user_id,
            required_user_id=required_user_id,
        )
        if result is None:
            raise ScheduleRunNotRetryableError()
        schedule_run, created = result
        if created:
            self.uow.collect_events(
                [
                    ScheduleFired(
                        schedule_id=schedule.id,
                        user_id=schedule_run.user_id,
                        schedule_type=schedule.schedule_type,
                        pod_id=schedule.pod_id,
                        account_id=schedule.account_id,
                        payload=schedule_run.payload,
                        metadata=schedule_run.metadata,
                        llm_output=schedule_run.llm_output,
                        scheduled_at=schedule_run.source_occurred_at,
                        source_event_id=schedule_run.source_event_id,
                        causation_id=schedule_run.id,
                    )
                ]
            )
        return schedule_run
