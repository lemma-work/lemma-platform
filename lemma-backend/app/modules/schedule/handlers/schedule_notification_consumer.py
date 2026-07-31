"""Schedule notification handlers.

Consumes ``schedule_events`` (own consumer group) and, on a
``ScheduleDeactivated`` event from the failure circuit breaker, emails the
schedule's creator. This subscriber is the extension point for reacting to
deactivation — future consumers (in-app notification, admin alerting) add their
own subscriber without touching the breaker.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from app.core.config import settings
from app.core.email.transactional import (
    EmailAction,
    EmailDetail,
    RenderedEmail,
    render_transactional_email,
)
from app.core.helpers.humanize import humanize_name
from app.core.log.log import get_logger
from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleEvents,
)

router = RedisRouter()
logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(
        cast(async_sessionmaker[AsyncSession], async_session_maker)
    )


def render_schedule_paused_email(
    *,
    schedule_name: str | None,
    schedule_id: UUID,
    consecutive_failures: int,
    review_url: str,
) -> tuple[str, RenderedEmail]:
    display_name = humanize_name(schedule_name) if schedule_name else None
    display_name = display_name or f"Schedule {schedule_id}"
    rendered = render_transactional_email(
        preheader=f"{display_name} was paused after repeated failures.",
        eyebrow="Automation paused",
        heading=f"{display_name} needs attention.",
        body=(
            "Lemma automatically paused this scheduled automation after repeated "
            "failed runs.",
            "Review the underlying error, then re-enable the schedule when the "
            "cause has been addressed.",
        ),
        action=EmailAction("Review schedule", review_url),
        details=(
            EmailDetail("Schedule", display_name),
            EmailDetail("Consecutive failures", str(consecutive_failures)),
            EmailDetail("Schedule ID", str(schedule_id)),
        ),
        footer=(
            "You are receiving this because you created this scheduled automation.",
        ),
    )
    return f"{display_name} was paused after repeated failures", rendered


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
        from app.modules.schedule.repositories.schedule_repository import (
            ScheduleRepository,
        )

        parsed = ScheduleDeactivated.model_validate(event)
        async with uow_factory() as uow:
            email = await resolve_user_email(uow, parsed.user_id)
            schedule = await ScheduleRepository(uow=uow).get(parsed.schedule_id)
        if email is None:
            logger.debug(
                "schedule.schedule_notification_consumer.scheduledeactivated_s_has_no_notification.diagnostic",
                schedule_id=parsed.schedule_id,
            )
            return

        review_url = settings.frontend_url.rstrip("/")
        if schedule and schedule.pod_id:
            review_url = f"{review_url}/pod/{schedule.pod_id}/schedules"
        subject, rendered = render_schedule_paused_email(
            schedule_name=schedule.name if schedule else None,
            schedule_id=parsed.schedule_id,
            consecutive_failures=parsed.consecutive_failures,
            review_url=review_url,
        )

        await EmailSender.from_settings().send_email(
            to_email=email,
            subject=subject,
            html_content=rendered.html,
            text_content=rendered.text,
        )

    await inbox.process("schedule-notifications", event, send_notification)
