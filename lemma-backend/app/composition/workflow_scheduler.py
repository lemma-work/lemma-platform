"""Timer adapter for workflow waits.

Arming a timer used to mean POSTing to the scheduler sidecar, which wrote a row
into APScheduler's job store. It no longer means anything: the wait row the
engine is about to persist carries `scheduled_at` and `external_ref`, and the
schedule poller claims from those columns directly. The wait row *is* the timer.

So all this does now is mint the token the two are joined by. It stays a port
rather than becoming a bare `uuid4()` at the call site because the engine should
not have to know that arming a timer is currently free -- if a future timer
needs real work again, it needs it here.
"""

from uuid import UUID, uuid4

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workflow.domain.ports import SchedulePort


class ScheduleControlAdapter(SchedulePort):
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        del uow

    async def schedule_workflow_wake(
        self,
        run_id: UUID,
        scheduled_at: str,
        pod_id: UUID,
        user_id: UUID,
    ) -> UUID:
        del run_id, scheduled_at, pod_id, user_id
        return uuid4()
