"""A deleted pod takes its links with it.

A link is a grant to ``POD:<id>`` in the pod that gave it, and a grant names its
grantee by id with no foreign key, so nothing else would remove the links a
deleted pod held. They could never be used -- a deleted pod runs nothing -- but
they would go on listing it in "Works with" and in the brief of every pod that
let it in.
"""

from __future__ import annotations

from faststream import Depends
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.agent.infrastructure.pod_link_queries import PodLinkQueries
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="agent-pod-links-pod-events",
    consumer="agent-pod-links-pod-events-consumer",
)
async def on_pod_deleted(
    event: dict[str, object],
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != PodDeletedEvent.get_event_type():
        return

    async def forget() -> None:
        parsed = PodDeletedEvent.model_validate(event)
        async with uow_factory() as uow:
            await PodLinkQueries(uow).forget_pod(pod_id=parsed.pod_id)
            await uow.commit()

    await inbox.process("agent.pod-links.pod-deletion", event, forget)
