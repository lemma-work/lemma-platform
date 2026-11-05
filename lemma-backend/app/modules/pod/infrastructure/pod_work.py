"""The work a pod's lifecycle causes, written in the transaction that causes it.

The repositories hand every event an entity raised to ``stage_pod_work`` as
they collect it, so the work commits with the row change or not at all: a pod
is never created without its provisioning, never deleted without its cleanup.

A pod made or deleted, or a member added or taken out, also changes which pods
the organization shows someone: once the transaction commits, the
organization's live channel says so (``pods``), and the ``org`` topic decides
again which pods each of its readers covers.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, facts, pod_namespace
from app.core.live.signals import publish_org_signal
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
    PodMemberAddedEvent,
    PodMemberRemovedEvent,
)
from app.modules.pod.events.join_request_work import add_join_request_email_work
from app.modules.pod.infrastructure.models.pod_models import Pod

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
            _tell_organization(uow, event.organization_id)
        elif isinstance(event, PodDeletedEvent):
            await _stage_pod_deleted(uow, event)
            _tell_organization(uow, event.organization_id)
        elif isinstance(event, PodMemberRemovedEvent):
            await facts.emit(
                uow, PodMemberRemoved(pod_id=event.pod_id, user_id=event.user_id)
            )
            await _tell_organization_of_pod(uow, event.pod_id)
        elif isinstance(event, PodMemberAddedEvent):
            await _tell_organization_of_pod(uow, event.pod_id)
        elif isinstance(event, PodJoinRequestedEvent):
            await add_join_request_email_work(uow, event)


async def _tell_organization_of_pod(uow: SqlAlchemyUnitOfWork, pod_id: UUID) -> None:
    pod = await uow.session.get(Pod, pod_id)
    if pod is not None:
        _tell_organization(uow, pod.organization_id)


def _tell_organization(uow: SqlAlchemyUnitOfWork, organization_id: UUID) -> None:
    """Once this commits, the organization's ``org`` readers look again at
    which pods they cover: a hint, and a lost one is caught by their timer."""

    async def announce() -> None:
        publish_org_signal(organization_id, {"k": "pods"})

    uow.after_commit(announce)


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
