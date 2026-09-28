"""Whether a pod is still live, for core's enumeration guard and for cleanup.

`app/core/authorization/pod_liveness.py` owns the rule -- a deleted pod stops
answering for its contents -- and this is the half only `mod:pod` can supply:
the row that says whether it was deleted.

Its own short unit of work, deliberately, matching what core did before: the
pod-scoped `UoWDep` commits precisely to hand its pooled connection back, and
reading on it would check one straight out again and hold a transaction open
through the handler.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory


async def pod_is_live(uow_factory: UnitOfWorkFactory, pod_id: UUID) -> bool:
    from app.modules.pod.infrastructure.models import Pod

    async with uow_factory() as uow:
        pod = await uow.session.get(Pod, pod_id)
        return pod is not None and not pod.is_deleted


async def pods_gone_since(
    uow_factory: UnitOfWorkFactory,
    pod_ids: Collection[UUID],
    *,
    deleted_before: datetime,
) -> set[UUID]:
    """Which of ``pod_ids`` are gone: no row, or deleted before the cutoff.

    For reclaiming what a pod left in stores this database does not own. A pod
    that is live, or was deleted at or after ``deleted_before``, is never in
    the answer. Deletion is a soft delete that nothing updates afterwards, so a
    deleted row's ``updated_at`` is when it was deleted.
    """
    from sqlalchemy import or_, select

    from app.modules.pod.infrastructure.models import Pod

    if not pod_ids:
        return set()
    async with uow_factory() as uow:
        retained = await uow.session.scalars(
            select(Pod.id).where(
                Pod.id.in_(list(pod_ids)),
                or_(Pod.is_deleted.is_(False), Pod.updated_at >= deleted_before),
            )
        )
        return set(pod_ids) - set(retained)
