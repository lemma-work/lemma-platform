"""Scheduler timer adapter for workflow waits."""

from datetime import datetime
from uuid import UUID, uuid4

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.scheduler.api_client import SchedulerAPIClient
from app.modules.workflow.domain.ports import SchedulePort


class ScheduleControlAdapter(SchedulePort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        del uow
        self.scheduler = SchedulerAPIClient()

    async def schedule_workflow_wake(
        self,
        run_id: UUID,
        scheduled_at: str,
        pod_id: UUID,
        user_id: UUID,
    ) -> UUID:
        del pod_id, user_id
        timer_id = uuid4()
        await self.scheduler.schedule_once_job(
            schedule_id=timer_id,
            run_date=datetime.fromisoformat(scheduled_at),
            payload={
                "workflow_run_id": str(run_id),
                "wait_ref": str(timer_id),
                "scheduled_at": scheduled_at,
                "source": "workflow_wait_until",
            },
            replace_existing=True,
        )
        return timer_id
