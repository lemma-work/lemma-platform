"""Telling somebody their allowance is running out, before it runs out.

`usage_events` carried `usage.model.recorded` and `usage.limit.denied` from the
day it existed and nothing ever subscribed to either, so the only signal a
person got about a spend limit was the 429 that refused their work. By then the
useful moment has passed: what an operator can act on is "you are at 80% with
three weeks of the month left", not "you may not run this".

One email per window per scope, because `UsageLimitApproachingEvent` is emitted
on the *crossing* rather than while above the line -- see the event's own
docstring. The inbox makes redelivery idempotent on top of that.

This subscriber is the extension point rather than the whole answer: an in-app
banner or an admin digest adds its own consumer without touching the emitter.
"""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.config import settings
from app.core.email.transactional import (
    EmailAction,
    EmailDetail,
    RenderedEmail,
    render_transactional_email,
)
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
from app.modules.usage.services.identity_lookups import identity_lookups
from app.modules.usage.domain.events import (
    USAGE_EVENTS_STREAM,
    UsageLimitApproachingEvent,
)

router = RedisRouter()
logger = get_logger(__name__)

#: What each window is called in a sentence somebody reads.
_SCOPE_NAMES = {
    "org_monthly": "organization's monthly",
    "user_weekly": "weekly",
    "user_monthly": "monthly",
}


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def render_limit_approaching_email(
    event: UsageLimitApproachingEvent,
) -> tuple[str, RenderedEmail]:
    """The warning, with the two facts that make it actionable.

    How much is left, and when it comes back. A percentage alone tells somebody
    they have a problem without telling them whether to wait for the month to
    turn or to ask for more headroom.
    """
    window = _SCOPE_NAMES.get(event.scope, event.scope)
    percent = round(event.threshold_fraction * 100)
    remaining = max(0.0, event.limit_usd - event.consumed_usd)
    heading = f"You have used over {percent}% of your {window} model allowance."
    rendered = render_transactional_email(
        preheader=f"{percent}% of the {window} allowance is gone.",
        eyebrow="Usage",
        heading=heading,
        body=(
            "Model work is charged against a spend allowance. Once it is used "
            "up, further work is refused until the allowance resets — nothing "
            "is silently downgraded or shortened.",
            "If this is expected, no action is needed. If it is not, this is "
            "the point at which it is still cheap to look.",
        ),
        action=EmailAction("Review usage", _usage_url(event)),
        details=(
            EmailDetail("Allowance", f"${event.limit_usd:,.2f}"),
            EmailDetail("Used", f"${event.consumed_usd:,.2f}"),
            EmailDetail("Remaining", f"${remaining:,.2f}"),
            EmailDetail("Resets", event.reset_at.strftime("%d %b %Y")),
        ),
        footer=("You are receiving this because this allowance applies to you.",),
    )
    return heading, rendered


def _usage_url(event: UsageLimitApproachingEvent) -> str:
    base = settings.frontend_url.rstrip("/")
    if event.organization_id is None:
        return base
    return f"{base}/organizations/{event.organization_id}/settings/usage"


@reliable_redis_stream_subscriber(
    router,
    USAGE_EVENTS_STREAM,
    group="usage-notifications",
    consumer="usage-notifications-consumer",
)
async def on_usage_limit_approaching(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    """Email the person a crossed allowance applies to."""
    del fs_logger
    if event.get("event_type") != UsageLimitApproachingEvent.get_event_type():
        return

    async def send_warning() -> None:
        from app.core.email.email_sender import EmailSender

        parsed = UsageLimitApproachingEvent.model_validate(event)
        async with uow_factory() as uow:
            email = await identity_lookups().resolve_user_email(uow, parsed.user_id)
        if email is None:
            logger.debug(
                "usage.limit_notification_consumer.no_address_for_warning.diagnostic",
                scope=parsed.scope,
            )
            return
        subject, rendered = render_limit_approaching_email(parsed)
        await EmailSender.from_settings().send_email(
            to_email=email,
            subject=subject,
            html_content=rendered.html,
            text_content=rendered.text,
        )

    await inbox.process("usage-notifications", event, send_warning)
