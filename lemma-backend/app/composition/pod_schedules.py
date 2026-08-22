"""Bind pod deletion to the schedule module's teardown.

Pod states what it needs as :class:`PodScheduleTeardownPort` in its own domain
and never names the schedule module; the binding lives here, with every other
cross-module wiring. Importing ``app.modules.schedule`` from ``app.modules.pod``
would put a forbidden edge -- and a ``datastore -> pod -> schedule`` cycle --
into the dependency graph for what is a composition concern.

See PS-OPS-020 and PS-POD-050 for why the teardown is in the deleting request
rather than left to the event-driven sweep.
"""

from app.modules.pod.domain.ports import PodScheduleTeardownPort
from app.modules.schedule.services.schedule_service import ScheduleService


def create_pod_schedule_teardown(uow: object) -> PodScheduleTeardownPort:
    """The schedule module's teardown, seen through pod's port."""
    return ScheduleService(uow=uow)
