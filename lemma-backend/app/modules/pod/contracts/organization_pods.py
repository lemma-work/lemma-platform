"""Deleting every pod an organization holds, for identity, when the organization goes.

An organization goes with the account of its only member. Its pods are deleted
through ``PodService.delete_pod`` one at a time rather than by deleting rows:
that is the path that disarms the pod's schedules and frees its addresses in
the same transaction, and raises the ``PodDeleted`` fact whose cleanup tears
down what the pod held outside the database. A cascade from the organization
row would do none of it, and would take the rows that cleanup reads.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.infrastructure.pod_repositories import PodRepository

#: Pods read per statement. Each page is deleted before the next is read, so
#: the cursor only ever moves past pods that are already gone.
_PAGE = 100


async def delete_organization_pods(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID, requester_user_id: UUID
) -> list[UUID]:
    """Delete every live pod in the organization, as ``requester_user_id``.

    The requester must be allowed to delete each pod -- an owner of the
    organization is -- or the pod's own refusal propagates.
    """
    from app.modules.pod.api.dependencies import get_pod_service

    pods = PodRepository(uow)
    service = get_pod_service(uow)
    deleted: list[UUID] = []
    cursor: str | None = None
    while True:
        page, cursor = await pods.list_by_org(organization_id, _PAGE, cursor)
        for pod in page:
            await service.delete_pod(pod.id, requester_user_id)
            deleted.append(pod.id)
        if cursor is None:
            return deleted


__all__ = ["delete_organization_pods"]
