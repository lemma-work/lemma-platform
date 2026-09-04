"""Arming the timer a suspended workflow is woken by.

Arming a timer used to mean POSTing to the scheduler sidecar, which wrote a row
into APScheduler's job store. It no longer means anything: the wait row the
engine is about to persist carries `scheduled_at` and `external_ref`, and the
schedule poller claims from those columns directly. The wait row *is* the timer.

So all this does now is mint the token the two are joined by. It stays a port
rather than becoming a bare `uuid4()` at the call site because the engine should
not have to know that arming a timer is currently free -- if a future timer
needs real work again, it needs it here.

It lived in `app/composition/workflow_scheduler.py`, the last remnant of the
sidecar binding, and by then it bound nothing: it names no other module, and the
`SqlAlchemyUnitOfWork` its constructor took went straight into a `del`. What the
composition root was wiring was workflow to itself, at the cost of one of the
edges that make the root a shared middle layer.
"""

from uuid import UUID, uuid4

from app.modules.workflow.domain.ports import SchedulePort


class WaitRowTimer(SchedulePort):
    """The wait row is the timer; this mints the token it is claimed by."""

    async def schedule_workflow_wake(
        self,
        run_id: UUID,
        scheduled_at: str,
        pod_id: UUID,
        user_id: UUID,
    ) -> UUID:
        del run_id, scheduled_at, pod_id, user_id
        return uuid4()
