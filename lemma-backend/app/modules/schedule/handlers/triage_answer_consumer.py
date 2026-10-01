"""Settling a held event when its person answers the question -- or does not.

`agent_surfaces` announces how a schedule's question closed as
`NotificationClosedEvent`; this routes the event it held. On the surfaces
stream under its own group, declared in `schedule/module.py` so it misses
nothing published before its first read, and through the inbox, so a
redelivery is a no-op.

Every surface event passes through here, so anything that is not a schedule's
closed question is dropped before the inbox is touched.
"""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter
from pydantic import ValidationError

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
from app.core.log.log import get_logger
from app.modules.agent_surfaces.contracts import (
    SURFACE_EVENTS_STREAM,
    NotificationClosedEvent,
    NotificationOriginKind,
)
from app.modules.schedule.domain.triage import TriageAsk
from app.modules.schedule.services.triage_answers import ClosedQuestion, TriageAnswers

router = RedisRouter()
logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def closed_question(event: NotificationClosedEvent) -> ClosedQuestion | None:
    """The schedule's question in a closed notification, or None if it holds none."""
    if event.origin_kind is not NotificationOriginKind.SCHEDULE:
        return None
    try:
        ask = TriageAsk.model_validate(event.action or {})
    except ValidationError:
        # Only this module writes a schedule's question, so an unreadable one
        # is a bug worth seeing -- and nothing to retry.
        logger.warning(
            "schedule.triage_answer_consumer.unreadable_question.degraded",
            notification_id=str(event.notification_id),
            exc_info=True,
        )
        return None
    return ClosedQuestion(
        pod_id=event.pod_id,
        ask=ask,
        status=event.status.value,
        answer=event.answer,
        responder_user_id=event.responder_user_id,
    )


@reliable_redis_stream_subscriber(
    router,
    SURFACE_EVENTS_STREAM,
    group="schedule-triage-answers",
    consumer="schedule-triage-answers-consumer",
)
async def on_notification_closed(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    del fs_logger
    if event.get("event_type") != NotificationClosedEvent.get_event_type():
        return
    if event.get("origin_kind") != NotificationOriginKind.SCHEDULE.value:
        return

    async def settle() -> None:
        closed = closed_question(NotificationClosedEvent.model_validate(event))
        if closed is not None:
            await TriageAnswers(uow_factory).settle(closed)

    await inbox.process("schedule.triage_answers", event, settle)
