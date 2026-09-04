"""Restarting a conversation that has stopped waiting on people.

Its own router rather than a handler on `events/handlers.py`, which sits at the
600-line ceiling: the registry takes a sequence of event routers, so a stream
this module consumes for one reason can be its own file.
"""

from __future__ import annotations

from faststream import Depends, Logger
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
from app.modules.agent.services.message_reply_service import MessageReplyService
from app.modules.agent_surfaces.contracts import (
    SURFACE_EVENTS_STREAM,
    NotificationSettledEvent,
)

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    SURFACE_EVENTS_STREAM,
    group="agent-notification-settled",
    consumer="agent-notification-settled-consumer",
)
async def on_notification_settled(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    """Start the next turn of a conversation that has stopped waiting on people.

    ``message_user`` does not pause the asker -- it sends and the turn ends --
    so without this an answer sits on its row and nothing ever reads it. The
    conversation is not waiting in any technical sense; it is simply over, and
    this starts it again.

    Through the inbox because replaying a turn twice is expensive and visible to
    whoever is watching the conversation. On the stream rather than as a direct
    call, which is what this replaced: that call swallowed every failure into a
    log line, so an answer the asker's side could not be started for was lost
    with only a warning to say so. A failure here is redelivered.
    """
    del fs_logger
    if event.get("event_type") != NotificationSettledEvent.get_event_type():
        return

    async def process() -> None:
        parsed = NotificationSettledEvent.model_validate(event)
        async with uow_factory() as uow:
            await MessageReplyService(uow).deliver(
                conversation_id=parsed.conversation_id,
                pod_id=parsed.pod_id,
            )
            await uow.commit()

    await inbox.process("agent.notification_settled", event, process)
