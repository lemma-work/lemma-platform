"""What a pod deletion does to the schedules inside the pod.

Replaces `app/composition/pod_schedules.py`. `pod` states what it needs as
`PodScheduleTeardownPort` in its own domain; this satisfies that port without
naming it -- a `Protocol` is checked structurally, so the arrow points inward
and neither module reaches the other's internals. Same shape as
`identity/contracts/organizations.py`, for the same reason.

Deliberately narrow: the deleting request *disarms* the pod's schedules and
nothing more. Two wider readings were both wrong. Doing the full teardown inline
-- what `ScheduleService` does on the pod-deleted event -- puts one Composio
round trip per webhook schedule, unbounded in number, inside the request's open
transaction. Deleting the rows inline is fast, but it leaves that event with no
work list and strands every provider trigger behind them forever.

Deactivating is the one thing that is both immediate and lossless: nothing in
the pod can fire the moment the request commits, and the rows are still there
for the event to tear down properly. See PS-OPS-020, PS-POD-050 and DEV-OPS-003.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
repository layer.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.schedule.repositories.schedule_repository import ScheduleRepository


class _PodScheduleTeardown:
    """The schedule module's disarm, seen through pod's port."""

    def __init__(self, uow: object):
        self._uow = uow

    async def disarm_all_for_pod(self, pod_id: UUID) -> int:
        return await ScheduleRepository(uow=self._uow).deactivate_all_by_pod(pod_id)


def create_pod_schedule_teardown(uow: object) -> _PodScheduleTeardown:
    """Satisfies a consumer's schedule-teardown port."""
    return _PodScheduleTeardown(uow)


__all__ = ["create_pod_schedule_teardown"]
