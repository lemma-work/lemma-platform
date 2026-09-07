"""Whether a pod is still live, for core's enumeration guard.

`app/core/authorization/pod_liveness.py` owns the rule -- a deleted pod stops
answering for its contents -- and this is the half only `mod:pod` can supply:
the row that says whether it was deleted.

Its own short unit of work, deliberately, matching what core did before: the
pod-scoped `UoWDep` commits precisely to hand its pooled connection back, and
reading on it would check one straight out again and hold a transaction open
through the handler.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory


async def pod_is_live(uow_factory: UnitOfWorkFactory, pod_id: UUID) -> bool:
    from app.modules.pod.infrastructure.models import Pod

    async with uow_factory() as uow:
        pod = await uow.session.get(Pod, pod_id)
        return pod is not None and not pod.is_deleted
