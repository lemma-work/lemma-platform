"""The pod's name, for the readable half of an inbound address.

One place, because everything that mints an address needs it and none of them
should reach into the pod module themselves.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.pod.contracts.members import pod_name


async def pod_name_for(uow, pod_id: UUID) -> str | None:
    """``None`` for a pod that is gone — the caller falls back to a slug."""
    return await pod_name(uow.session, pod_id)


__all__ = ["pod_name_for"]
