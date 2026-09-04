"""Schedule notification handlers.

Consumes ``schedule_events`` (own consumer group) and, on a
``ScheduleDeactivated`` event from the failure circuit breaker, emails the
schedule's creator. This subscriber is the extension point for reacting to
deactivation — future consumers (in-app notification, admin alerting) add their
own subscriber without touching the breaker.
"""

from __future__ import annotations

from uuid import UUID

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
    return SessionUnitOfWorkFactory(async_session_maker)


def render_schedule_paused_email(
    *,
    schedule_name: str | None,
    schedule_id: UUID,
    consecutive_failures: int,
    review_url: str,
    reason: str = "consecutive_failures",
) -> tuple[str, RenderedEmail]:
    display_name = humanize_name(schedule_name) if schedule_name else None
    display_name = display_name or f"Schedule {schedule_id}"
    copy = {
        "SCHEDULE_TOO_FREQUENT": (
            "was paused because it runs too frequently",
            "Lemma paused this scheduled automation because its recurring frequency exceeds the configured minimum interval.",
        ),
        "SCHEDULE_ONE_TIME_MISSED": (
            "was paused because its one-time execution was missed",
            "Lemma paused this one-time automation because its scheduled timestamp has already passed.",
        ),
        "SCHEDULE_REJECTED": (
            "was paused because the scheduler rejected it",
            "Lemma paused this scheduled automation because the scheduler could not accept its configuration.",
        ),
        "SCHEDULE_VALIDATION_ERROR": (
            "was paused because its configuration is invalid",
            "Lemma paused this scheduled automation because its time configuration is invalid.",
        ),
    }.get(
        reason,
        (
            "was paused after repeated failures",
            "Lemma automatically paused this scheduled automation after repeated failed runs.",
        ),
    )
    summary, explanation = copy
    details = [
        EmailDetail("Schedule", display_name),
        EmailDetail("Schedule ID", str(schedule_id)),
    ]
    if reason == "consecutive_failures":
        details.insert(
            1, EmailDetail("Consecutive failures", str(consecutive_failures))
        )
    rendered = render_transactional_email(
        preheader=f"{display_name} {summary}.",
        eyebrow="Automation paused",
        heading=f"{display_name} needs attention.",
        body=(
            explanation,
            "Review the underlying error, then re-enable the schedule when the "
            "cause has been addressed.",
        ),
        action=EmailAction("Review schedule", review_url),
        details=tuple(details),
        footer=(
            "You are receiving this because you created this scheduled automation.",
        ),
    )
    return f"{display_name} {summary}", rendered


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
        from app.core.email.email_sender import EmailSender
        from app.modules.identity.contracts.profiles import user_profile
        from app.modules.schedule.repositories.schedule_repository import (
            ScheduleRepository,
        )

        parsed = ScheduleDeactivated.model_validate(event)
        async with uow_factory() as uow:
            owner = await user_profile(uow.session, parsed.user_id)
            schedule = await ScheduleRepository(uow=uow).get(parsed.schedule_id)
        email = owner.email if owner else None
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
            reason=parsed.reason,
        )

        await EmailSender.from_settings().send_email(
            to_email=email,
            subject=subject,
            html_content=rendered.html,
            text_content=rendered.text,
        )

    await inbox.process("schedule-notifications", event, send_notification)
