"""``schedule.paused_notice``: email the owner that the platform paused a schedule."""

from __future__ import annotations

from app.core.infrastructure.work import JobContext
from app.modules.schedule.work.kinds import PausedNotice


async def run_paused_notice(ctx: JobContext, payload: PausedNotice) -> None:
    from urllib.parse import quote

    from app.core.config import settings
    from app.core.email.email_sender import EmailSender
    from app.modules.identity.contracts.profiles import user_profile
    from app.modules.schedule.repositories.schedule_repository import (
        ScheduleRepository,
    )
    from app.modules.schedule.services.paused_email import (
        render_schedule_paused_email,
    )

    async with ctx.checkpoint() as uow:
        owner = await user_profile(uow.session, payload.user_id)
        schedule = await ScheduleRepository(uow=uow).get(payload.schedule_id)
    email = owner.email if owner else None
    if email is None:
        return
    review_url = settings.frontend_url.rstrip("/")
    if schedule and schedule.pod_id:
        review_url = (
            f"{review_url}/t/{quote(str(schedule.pod_id), safe='')}"
            "/about?section=schedules"
        )
    subject, rendered = render_schedule_paused_email(
        schedule_name=schedule.name if schedule else None,
        schedule_id=payload.schedule_id,
        consecutive_failures=payload.consecutive_failures,
        review_url=review_url,
        reason=payload.reason,
    )
    await EmailSender.from_settings().send_email(
        to_email=email,
        subject=subject,
        html_content=rendered.html,
        text_content=rendered.text,
    )
