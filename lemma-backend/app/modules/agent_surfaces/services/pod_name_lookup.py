"""The pod's name, for the readable half of an inbound address.

One place, because everything that mints an address needs it and none of them
should reach for ``PodRepository`` themselves. Keeping the lookup here leaves
``agent_surfaces`` holding a single import edge into the pod module instead of
one per call site.
"""

from __future__ import annotations

from uuid import UUID


async def pod_name_for(uow, pod_id: UUID) -> str | None:
    """``None`` for a pod that is gone — the caller falls back to a slug."""
    from app.modules.pod.infrastructure.pod_repositories import PodRepository

    pod = await PodRepository(uow).get(pod_id)
    return getattr(pod, "name", None)


__all__ = ["pod_name_for"]
