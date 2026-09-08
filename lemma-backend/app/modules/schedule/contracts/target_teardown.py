"""What deleting a schedule's target does to the schedules pointing at it.

A schedule fires at one of two things: a workflow or an agent. Deleting either
used to leave its schedules behind, armed. PS-SCHED-030 asks for the dangling
target to be *prevented* rather than reported, and the reason is in the
scenario that pins it: a schedule left pointing at nothing fires on a timer
forever with nobody able to see why it did nothing.

Shaped like `pod_teardown.py` and for the same reason -- the consumer states
what it needs as a `Protocol` in its own domain, this satisfies it structurally
without either module naming the other's internals.

Unlike the pod path this removes rather than disarms, and does it inline. A pod
holds an unbounded number of webhook schedules and each teardown is a Composio
round trip, so doing that inside the deleting request's transaction was the
wrong trade and pod defers it to an event. One workflow or agent holds the
handful of schedules its author pointed at it, and the promise here is that the
schedule is gone when the delete returns -- not shortly afterwards.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
service layer.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.schedule.api.dependencies import get_webhook_source_registry
from app.modules.schedule.services import schedule_cleanup
from app.modules.schedule.services.schedule_service import ScheduleService


class _TargetScheduleTeardown:
    """The schedule module's per-target cleanup, seen through a consumer's port."""

    def __init__(self, uow: object):
        self._uow = uow

    def _service(self) -> ScheduleService:
        # The same construction every other creator of a schedule reaches, so a
        # WEBHOOK schedule is torn down against the sources this deployment
        # accepts rather than a narrower set assembled here.
        return ScheduleService(
            uow=self._uow, webhook_sources=get_webhook_source_registry()
        )

    async def remove_all_for_workflow(self, workflow_id: UUID) -> int:
        return await schedule_cleanup.delete_all_for_workflow(
            self._service(), workflow_id
        )

    async def remove_all_for_agent(self, agent_id: UUID) -> int:
        return await schedule_cleanup.delete_all_for_agent(self._service(), agent_id)


def create_target_schedule_teardown(uow: object) -> _TargetScheduleTeardown:
    """Satisfies a consumer's target-schedule-teardown port."""
    return _TargetScheduleTeardown(uow)


__all__ = ["create_target_schedule_teardown"]
