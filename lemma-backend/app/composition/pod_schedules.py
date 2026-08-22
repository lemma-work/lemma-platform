"""Bind pod deletion to the schedule module's teardown.

Pod states what it needs as :class:`PodScheduleTeardownPort` in its own domain
and never names the schedule module; the binding lives here, with every other
cross-module wiring. Importing ``app.modules.schedule`` from ``app.modules.pod``
would put a forbidden edge -- and a ``datastore -> pod -> schedule`` cycle --
into the dependency graph for what is a composition concern.

The port is deliberately narrow: the deleting request *disarms* the pod's
schedules and nothing more. Two wider readings were both wrong.

Doing the full teardown inline -- which is what ``ScheduleService`` does on the
pod-deleted event -- puts one Composio round trip per webhook schedule,
unbounded in number, inside the request's open transaction. Deleting the rows
inline instead is fast, but it leaves that event with no work list and strands
every provider trigger behind them forever.

Deactivating is the one thing that is both immediate and lossless: nothing in
the pod can fire the moment the request commits, and the rows are still there
for the event to tear down properly. See PS-OPS-020 and PS-POD-050, and
DEV-OPS-003 for why the event alone was not enough.
"""

from uuid import UUID

from app.modules.pod.domain.ports import PodScheduleTeardownPort
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository


class _PodScheduleTeardown:
    """The schedule module's disarm, seen through pod's port."""

    def __init__(self, uow: object):
        self._uow = uow

    async def disarm_all_for_pod(self, pod_id: UUID) -> int:
        return await ScheduleRepository(uow=self._uow).deactivate_all_by_pod(pod_id)


def create_pod_schedule_teardown(uow: object) -> PodScheduleTeardownPort:
    return _PodScheduleTeardown(uow)
