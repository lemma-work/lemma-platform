"""Facts a pod's lifecycle announces, for the modules that react to it.

A module that has to do something when a pod is created, deleted or loses a
member subscribes a work kind to one of these at wiring time
(``facts.subscribe``); the pod transaction that made it true writes that work.
There is no stream between the two, so a reaction cannot be lost between a
commit and a consumer.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime,
and ``contracts/__init__`` is imported by anything that wants any pod contract.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, org_namespace, pod_namespace


class PodCreated(Fact):
    """A pod row was created. Its work runs in the pod's own namespace."""

    pod_id: UUID
    organization_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


class PodDeleted(Fact):
    """A pod was soft-deleted.

    Its cleanup runs under the organization, not the pod: the delete
    transaction drops the pod's own queued work, and the cleanup must not go
    with it.
    """

    pod_id: UUID
    organization_id: UUID
    deleted_by_user_id: UUID | None = None

    def work_namespace(self) -> str:
        return org_namespace(self.organization_id)


class PodMemberRemoved(Fact):
    """Someone stopped being a member of a pod."""

    pod_id: UUID
    user_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["PodCreated", "PodDeleted", "PodMemberRemoved"]
