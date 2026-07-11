"""Schedule notification handlers.

Consumes ``schedule_events`` (own consumer group) and, on a
``ScheduleDeactivated`` event from the failure circuit breaker, emails the
schedule's creator. This subscriber is the extension point for reacting to
deactivation — future consumers (in-app notification, admin alerting) add their
own subscriber without touching the breaker.
"""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleEvents,
)

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    ScheduleEvents.STREAM,
    group="schedule-notifications",
    consumer="schedule-notifications-consumer",
)
async def on_schedule_deactivated(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    """Email the creator when their schedule is auto-deactivated."""
    if event.get("event_type") != ScheduleDeactivated.get_event_type():
        return

    async def send_notification() -> None:
        from app.composition.identity_notifications import resolve_user_email
        from app.core.email.email_sender import EmailSender

        parsed = ScheduleDeactivated.model_validate(event)
        async with uow_factory() as uow:
            email = await resolve_user_email(uow, parsed.user_id)
        if email is None:
            fs_logger.warning(
                "ScheduleDeactivated for %s has no notification destination",
                parsed.schedule_id,
            )
            return

        await EmailSender.from_settings().send_email(
            to_email=email,
            subject="A scheduled automation was paused after repeated failures",
            html_content=(
                "<p>One of your scheduled automations was automatically paused "
                f"after {parsed.consecutive_failures} distinct failed runs.</p>"
                f"<p>Schedule ID: <code>{parsed.schedule_id}</code></p>"
                "<p>Re-enable it after addressing the cause.</p>"
            ),
        )
        fs_logger.info(
            "Sent deactivation notice for schedule %s",
            parsed.schedule_id,
        )

    await inbox.process("schedule-notifications", event, send_notification)
