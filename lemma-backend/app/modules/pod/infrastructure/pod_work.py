"""The work a pod's lifecycle causes, written in the transaction that causes it.

The repositories hand every event an entity raised to ``stage_pod_work`` as
they collect it, so the work commits with the row change or not at all: a pod
is never created without its provisioning, never deleted without its cleanup.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, facts, pod_namespace
from app.core.log.log import get_logger
from app.modules.pod.contracts.lifecycle import (
    PodCreated,
    PodDeleted,
    PodMemberRemoved,
)
from app.modules.pod.domain.events import (
    PodCreatedEvent,
    PodDeletedEvent,
    PodJoinRequestedEvent,
    PodMemberRemovedEvent,
)
from app.modules.pod.events.join_request_work import add_join_request_email_work

logger = get_logger(__name__)


async def stage_pod_work(
    uow: SqlAlchemyUnitOfWork, events: Sequence[DomainEvent]
) -> None:
    for event in events:
        if isinstance(event, PodCreatedEvent):
            await facts.emit(
                uow,
                PodCreated(pod_id=event.pod_id, organization_id=event.organization_id),
            )
        elif isinstance(event, PodDeletedEvent):
            await _stage_pod_deleted(uow, event)
        elif isinstance(event, PodMemberRemovedEvent):
            await facts.emit(
                uow, PodMemberRemoved(pod_id=event.pod_id, user_id=event.user_id)
            )
        elif isinstance(event, PodJoinRequestedEvent):
            await add_join_request_email_work(uow, event)


async def _stage_pod_deleted(uow: SqlAlchemyUnitOfWork, event: PodDeletedEvent) -> None:
    # Drop first, then emit: the cleanup is written under the organization, so
    # dropping the pod's namespace cannot take it, but a pod-scoped item written
    # by an earlier statement of this same transaction would go with the rest --
    # which is right, it was waiting on a pod that no longer exists.
    dropped = await WorkWriter.of(uow).drop_queued(pod_namespace(event.pod_id))
    await facts.emit(
        uow,
        PodDeleted(
            pod_id=event.pod_id,
            organization_id=event.organization_id,
            deleted_by_user_id=event.deleted_by_user_id,
        ),
    )
    if dropped:
        logger.info(
            "pod.work.dropped_with_pod",
            pod_id=str(event.pod_id),
            count=dropped,
        )
